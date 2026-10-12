"""从当前用户原话提取明确绑定到一份演示的总页数，不推断跨工件约束。"""
from __future__ import annotations

import re


_NUMBER = r"(?:[0-9]{1,2}|[一二两三四五六七八九十]{1,3})"
_PAGES = re.compile(
    rf"(?<![0-9一二两三四五六七八九十百])(?P<lower>{_NUMBER})\s*"
    rf"(?:(?:页\s*)?(?:到|至|[-—–－~～])\s*(?P<upper>{_NUMBER})\s*)?页"
)
_DECK_WORDS = re.compile(r"(?:pptx?|powerpoint|幻灯片|演示文稿|演示|介绍|汇报|报告|概览|方案|培训)", re.I)


def _number(value: str) -> int | None:
    """只识别本接口 3–50 页范围内常见的阿拉伯数字和中文整数。"""
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    parts = value.split("十")
    if len(parts) == 2 and (not parts[0] or parts[0] in digits) and (not parts[1] or parts[1] in digits):
        return (digits.get(parts[0], 1) * 10) + digits.get(parts[1], 0)
    return None


def _matches_title(scope: str, title: str) -> bool:
    """要求标题原文或明确产品名同现；通用“介绍/PPT”词不构成工件身份。"""
    scope = re.sub(r"\s+", "", scope).lower()
    title = re.sub(r"\s+", "", title).lower().removesuffix(".pptx")
    if not title or not _DECK_WORDS.search(scope):
        return False
    # 合并对象、代词或分别计数的范围没有独立身份，留给模型显式提供 page_count。
    if re.search(r"(?:以及|分别|每份|各自|两份|两张|两个|同时|另外|再做|和|与|、)", scope):
        return False
    if len(title) >= 4 and title in scope:
        return True
    ignored = {"ppt", "pptx", "powerpoint", "presentation", "report", "introduction"}
    for token in re.findall(r"[a-z][a-z0-9]{1,}", title):
        if token in ignored:
            continue
        qualifier = re.sub(r"(?:系统|通用|产品|介绍|概览|演示文稿|演示|pptx?)", "", title, flags=re.I)
        qualifier = re.sub(re.escape(token), "", qualifier)
        qualifier = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", qualifier)
        if qualifier and qualifier not in scope:
            # 同为 ERP 的实施方案与通用介绍是不同文件，不能仅靠产品缩写绑定。
            continue
        # ERP 系统介绍与“ERP介绍”可对齐；仅被提及的 ERP 引用不自动获得页数约束。
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])(?:系统|软件|平台|产品)?(?:介绍|概览|演示|ppt|说明)", scope):
            return True
    identity = re.sub(r"(?:系统|通用|产品|数据|分析|介绍|概览|报告|演示文稿|演示|pptx?)", "", title, flags=re.I)
    return len(identity) >= 4 and identity in scope


def infer_page_constraint(request: str, title: str, *, natural_page_count: int) -> dict | None:
    """提取明确分句中的总页数；冲突、正文页数、模糊上下限和跨对象范围均不猜测。"""
    matches = []
    for sentence in re.split(r"[。！？!?；;\n]+", str(request or "")):
        for match in _PAGES.finditer(sentence):
            prefix = sentence[:match.start()]
            immediate = prefix[-10:]
            if re.search(r"正文|至少|至多|最多|最少|不超过|超过|大约|约|第|前|后", immediate):
                continue
            if re.match(r"\s*(?:以上|以下|左右)", sentence[match.end():]):
                continue
            clauses = [part.strip() for part in re.split(r"[，,:：]", prefix) if part.strip()]
            if not clauses:
                continue
            scope = clauses[-1]
            # “控制在/总页数为”等仅修饰数量的短语可回溯到紧邻的明确对象分句。
            if re.fullmatch(r"(?:控制在|总页数为|总页数|页数为|页数|做成|做|为|共|共计|一共|总共)?", scope) and len(clauses) > 1:
                scope = clauses[-2]
            if not _matches_title(scope, title):
                continue
            lower = _number(match.group("lower"))
            upper = _number(match.group("upper")) if match.group("upper") else lower
            if lower is None or upper is None or not 3 <= lower <= upper <= 50:
                continue
            matches.append((lower, upper, scope))
    bounds = {(lower, upper) for lower, upper, _ in matches}
    if len(bounds) != 1:
        return None
    lower, upper = next(iter(bounds))
    return {"source": "current_user_request", "minimum": lower, "maximum": upper,
            "selected": max(lower, min(upper, natural_page_count)), "scope": matches[0][2],
            "includes_cover_and_closing": True}
