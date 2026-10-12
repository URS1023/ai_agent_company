"""只检查已持久化的外部工具回执，调用方按可信运行或任务范围选择候选记录。"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from app.providers.tool.base import ToolResult
from .dispatch_state import unfinished_external_result


class PersistedToolCall(Protocol):
    """由 Worker 从可信存储读取的调用记录，不接收模型提交的回执。"""

    id: str
    run_id: str
    tool_name: str
    status: str
    idempotency_key: str | None
    result_json: dict[str, Any] | None


def unconfirmed_replay_result(*, run_id: str, tool_name: str, provider: str,
                              previous_calls: Iterable[PersistedToolCall]) -> ToolResult | None:
    """检查 provider 未确认回执、可信派发日志或旧版无阶段遗留记录。

    调用者须先核验当前身份与工具权限，并在运行锁内读取持久化 ToolCall。
    本函数不读写数据库、不执行工具，也不相信 arguments_json 中的任何标记。
    更换 call_id、事件序号或参数不能绕过同工具阻断；本函数只检查指定原运行。
    同任务续办由调用方按原运行逐条核验，独立任务不受此层限制。它不宣称
    跨运行精确一次，也不替代远端业务状态核实。
    """
    if provider not in {"http", "mcp"}:
        return None
    for call in previous_calls:
        if call.run_id != run_id or call.tool_name != tool_name:
            continue
        unfinished = unfinished_external_result(call, provider)
        result = unfinished.to_dict() if unfinished else call.result_json
        if not unfinished and call.status != "failed":
            continue
        if not isinstance(result, dict) or result.get("status") != "failed" or result.get("error_code") != "tool_result_unconfirmed":
            continue
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            continue
        # provider 回执与可信派发日志均绑定原持久调用键；无阶段旧记录须明确
        # 标为无法判断，普通错误、参数伪造和本保护的拦截回执均不能冒充新证据。
        if (metadata.get("provider") not in {"http", "mcp"}
                or metadata.get("operationOutcome") != "uncertain"
                or metadata.get("retryable") is not False
                or metadata.get("requiresVerification") is not True
                or (not call.idempotency_key and not unfinished)
                or metadata.get("idempotencyKey") != call.idempotency_key
                or metadata.get("replayBlocked") is True):
            continue
        # 防重只返回操作状态；当前工具可用不代表旧业务数据或工件仍有访问权限。
        # 不复制历史 data/artifacts 或其他自由元数据，避免把阻断分支变成缓存旁路。
        preserved = {key: metadata[key] for key in
                     ("provider", "operationOutcome", "idempotencyKey", "retryable", "requiresVerification")}
        preserved.update({
            "outcome": "unknown",
            "recovery": "reconcile_operation",
            "replayBlocked": True,
            "replayScope": "same_run_same_tool",
            "originalToolCallId": call.id,
            "originalErrorSummary": result.get("error_summary"),
            "guidance": "此前请求的业务结果尚未核实，本轮已阻止此工具再次执行；更换参数也不会重发。其他独立工具仍可继续。",
        })
        return ToolResult(
            status="failed",
            metadata=preserved,
            error_code="tool_result_unconfirmed",
            error_summary="此前外部操作的结果尚未核实，本轮已阻止该工具重复执行；请先核实原操作结果。",
        )
    return None
