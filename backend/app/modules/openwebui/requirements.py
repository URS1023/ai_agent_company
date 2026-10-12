"""从用户的明确制作要求提取交付清单，不执行工具或推断业务数据。"""
from __future__ import annotations

import re
import unicodedata


_ARTIFACT = re.compile(
    r"(?P<docx>(?<![a-z])(?:word|docx)(?![a-z]))"
    r"|(?P<xlsx>(?<![a-z])(?:excel|xlsx)(?![a-z]))"
    r"|(?P<pptx>(?<![a-z])(?:powerpoint|pptx?)(?![a-z])|幻灯片|演示文稿)"
    r"|(?P<pdf>(?<![a-z])pdf(?![a-z]))"
    r"|(?P<dashboard>数字大屏|大屏|看板|(?<![a-z])dashboard(?![a-z]))"
    r"|(?P<pie>饼图|图饼|环形图|圆环图|(?<![a-z])(?:pie|doughnut|donut)\s+chart(?![a-z]))"
    r"|(?P<bar>柱状图|柱形图|条形图|(?<![a-z])bar\s+chart(?![a-z]))"
    r"|(?P<line>折线图|(?<![a-z])line\s+chart(?![a-z]))"
    r"|(?P<chart>图表|统计图)", re.I,
)
_ACTION = re.compile(
    r"生成|制作|导出|创建|转换(?:为|成)?|转成|转为|做(?:成)?|绘制|画"
    r"|输出|提供|交付|给我|给我们|发给我|发我|保存为|另存为|放到|放入|嵌入|插入"
    r"|需要|想要|只要|还要|也要|(?<!不)要"
    r"|\b(?:create|generate|export|make|give\s+me|provide|draw|save\s+as)\b", re.I,
)
_NEGATIVE = re.compile(
    r"不需要|不要|不用|无需|不必|暂不|停止|取消|别|不(?=生成|制作|导出|做|画|要)"
    r"|\b(?:do\s+not|don't|no\s+need\s+to|stop|cancel)\b", re.I,
)
_NUMBER = r"[0-9零〇一二两三四五六七八九十百千]+"
_PAGES = re.compile(rf"(?P<number>{_NUMBER})\s*(?:页|[- ]*pages?\b)", re.I)
_CHART_TYPES = {"pie", "bar", "line"}


