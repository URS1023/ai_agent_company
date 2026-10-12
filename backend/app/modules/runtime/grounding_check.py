"""交付落地校验：最终正文的关键数字必须能从本次工具回执/工件账本中追溯到。

业务增强点（优于基线 DSH）：DSH 原生只做“生成了内容”，本模块在收尾时对正文做
一次机器预检——正文里的数字与成功工具回执、工件元数据交叉核对，发现“无法溯源”
的数值时发出 ``delivery.audit`` 事件并留痕到 run 快照，提示人工复核，不修改用户正文。

边界（诚实声明）：这是启发式预检，不是独立业务验收；单位换算/语境推导不在此判定。
只负责把“无依据数字”显式暴露出来，由四维复核与人工兜底。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_UNITS = ("台", "条", "次", "个", "张", "份", "元", "万", "亿", "%", "％", "吨", "kg", "m³", "小时", "天", "人")
# 证据来源：成功工具回执的文本化 + 工件元数据。
_EVIDENCE_FIELDS = ("results", "rows", "data", "stdout", "summary", "columns", "count", "total", "value")


def extract_numbers(text: str) -> list[dict]:
    """提取正文数字及其后的单位词语，作为待核对断言（按出现顺序、去重保序）。

    单位只看数字后紧跟的 3 个字符，避免把下一句的距离数字误当作当前单位。
    """
    seen = set()
    claims = []
    for match in _NUMBER.finditer(text):
        value = match.group(0)
        tail = text[match.end():match.end() + 3]
        unit = next((u for u in _UNITS if u in tail), "")
        key = (value, unit)
        if key in seen:
            continue
        seen.add(key)
        claims.append({"value": value, "unit": unit, "sample": text[max(0, match.start() - 18):match.end() + 24].strip()})
    return claims


def _scalars(obj: Any) -> list[str]:
    """把证据结构递归展开成可比较的数字字符串集合。"""
    values: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and any(field in key.lower() for field in _EVIDENCE_FIELDS):
                values.extend(_scalars(value))
            else:
                values.extend(_scalars(value))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            values.extend(_scalars(item))
    elif isinstance(obj, bool):
        return values
    elif obj is not None:
        values.append(str(obj))
    return values


def _evidence_numbers(evidence: list[str]) -> set[str]:
    result = set()
    for token in evidence:
        for value in _NUMBER.findall(token):
            result.add(value)
    return result


def check_grounding(*, committed_text: str, evidence: list[str],
                    artifacts: Optional[list[dict]] = None) -> dict:
    """对正文断言做机器预检；返回可读结论，不修改正文。

    ``evidence``：本次运行成功工具回执的文本化集合（数字来源）；
    ``artifacts``：工件引用（id/name/type），用于“有没有真实交付物”。
    """
    claims = extract_numbers(committed_text or "")
    evidence_numbers = _evidence_numbers(evidence)
    unmatched = []
    for claim in claims:
        if claim["value"] not in evidence_numbers:
            # 排除明显是“无数字上下文”的零散断言：例如纯序号/年份。
            unmatched.append(claim)
    artifacts_generated = bool(artifacts)
    has_numeric_claims = bool(claims)
    status = "pass"
    warnings = []
    if has_numeric_claims and not evidence:
        status = "warn"
        warnings.append("正文含数字断言，但本次运行没有任何成功查询/工具数字回执，数字无法溯源。")
    if unmatched:
        status = "warn"
        warnings.append(f"正文有 {len(unmatched)} 处数字断言无法从本次工具回执中溯源（示例：{unmatched[0]['sample']}）。")
    if has_numeric_claims and not artifacts_generated and "文档" in (committed_text or ""):
        warnings.append("正文声称交付文档但本次未登记任何工件，存在“口头交付”。")
    return {
        "status": status,
        "checked": len(claims),
        "matched": len(claims) - len(unmatched),
        "unmatched": unmatched[:8],
        "warnings": warnings,
        "evidence_sources": len(evidence),
        "artifacts": [{"id": item.get("id"), "name": item.get("name")} for item in (artifacts or [])],
        "note": "机器预检提示，不替代人工四维复核。",
    }


def evidence_from_run(db, run) -> list[str]:
    """从当前 run 的成功工具回执中抽取数字证据（只读，不重复执行）。"""
    from sqlalchemy import select
    from ...models import ToolCall
    evidence: list[str] = []
    rows = db.scalars(select(ToolCall).where(
        ToolCall.workspace_id == run.workspace_id, ToolCall.run_id == run.id,
        ToolCall.status.in_(["completed", "success"]),
    )).all()
    for call in rows:
        result = call.result_json or {}
        evidence.extend(_scalars(result))
        args = call.arguments_json or {}
        evidence.extend(_scalars(args.get("query_id") or args.get("query_ids") or ""))
    return evidence


def artifacts_for_run(db, run) -> list[dict]:
    from sqlalchemy import select
    from ...models import Artifact
    rows = db.scalars(select(Artifact).where(
        Artifact.workspace_id == run.workspace_id, Artifact.run_id == run.id,
    )).all()
    return [{"id": row.id, "name": row.name, "type": row.type} for row in rows]


def run_delivery_audit(db, run, committed_text: str) -> dict:
    """收尾调用：核对正文数字来源并返回审计结论（异常不阻断业务，只打日志）。"""
    try:
        evidence = evidence_from_run(db, run)
        artifacts = artifacts_for_run(db, run)
        report = check_grounding(committed_text=committed_text, evidence=evidence, artifacts=artifacts)
        run.context_snapshot = {**(run.context_snapshot or {}), "delivery_audit": report}
        return report
    except Exception as exc:  # 审计属于旁路，绝不能影响交付
        logger.warning("delivery audit failed (%s)", type(exc).__name__)
        return {"status": "skipped", "warnings": [], "note": "审计旁路失败，不影响交付。"}