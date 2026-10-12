"""跨原生补交消息保存工具失败，文件附件不能替未完成的业务操作背书。"""
from __future__ import annotations

from copy import deepcopy


_SIMPLE_OUTPUTS = {
    "render_spreadsheet": "xlsx", "export_query_spreadsheet": "xlsx",
    "render_presentation": "pptx", "create_presentation_plan": "pptx",
    "write_presentation_slides": "pptx", "finalize_presentation": "pptx",
    "render_chart_image": "chart", "render_dashboard_image": "dashboard_image",
}


def _safe_arguments(name: str, args: dict | None) -> dict:
    """只保留交付类型参数，SQL、代码、文档正文和业务数据不得进入失败摘要。"""
    args = args if isinstance(args, dict) else {}
    result = {}
    if name == "render_document":
        value = str(args.get("format") or "docx").lower()
        result["format"] = value if value in {"docx", "pdf"} else "unknown"
    if name == "render_chart_image" and isinstance(args.get("chart_type"), str) and args["chart_type"] in {"pie", "bar", "line"}:
        result["chart_type"] = args["chart_type"]
    if name == "export_inspection_report":
        value = args.get("formats")
        if isinstance(value, list):
            result["formats"] = [item for item in value if isinstance(item, str) and item in {"docx", "xlsx", "chart"}]
    return result


def _replacement_targets(failure: dict, required: dict) -> set[str]:
    """限定无业务写入的生成工具；未知工具、Python、查询和工作流一律不能靠附件豁免。"""
    name = failure["name"]
    if name == "render_document":
        # 未观察到真实参数时不推断默认格式，防止 PDF 失败被 Word 附件掩盖。
        output = failure.get("arguments", {}).get("format")
        return {output} if output in {"docx", "pdf"} and required.get(output) else set()
    if name == "export_inspection_report":
        outputs = {kind for kind in ("docx", "xlsx", "chart") if required.get(kind)}
        formats = failure.get("arguments", {}).get("formats")
        if formats:
            outputs.intersection_update(formats)
        return outputs
    output = _SIMPLE_OUTPUTS.get(name)
    if output == "dashboard_image":
        return {output} if required.get("embedded_dashboard") else set()
    return {output} if output and required.get(output) else set()


def _replaced_by_delivery(failure: dict, delivery: dict) -> bool:
    """只有完整清单和实际核验文件同时满足，才允许有限的等类交付替代。"""
    required = delivery.get("required")
    if not isinstance(required, dict) or not required or delivery.get("missing") != [] or delivery.get("blocker"):
        return False
    targets = _replacement_targets(failure, required)
    if not targets:
        return False
    files = delivery.get("files")
    if not isinstance(files, list):
        return False
    for kind in targets:
        matches = [row for row in files if isinstance(row, dict) and row.get("kind") == kind and row.get("id")]
        if not matches:
            return False
        if kind == "pptx" and type(required.get("pptx")) is int:
            if not any(row.get("pages") == required["pptx"] for row in matches):
                return False
        if kind == "chart":
            types = {item for row in matches for item in row.get("chart_types", [])}
            needed = set(required.get("chart_types") or [])
            original = failure.get("arguments", {}).get("chart_type")
            if original:
                needed.add(original)
            if not needed.issubset(types):
                return False
    return True