def _integer(text: str) -> int | None:
    """转换常用中英文页数；拒绝不规范或无意义的零页要求。"""
    if len(text) > 12:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    digits = {character: value for value, character in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    if all(character in digits for character in text):
        value = int("".join(str(digits[character]) for character in text))
        return value if value > 0 else None
    units = {"十": 10, "百": 100, "千": 1000}
    total, current, previous_unit = 0, 0, 10000
    for character in text:
        if character in digits:
            current = digits[character]
        elif character in units and units[character] < previous_unit:
            previous_unit = units[character]
            total += (current or 1) * previous_unit
            current = 0
        else:
            return None
    return total + current or None


def _consultation(clause: str) -> bool:
    """区分询问操作方法、能力和礼貌的实际委托。"""
    if re.search(r"怎么|如何|为什么|怎样|\bhow\s+(?:to|can|do)\b", clause, re.I):
        return True
    if re.match(r"\s*(?:请问|请|我想|想要|想)?\s*(?:了解|学习|解释|讲解|介绍|教我)", clause):
        return True
    if re.search(r"(?:的方法|的步骤|的教程|有什么区别|有何区别)", clause):
        return True
    if re.match(r"\s*(?:如果|假如|假设|假定|比如|例如|假使|if\b)", clause, re.I):
        return True
    delegated = re.search(r"帮(?:我|我们)|为(?:我|我们)|替(?:我|我们)|给(?:我|我们)|发我|\b(?:for\s+me|give\s+me)\b", clause, re.I)
    capability = re.search(r"是否|能否|可否|能不能|可不可以|(?:能|可以|支持).*(?:吗|么|\?)|\b(?:can|could)\s+(?:you|it)\b", clause, re.I)
    return bool(capability and not delegated)


def _page_count(clause: str, artifact: re.Match, artifacts: list[re.Match]) -> int | None:
    """仅采用紧邻 PPT 的确定页数，避免把 Word 页数或范围上限混入。"""
    index = artifacts.index(artifact)
    start = artifacts[index - 1].end() if index else 0
    end = artifacts[index + 1].start() if index + 1 < len(artifacts) else len(clause)
    before = clause[start:artifact.start()]
    after = clause[artifact.end():end]
    candidates = list(_PAGES.finditer(before))[-1:] or list(_PAGES.finditer(after))[:1]
    if not candidates:
        return None
    candidate = candidates[0]
    fragment = before if _PAGES.search(before) else after
    prefix = fragment[:candidate.start()]
    suffix = fragment[candidate.end():]
    if re.search(r"(?:到|至|[-~～]|大约|约|至少|最多|不超过|不少于)\s*$", prefix):
        return None
    if re.match(r"\s*(?:到|至|[-~～])", suffix):
        return None
    return _integer(candidate.group("number"))


def requested_outputs(text: str) -> dict:
    """提取附件及图类要求；交付核验使用此清单，工具授权仍由运行时独立控制。"""
    text = unicodedata.normalize("NFKC", text or "")
    required: dict = {}
    chart_types: list[str] = []
    inherited_request = False
    previous_output: str | None = None
    positive_parts: list[str] = []
    # 先按语句和转折切分，否定只覆盖自己的对象，逗号后的省略列表可继承制作意图。
    clauses = re.split(r"[，,。；;\n!?！？]|但是|不过|而是|然后|接着|但(?!是)", text)
    for clause in clauses:
        if not clause.strip():
            continue
        if _consultation(clause):
            inherited_request = False
            continue
        artifacts = list(_ARTIFACT.finditer(clause))
        # 后续省略主语的“要十五页”沿用紧邻的 PPT，不覆盖其他文档的页数。
        if not artifacts and inherited_request and previous_output == "pptx":
            pages = _PAGES.search(clause)
            if pages and re.fullmatch(r"\s*(?:共|总共|需要|要|做成|改成|改为)?\s*", clause[:pages.start()]):
                count = _integer(pages.group("number"))
                if count:
                    required["pptx"] = count
        events = [(match.start(), 2, "artifact", match) for match in artifacts]
        events.extend((match.start(), 1, "action", match) for match in _ACTION.finditer(clause))
        events.extend((match.start(), 0, "negative", match) for match in _NEGATIVE.finditer(clause))
        events.sort(key=lambda event: event[:2])
        mode = inherited_request
        negative_end: int | None = None
        negative_pending = False
        negative_ranges = [(match.start(), match.end()) for match in _NEGATIVE.finditer(clause)]
        accepted = False
        for position, _, kind, match in events:
            if kind == "negative":
                mode = False
                negative_end = match.end()
                negative_pending = True
                continue
            if kind == "action":
                # “不要生成”是一条否定，不允许内部的“生成”重新授权。
                if any(start <= position < end for start, end in negative_ranges):
                    continue
                gap = clause[negative_end:position] if negative_end is not None else ""
                if negative_pending and re.fullmatch(r"(?:\s|再|继续|帮我|为我|替我|请|去|进行|自动|现在|立即)*", gap):
                    continue
                mode = True
                negative_pending = False
                negative_end = None
                continue
            output = match.lastgroup
            negative_pending = False
            following = clause[match.end():]
            # “Excel 也不需要了”采用后置否定；“Word 不要封面”不否定 Word 本身。
            denied_after = re.match(
                r"\s*(?:也|都|暂时|先)?\s*(?:不需要|不要|不用|无需|取消)"
                r"\s*(?:生成|制作|导出|做)?\s*(?:了)?\s*$", following,
            )
            if output in _CHART_TYPES:
                if mode and not denied_after:
                    if output not in chart_types:
                        chart_types.append(output)
                    required["chart"] = True
                    accepted = True
                else:
                    chart_types = [item for item in chart_types if item != output]
                continue
            if not mode or denied_after:
                if negative_end is not None or denied_after:
                    required.pop(output, None)
                    if output == "chart":
                        chart_types.clear()
                continue
            required[output] = (_page_count(clause, match, artifacts) or required.get("pptx", True)) if output == "pptx" else True
            previous_output = output
            accepted = True
        if accepted:
            positive_parts.append(clause)
        inherited_request = mode
    if chart_types:
        required["chart_types"] = chart_types
    elif "chart" in required and not any(
        match.lastgroup == "chart" for clause in positive_parts for match in _ARTIFACT.finditer(clause)
    ):
        required.pop("chart", None)
    positive = "，".join(positive_parts)
    if required.get("docx") and required.get("dashboard") and re.search(r"放到|放入|嵌入|插入|里面", positive):
        required["embedded_dashboard"] = True
    return required
