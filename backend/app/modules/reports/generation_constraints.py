"""Recognize explicit widget counts without treating metric limits as layouts."""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.errors import ValidationError


@dataclass(frozen=True)
class ComponentCount:
    """用户明确指定的组件数量约束（不可变值对象，可直接放进集合去重）。

    ``scope="all"`` 表示文字与装饰也计入总数，``scope="data"`` 只统计带真实查询的组件；
    两者不可混用——同一需求出现多个互不相同的约束时由 ``explicit_component_count`` 报错。
    """

    count: int
    scope: str

    def as_dict(self) -> dict:
        """转成可写入请求追溯与模型上下文的普通字典（``componentCountConstraint``）。"""
        return {"count": self.count, "scope": self.scope}


_DIGITS = {char: index for index, char in enumerate("零一二三四五六七八九")}
_DIGITS["两"] = 2
_ENGLISH = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))
_NUMBER = r"[0-9零一二两三四五六七八九十百]+"
_ACTION = r"生成|创建|保留|包含|使用|显示|展示"
_PATTERNS = (
    rf"(?:(?:只|仅)(?:需(?:要)?|要|想)?(?:{_ACTION})|总共|一共|恰好)[^。；;\n!?！？]{{0,120}}?(?P<count>{_NUMBER})\s*(?:个|项)\s*(?P<data>数据)?组件",
    rf"(?:只(?:要|需(?:要)?)|仅(?:需(?:要)?)?)\s*(?P<count>{_NUMBER})\s*(?:个|项)\s*(?P<data>数据)?组件",
    rf"(?:生成|创建|保留|包含)\s*(?P<count>{_NUMBER})\s*(?:个|项)\s*(?P<data>数据)?组件",
    r"\b(?:only|exactly)\s+(?:(?:generate|create|keep|include)\s+)?(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?P<data>data\s+)?(?:widgets?|components?)\b",
)


def _number(value: str) -> int | None:
    """把中文/英文/阿拉伯数字写法解析成整数；无法确定含义时返回 None。

    只覆盖 1–100 的常用写法（含"十/二十/一百"）。超出表达能力的写法宁可返回 None 也不猜，
    因为误判会把"数量约束"错误地强加给生成结果。
    """

    if value.isascii() and value.isdigit():
        return int(value)
    if value.lower() in _ENGLISH:
        return _ENGLISH[value.lower()]
    if value in _DIGITS:
        return _DIGITS[value]
    if value in {"一百", "百"}:
        return 100
    if re.fullmatch(r"[一二两三四五六七八九]?十[一二三四五六七八九]?", value):
        tens, units = value.split("十")
        return _DIGITS.get(tens, 1) * 10 + _DIGITS.get(units, 0)
    return None


def explicit_component_count(requirement: str) -> ComponentCount | None:
    """Only unambiguous exact counts; ranges and per-group counts stay semantic.

    从需求里识别"只要 3 个组件""仅需 2 个数据组件"这类**明确**约束：先在同一分句内排除
    "最多/至少/大约/每个/不接受"等模糊或否定表达，也排除区间写法（3 到 5 个）与"上次/以前"
    等历史指代；解析出的数量必须在 1–100 内。

    无约束返回 None；出现互相冲突的多个约束或数量越界时抛 ``ValidationError``，要求用户
    明确本次总数，而不是替用户挑一个。指标数量（如"TOP 10"）不会被当成布局数量，
    因为模式只匹配"…个/项 [数据]组件"。
    """
    constraints = set()
    for pattern in _PATTERNS:
        for match in re.finditer(pattern, requirement, re.I):
            clause_start = max(requirement.rfind(separator, 0, match.start()) for separator in "。；;，,\n!?！？") + 1
            clause = requirement[clause_start:match.end()]
            if (re.search(r"最多|至少|不超过|不少于|大约|每[个种类组]|at most|at least|per\s", clause, re.I)
                    or re.search(r"(?:不要|不需要|不必|不要求|不应|不能|不是|并非|don't|do not)(?:只|仅)?\s*$", requirement[clause_start:match.start()], re.I)
                    or re.search(r"(?:上次|原来|之前|以前|曾经|已经)(?:只|仅)?\s*$", requirement[clause_start:match.start()])
                    or re.search(rf"{_NUMBER}\s*(?:到|至|~|-|或|、)\s*{_NUMBER}\s*(?:个|项)\s*(?:数据)?组件", clause)):
                continue
            count = _number(match.group("count"))
            if count is None:
                continue
            if not 1 <= count <= 100:
                raise ValidationError("单次大屏生成支持 1 至 100 个组件，请调整明确指定的组件数量")
            constraints.add(ComponentCount(count, "data" if match.group("data") else "all"))
    if len(constraints) > 1:
        raise ValidationError("需求中存在多个组件数量或统计范围，请明确本次组件总数，或仅指定数据组件数量")
    return next(iter(constraints), None)