class ToolFailureLedger:
    """保留失败历史及其后续解决依据；同名成功只证明工具后来执行成功，不证明语义等价。"""

    def __init__(self, saved_state: dict | None = None):
        """从服务端快照恢复，不依赖当前原生消息是否还包含较早工具记录。"""
        saved = saved_state if isinstance(saved_state, dict) else {}
        self._messages = list(dict.fromkeys(value for value in saved.get("messages", []) if isinstance(value, str)))
        self._calls: dict[tuple[str, str], dict] = {}
        for value in saved.get("calls", []):
            if not isinstance(value, dict) or value.get("outcome") not in {"failed", "success"}:
                continue
            if not all(isinstance(value.get(key), str) and value[key] for key in ("message_id", "call_id", "name")):
                continue
            row = {key: deepcopy(value[key]) for key in (
                "message_id", "call_id", "name", "outcome", "arguments", "sequence", "authoritative", "resolved_by",
            ) if key in value}
            row["arguments"] = _safe_arguments(row["name"], row.get("arguments")) if row.get("authoritative") else {}
            if type(row.get("sequence")) is not int:
                row["sequence"] = len(self._calls)
            self._calls[(row["message_id"], row["call_id"])] = row
            if row["message_id"] not in self._messages:
                self._messages.append(row["message_id"])
        self._sequence = max((row["sequence"] for row in self._calls.values()), default=-1) + 1

    def _record(self, message_id: str, call_id: str, name: str, outcome: str, *, args=None, authoritative=False) -> dict | None:
        """同一调用只登记一次；原生快照不能覆盖业务网关已经记录的真实结果。"""
        if not all(isinstance(value, str) and value for value in (message_id, call_id, name)):
            return None
        if message_id not in self._messages:
            self._messages.append(message_id)
        key = (message_id, call_id)
        current = self._calls.get(key)
        if current is not None:
            if authoritative:
                current.update(name=name, outcome=outcome, arguments=_safe_arguments(name, args), authoritative=True)
            return current
        row = {"message_id": message_id, "call_id": call_id, "name": name, "outcome": outcome,
               "arguments": _safe_arguments(name, args) if authoritative else {},
               "sequence": self._sequence, "authoritative": authoritative}
        self._sequence += 1
        self._calls[key] = row
        return row

    def _resolve_with(self, success: dict, *, same_message_ids: set[str] | None = None, observed_order=False) -> None:
        """成功只解除较早的同名失败；重新读取旧消息不会解决后来发生的失败。"""
        if success["outcome"] != "success":
            return
        order = self._messages.index(success["message_id"])
        for row in self._calls.values():
            if row["outcome"] != "failed" or row["name"] != success["name"]:
                continue
            prior = self._messages.index(row["message_id"]) < order
            if row["message_id"] == success["message_id"]:
                prior = (row["sequence"] < success["sequence"] if observed_order else
                         row["call_id"] in (same_message_ids or set()))
            if prior:
                row["resolved_by"] = {"message_id": success["message_id"], "call_id": success["call_id"]}

    def observe_tool(self, native_message_id: str, call_id: str, name: str, args: dict, result) -> None:
        """在工具结果持久化时调用，保持跨消息顺序并只提取安全的格式参数。"""
        status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
        if status not in {"success", "failed", "cancelled", "error"}:
            return
        row = self._record(native_message_id, call_id, name, "success" if status == "success" else "failed",
                           args=args, authoritative=True)
        if row is not None and row["outcome"] == "success":
            self._resolve_with(row, observed_order=True)

    def observe_completion(self, native_message_id: str, completion: dict) -> None:
        """吸收持久原生检查结果，纯口头回复或新消息不会清除旧失败。"""
        recovered = set()
        for key in ("failed_tool_calls", "recovered_tool_calls"):
            for item in completion.get(key) or []:
                if not isinstance(item, dict):
                    continue
                row = self._record(native_message_id, item.get("call_id"), item.get("name"), "failed")
                if row is not None and key == "recovered_tool_calls":
                    recovered.add(row["call_id"])
        successes = completion.get("successful_tool_calls") or []
        for item in sorted((row for row in successes if isinstance(row, dict)),
                           key=lambda row: row.get("index") if type(row.get("index")) is int else -1):
            row = self._record(native_message_id, item.get("call_id"), item.get("name"), "success")
            if row is not None:
                self._resolve_with(row, same_message_ids=recovered)

    def state(self) -> dict:
        """返回可写入运行快照的纯 JSON 状态，不包含参数正文和错误原文。"""
        return {"version": 1, "messages": list(self._messages), "calls": deepcopy(list(self._calls.values()))}

    def status(self, delivery_state: dict | None = None) -> dict:
        """区分仍阻断、同名后来成功和经文件核验替代，保留结论的实际证明边界。"""
        unresolved, recovered, replaced = [], [], []
        for row in self._calls.values():
            if row["outcome"] != "failed":
                continue
            record = {key: deepcopy(row[key]) for key in ("message_id", "call_id", "name", "arguments")}
            if row.get("resolved_by"):
                record.update(resolution="same_tool_later_success", resolved_by=deepcopy(row["resolved_by"]))
                recovered.append(record)
            elif _replaced_by_delivery(row, delivery_state or {}):
                record["resolution"] = "validated_artifact_replacement"
                replaced.append(record)
            else:
                unresolved.append(record)
        return {"unresolved": unresolved, "recovered": recovered, "replaced": replaced,
                "validation_scope": "同名工具后来成功仅证明执行成功；等类附件替代仅证明交付完整，均不证明原任务业务语义等价"}

    def effective(self, delivery_state: dict | None = None) -> list[dict]:
        """返回仍须处理的失败，供任务完成检查使用，不触发任何自动重试。"""
        return self.status(delivery_state)["unresolved"]
