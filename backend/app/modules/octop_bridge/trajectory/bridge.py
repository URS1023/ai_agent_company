"""将现有运行事件映射为 Octop 轨迹，保留闭环证据而不复制敏感正文。"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, replace
from typing import Any

from .metrics import aggregate_metrics
from .projector import project_harness_chunk
from .types import TrajectoryEvent


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _label(value: Any, limit: int = 160) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


class RunTrajectory:
    """为单个业务运行记录模型与工具闭环；模型线程可安全追加事件。"""

    def __init__(self, run_id: str, conversation_id: str, workspace_id: str, user_id: str):
        self.run_id = str(run_id)
        self.conversation_id = str(conversation_id)
        self.workspace_id = str(workspace_id)
        self.user_id = str(user_id)
        self._lock = threading.RLock()
        self._events: list[TrajectoryEvent] = []
        self._nodes: dict[tuple[str, str], int] = {}
        self._started = False
        self._terminal: int | None = None

    def _project(self, chunk: dict[str, Any]) -> int:
        chunk = {**chunk, "ts": time.time(), "turn_id": self.run_id}
        projected = project_harness_chunk(
            chunk, agent_id="openwebui", thread_id=self.conversation_id,
            seq=len(self._events) + 1,
        )
        if len(projected) != 1:
            raise ValueError("运行轨迹事件未生成唯一投影")
        # 上游事件 ID 只含会话序号，本系统追加 run_id 避免跨轮次覆盖。
        event = replace(projected[0], event_id=f"{self.run_id}:{len(self._events) + 1}")
        self._events.append(event)
        return len(self._events) - 1

    def observe(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """只接收已知运行事件；参数、查询结果、模型正文和异常原文均不入轨迹。"""
        data = data or {}
        with self._lock:
            if event_type == "run.started":
                if not self._started:
                    self._project({"type": "user", "content": "", "source": "enterprise"})
                    self._started = True
                return
            if event_type == "context.loaded":
                # 仅记录来源名称、内容指纹和数量，不保存实际提示词或业务材料。
                chunk = {"type": "context", "source": _label(data.get("source")),
                         "label": _label(data.get("label") or "已加载上下文")}
                for name in ("content_sha256", "content_chars", "tokens"):
                    value = data.get(name)
                    if name == "content_sha256" and isinstance(value, str) and len(value) == 64:
                        chunk[name] = value
                    elif name != "content_sha256" and _number(value) is not None:
                        chunk[name] = int(value)
                self._project(chunk)
                return
            prefix, _, action = event_type.partition(".")
            if prefix in {"model", "tool"} and action in {"started", "completed", "failed"}:
                self._observe_node(prefix, action, data)
                return
            if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                # 现有 SSE 用 run.failed 通知停止，轨迹仍区分用户取消与执行异常。
                status = "cancelled" if data.get("status") in {"cancelled", "canceled"} else action
                payload = {"status": status}
                if data.get("error_code"):
                    payload["error_code"] = _label(data["error_code"])
                event = TrajectoryEvent(
                    event_id=f"{self.run_id}:terminal", thread_id=self.conversation_id,
                    agent_id="openwebui", seq=len(self._events) + 1, ts=time.time(),
                    kind="unknown", turn_id=self.run_id, request_seq=None,
                    is_error=status == "failed", summary="运行结束", payload=payload,
                )
                if self._terminal is None:
                    self._terminal = len(self._events)
                    self._events.append(event)
                else:
                    self._events[self._terminal] = replace(event, seq=self._terminal + 1)

    def _observe_node(self, kind: str, action: str, data: dict[str, Any]) -> None:
        raw_id = data.get("tool_call_id") if kind == "tool" else data.get("request_id")
        call_id = str(raw_id or data.get("id") or "")
        if not call_id:
            raise ValueError("模型和工具轨迹必须包含稳定的调用 ID")
        key = (kind, call_id)
        index = self._nodes.get(key)
        if index is None:
            chunk = ({"type": "tool_call_chunk", "id": call_id,
                      "name": _label(data.get("tool") or data.get("name") or "tool")}
                     if kind == "tool" else
                     {"type": "token", "content": "模型调用", "node": "model"})
            index = self._project(chunk)
            self._nodes[key] = index
            event = self._events[index]
            payload = {k: v for k, v in event.payload.items() if k != "args"}
            payload.update({"call_id": call_id, "status": "running", "node_type": kind,
                            "start_observed": action == "started"})
            self._events[index] = replace(event, payload=payload)
        event = self._events[index]
        if action == "started" or event.payload.get("status") != "running":
            return
        supplied_status = data.get("status")
        cancelled = supplied_status in {"cancelled", "canceled"}
        failed = not cancelled and (action == "failed" or supplied_status in {"failed", "error"})
        status = "cancelled" if cancelled else "failed" if failed else "completed"
        payload = {**event.payload, "status": status}
        duration = _number(data.get("duration_ms"))
        if duration is None and payload.get("start_observed"):
            duration = max(0.0, (time.time() - event.ts) * 1000)
        if duration is not None:
            payload["tool_duration_ms" if kind == "tool" else "llm_duration_ms"] = duration
        if data.get("error_code"):
            payload["error_code"] = _label(data["error_code"])
        if kind == "tool":
            ids = data.get("artifact_ids")
            if isinstance(ids, list):
                payload["artifact_ids"] = [_label(value) for value in ids if isinstance(value, str)][:64]
        else:
            self._copy_usage(payload, data)
        self._events[index] = replace(event, payload=payload, is_error=failed)

    @staticmethod
    def _copy_usage(payload: dict[str, Any], data: dict[str, Any]) -> None:
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else data
        # Octop 将 input_tokens 定义为非缓存输入；OpenAI prompt_tokens 已包含缓存。
        prompt = _number(usage.get("prompt_tokens"))
        input_count = _number(usage.get("input_tokens"))
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        cached = _number(usage.get("cache_read_tokens"))
        if cached is None:
            cached = _number(details.get("cached_tokens"))
        if prompt is not None:
            input_count = max(0.0, prompt - (cached or 0))
        output = _number(usage.get("output_tokens", usage.get("completion_tokens")))
        for key, value in (("input_tokens", input_count), ("output_tokens", output),
                           ("cache_read_tokens", cached)):
            if value is not None:
                payload[key] = int(value)
        ttft = _number(data.get("ttft_ms"))
        if ttft is not None:
            payload["ttft_ms"] = ttft
        duration = payload.get("llm_duration_ms")
        if output is not None and duration and duration > 0:
            payload["tok_per_s"] = output * 1000 / duration

    def snapshot(self, *, max_events: int = 160) -> dict[str, Any]:
        """返回可持久化的脱敏摘要；统计与闭环检查始终覆盖全部事件。"""
        with self._lock:
            metrics = asdict(aggregate_metrics(self._events))
            nodes = [event for event in self._events if event.payload.get("node_type")]
            open_tools = [event.payload["call_id"] for event in nodes
                          if event.kind == "tool" and event.payload.get("status") == "running"]
            open_models = [event.payload["call_id"] for event in nodes
                           if event.kind == "assistant" and event.payload.get("status") == "running"]
            unpaired = [event.payload["call_id"] for event in nodes
                        if not event.payload.get("start_observed")]
            limit = max(1, int(max_events))
            return {
                "engine": "octop.trajectory", "source_version": "1.0.1",
                "run_id": self.run_id, "conversation_id": self.conversation_id,
                "workspace_id": self.workspace_id, "user_id": self.user_id,
                "metrics": metrics,
                "events": [asdict(event) for event in self._events[-limit:]],
                "event_count": len(self._events),
                "events_truncated": len(self._events) > limit,
                "open_tool_calls": open_tools, "open_model_calls": open_models,
                "failed_tool_calls": [event.payload["call_id"] for event in nodes
                                      if event.kind == "tool" and event.is_error],
                "failed_model_calls": [event.payload["call_id"] for event in nodes
                                       if event.kind == "assistant" and event.is_error],
                "cancelled_tool_calls": [event.payload["call_id"] for event in nodes
                                         if event.kind == "tool" and event.payload.get("status") == "cancelled"],
                "cancelled_model_calls": [event.payload["call_id"] for event in nodes
                                          if event.kind == "assistant" and event.payload.get("status") == "cancelled"],
                "integrity": {"calls_closed": not open_tools and not open_models,
                              "starts_observed": not unpaired, "unpaired_results": unpaired},
            }
