# -*- coding: utf-8 -*-
"""Result Normalizer — unify heterogeneous tool outputs into a single protocol.

工具结果的统一出口：任何 provider 的返回值（ToolResult / dict / list / 标量）
都经这里转成 ToolResult，保证模型与编排层只需要理解一种结构。

关键约束：
- 兼容"已是 ToolResult"的返回值（provider 内部已归一化过则原样透传），
  因此 provider 可以自由选择返回 dict 还是 ToolResult。
- 不是所有权威状态都来自 status 字段：dict 型返回值若带 artifacts 列表，
  即使没有 status 也按成功处理并把 artifacts 提升到结果对象上。
- 失败结果不抛异常，只做承载；错误码/摘要需由调用方（gateway）填写。
"""
from __future__ import annotations

from typing import Any

from .base import ToolResult


def normalize(result: Any, *, tool_name: str = "", suggestion: dict = None) -> ToolResult:
    """Convert an arbitrary provider return value into a ToolResult.

    参数：``tool_name`` 只用于在 metadata 里标注来源工具（便于日志与审计追踪），
    ``suggestion`` 为早期版本保留的兼容参数，当前不参与结果构造。
    返回：status ∈ {success, running, failed, cancelled} 的 ToolResult；
    dict 的 data 优先取 ``data``，兼容旧 provider 使用的 ``result`` 键。
    本函数不做鉴权、不写库、不抛业务异常。
    """
    if isinstance(result, ToolResult):
        return result
    if isinstance(result, dict):
        if result.get("status") in ("success", "running", "failed", "cancelled"):
            return ToolResult(
                status=result.get("status", "success"),
                data=result.get("data", result.get("result")),
                artifacts=result.get("artifacts") or [],
                metadata=result.get("metadata") or {},
                error_code=result.get("error_code"),
                error_summary=result.get("error_summary"),
            )
        # Simple mapping: try to surface artifacts if present.
        artifacts = []
        if "artifacts" in result and isinstance(result["artifacts"], list):
            artifacts = result["artifacts"]
        return ToolResult(
            status="success",
            data=result,
            artifacts=artifacts,
            metadata={"tool": tool_name},
        )
    if isinstance(result, (list, tuple)):
        return ToolResult(status="success", data=list(result), metadata={"tool": tool_name})
    return ToolResult(status="success", data=result, metadata={"tool": tool_name})


def make_error_result(error_code: str, message: str) -> ToolResult:
    """构造失败结果：status=failed、data=None，错误码与中文摘要回给模型。

    ``error_code`` 用稳定的英文枚举（如 tool_not_found / tool_execution_error），
    供上层做程序化分支；``message`` 是面向用户与模型的说明，两者不要混用。
    """
    return ToolResult(status="failed", data=None, error_code=error_code, error_summary=message)
