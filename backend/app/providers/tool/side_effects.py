# -*- coding: utf-8 -*-
"""外部工具副作用的幂等键与不确定回执辅助函数。

HTTP 与 MCP provider 都可能在连接断开前已经把请求交给远端。此模块只负责
生成稳定、不会泄露业务参数的键，以及统一构造“结果尚未核实”的可读回执；
真正的重试策略仍由上层运行器决定，provider 不自动重放副作用。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


def idempotency_key(ctx: dict, spec: Any, arguments: dict) -> str:
    """返回本次外部工具调用的稳定幂等键。

    业务 Worker 会把持久化的调用键放入上下文；连接器试运行或旧调用没有该
    字段时，按运行、连接器版本、工具名和规范化参数计算回退键。回退键不把
    原始参数放进请求头，避免 SQL、文件名或用户输入进入外部日志。
    """
    supplied = str((ctx or {}).get("idempotency_key") or "").strip()
    if _SAFE_KEY.fullmatch(supplied):
        return supplied
    payload = {
        "run": (ctx or {}).get("run_id") or "anonymous",
        "connector": (ctx or {}).get("connector_version_id") or (ctx or {}).get("connector_id") or "unknown",
        "tool": getattr(spec, "name", "tool"),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "tool-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def uncertain_result(*, key: str, provider: str, error_summary: str) -> dict:
    """构造禁止自动重做的未知副作用回执，供 provider 返回 ToolResult。"""
    return {
        "status": "failed",
        "data": None,
        "artifacts": [],
        "metadata": {
            "operationOutcome": "uncertain",
            "idempotencyKey": key,
            "provider": provider,
            "retryable": False,
            "requiresVerification": True,
        },
        "error_code": "tool_result_unconfirmed",
        "error_summary": error_summary,
    }


def exception_is_uncertain(exc: Exception) -> bool:
    """判断传输异常是否发生在请求可能已经送达远端之后。"""
    extra = getattr(exc, "extra", None)
    return isinstance(extra, dict) and extra.get("operation_outcome") == "uncertain"
