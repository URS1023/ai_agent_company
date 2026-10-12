"""借鉴 Octop 的历史闭环规则，核对 Open WebUI 原生输出是否真正结束。

来源与差异记录在 services/octop/COMPLETION_UPSTREAM.json。这里只核对执行
记录和最终答复，文件页数、数据准确性及业务交付仍由各业务验收器负责。
"""
from __future__ import annotations

import json
import re
from typing import Any


_UNFINISHED = {"in_progress", "pending", "queued", "incomplete", "requires_approval"}
_FAILED = {"failed", "error", "cancelled", "canceled", "rejected"}
_PROGRESS_LINE = re.compile(
    r"^(?:现在)?(?:我来|我会|我将|让我|正在|继续|接下来|稍后|马上|待会|请稍等)"
    r".{0,120}(?:查询|获取|分析|生成|制作|写入|完善|完成|处理|导出|创建|补齐|整理|检查|等待).{0,90}[。.!！…]*$"
    r"|^(?:let me|i(?:'ll| will)|continuing|working on|please wait)\b.{0,180}$",
    re.IGNORECASE,
)


def _text(content: Any) -> str:
    """仅提取可见答复，推理片段和工具输入不能充当最终回答。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}
        ).strip()
    return ""


def _progress_only(text: str) -> bool:
    """只排除全部由明确进度承诺组成的工具后答复，普通咨询不受此规则限制。"""
    # 数值、文件链接及完成陈述说明可能已有实际结论，不用措辞启发式否定它。
    # 这些内容的真实性由业务交付核验负责，而不是通过自然语言正则证明。
    if re.search(
        r"\d|https?://|/api/|artifact:|\[[^\]]+\]\([^)]+\)"
        r"|已(?:经)?(?:完成|生成|导出|保存|查到|查询|写入|创建|制作|交付|分析)"
        r"|(?:结果|结论)(?:为|是)|统计显示", text, re.IGNORECASE
    ):
        return False
    lines = [line.strip(" \t#*-") for line in re.split(r"[\n\r]+", text) if line.strip()]
    return bool(lines) and all(_PROGRESS_LINE.fullmatch(line) for line in lines)


def _arguments_complete(value: Any) -> bool:
    """流式参数必须已收齐为 JSON 对象，空片段和数组都不能视为有效调用。"""
    if isinstance(value, dict):
        try:
            json.dumps(value, allow_nan=False)
            return True
        except (ValueError, TypeError):
            return False
    if not isinstance(value, str):
        return False
    try:
        def reject_constant(_: str) -> None:
            raise ValueError("JSON 参数包含非标准数值")
        return isinstance(json.loads(value, parse_constant=reject_constant), dict)
    except (ValueError, TypeError):
        return False


def _tool_failed(item: dict) -> bool:
    """兼容原生失败状态及业务网关的 JSON 回执，不扫描任意业务文本关键字。"""
    if _status(item) in _FAILED or item.get("error"):
        return True
    output = item.get("output")
    values = output if isinstance(output, list) else [output]
    for value in values:
        if isinstance(value, dict) and value.get("type") in {"input_text", "text"}:
            value = value.get("text")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        if isinstance(value, dict) and (
            _status(value) in _FAILED or value.get("error") or value.get("error_code")
        ):
            return True
    return False


def _status(item: dict) -> str:
    """畸形状态字段不能导致核验器自身报错。"""
    value = item.get("status")
    return value if isinstance(value, str) else ""


def inspect_completion(
    output: Any, *, done: bool = True, error: Any = None, fallback_text: str = ""
) -> dict[str, Any]:
    """核对一条持久化原生助手消息的完整输出快照，不接受未合并的流式增量。

    complete 表示执行闭环、最终答复和已知工具失败均已处理；execution_complete
    仅表示调用记录闭合。后续同名工具成功会解除旧失败的阻断，但不能证明业务
    内容等价，调用方仍须核验文件或数据。纯普通回答可使用 fallback_text；存在
    工具时必须从 output 的时序中找到工具之后的答复，避免复用前面的进度文案。
    """
    errors: list[dict[str, str]] = []
    unfinished: list[str] = []
    missing: list[str] = []
    calls: dict[str, tuple[int, dict]] = {}
    results: dict[str, tuple[int, dict]] = {}
    messages: list[tuple[int, str]] = []
    last_tool_index = -1

    def fail(code: str, item_id: str = "") -> None:
        record = {"code": code}
        if item_id:
            record["id"] = item_id
        if record not in errors:
            errors.append(record)

    if not done:
        fail("native_not_done")
    if error:
        # 不把上游错误正文写入审计摘要，正文可能包含 SQL 或模型凭据。
        fail("native_error")
    if output is None:
        output = []
    if not isinstance(output, list):
        fail("invalid_output")
        output = []
    for index, item in enumerate(output):
        if not isinstance(item, dict):
            fail("invalid_output_item", f"output@{index}")
            continue
        kind = item.get("type")
        item_id = str(item.get("call_id") or item.get("id") or f"output@{index}")
        status = _status(item)
        if item.get("status") is not None and not isinstance(item["status"], str):
            fail("invalid_output_status", item_id)
        if status in _UNFINISHED:
            unfinished.append(item_id)
        if item.get("error") and kind not in {"function_call", "function_call_output"}:
            fail("output_error", item_id)
        if kind in {"error", "response.failed", "response.incomplete"}:
            fail("output_error", item_id)
        if kind in {"function_call", "function_call_output"}:
            last_tool_index = index
            call_id = str(item.get("call_id") or (item.get("id") if kind == "function_call" else "") or "")
            if not call_id:
                fail("missing_call_id", item_id)
                call_id = item_id
            target = calls if kind == "function_call" else results
            if call_id in target:
                fail("duplicate_call" if kind == "function_call" else "duplicate_result", call_id)
            target[call_id] = (index, item)
            if kind == "function_call":
                if not isinstance(item.get("name"), str) or not item["name"].strip():
                    fail("missing_tool_name", call_id)
                if not _arguments_complete(item.get("arguments")):
                    fail("incomplete_tool_arguments", call_id)
                if item.get("error") and status not in _FAILED:
                    fail("output_error", call_id)
            elif "output" not in item:
                fail("missing_result_payload", call_id)
            continue
        if status in _FAILED:
            fail("output_failed", item_id)
        if kind == "message" and item.get("role", "assistant") == "assistant":
            text = _text(item.get("content"))
            if text and status not in _UNFINISHED | _FAILED:
                messages.append((index, text))

    failures: list[dict[str, Any]] = []
    successes: dict[str, list[int]] = {}
    successful_calls: list[dict[str, Any]] = []
    for call_id, (call_index, call) in calls.items():
        if call_id not in results:
            missing.append(call_id)
            continue
        result_index, result = results[call_id]
        if result_index <= call_index:
            fail("result_before_call", call_id)
        name = str(call.get("name") or "")
        if _tool_failed(call) or _tool_failed(result):
            failures.append({"call_id": call_id, "name": name, "index": result_index})
        elif _status(result) not in _UNFINISHED and _status(call) not in _UNFINISHED:
            successes.setdefault(name, []).append(result_index)
            if (result_index > call_index and "output" in result and name
                    and _arguments_complete(call.get("arguments"))
                    and not any(item.get("id") == call_id for item in errors)):
                successful_calls.append({"call_id": call_id, "name": name, "index": result_index})
    for call_id in results.keys() - calls.keys():
        fail("orphan_tool_result", call_id)
    recovered, unresolved = [], []
    for failure in failures:
        is_recovered = any(index > failure["index"] for index in successes.get(failure["name"], []))
        public = {key: failure[key] for key in ("call_id", "name")}
        (recovered if is_recovered else unresolved).append(public)

    final_answer = "\n\n".join(text for index, text in messages if index > last_tool_index)
    if last_tool_index == -1 and not final_answer and not output:
        final_answer = str(fallback_text or "").strip()
    if not final_answer:
        fail("missing_final_answer")
    elif _progress_only(final_answer):
        # 尚未调用工具就停在“让我继续分析”同样只是承诺，不能作为任务的最终交付。
        fail("progress_only_answer")
    execution_complete = not errors and not missing and not unfinished
    return {
        "complete": execution_complete and not unresolved,
        "execution_complete": execution_complete,
        "final_answer": final_answer,
        "missing_tool_results": missing,
        "unfinished_ids": list(dict.fromkeys(unfinished)),
        "errors": errors,
        "failed_tool_calls": unresolved,
        "recovered_tool_calls": recovered,
        "successful_tool_calls": successful_calls,
        "tool_calls": len(calls),
        "tool_results": len(results),
    }
