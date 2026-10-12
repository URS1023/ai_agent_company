"""将原生会话事件投影为现有聊天正文，区分增量尝试与已提交消息。"""
from __future__ import annotations

from .client import HarnessUnavailable


class NativeProjection:
    """只读取事件事实，不推断完成或触发续跑。"""

    def __init__(self):
        self.cursor = -1
        self.attempt_id = None
        self.next_index = 0
        self.text = ""
        self.committed_text = ""
        self.last_event = None

    def feed(self, frame: dict) -> list[tuple[str, dict]]:
        """历史快照仅建立游标；当前增量去重，结算正文按原生事件替换。"""
        self.last_event = None
        kind = frame.get("type")
        if kind == "snapshot":
            self.cursor = int(frame["cursor"])
            return []
        if kind == "assistant-stream":
            stream = frame.get("frame") or {}
            if stream.get("type") == "start":
                self.attempt_id = stream["attemptId"]
                self.next_index = 0
                self.text = ""
                return [("message.replace", {"content": "", "attempt_id": self.attempt_id, "committed": False})]
            if stream.get("attemptId") != self.attempt_id:
                return []
            if stream.get("type") == "chunk":
                index = int(stream["index"])
                if index < self.next_index:
                    return []
                if index != self.next_index:
                    raise HarnessUnavailable("Harness 实时事件缺失，不能继续拼接不完整正文")
                self.next_index += 1
                chunk = stream.get("chunk") or {}
                if chunk.get("type") == "text-delta":
                    text = chunk.get("text") or ""
                    self.text += text
                    return [("message.delta", {"delta": text, "attempt_id": self.attempt_id})]
            if stream.get("type") == "end" and (stream.get("outcome") or {}).get("kind") == "abandoned":
                self.text = self.committed_text
                return [("message.replace", {"content": self.text, "attempt_id": self.attempt_id, "committed": True})]
            return []
        if kind != "event":
            return []
        event = frame.get("event") or {}
        seq = int(event["seq"])
        if seq <= self.cursor:
            return []
        self.cursor = seq
        self.last_event = event
        data = event.get("data") or {}
        if event.get("type") == "assistant/message":
            self.committed_text = "".join(block.get("text") or "" for block in
                (data.get("message") or {}).get("content", []) if block.get("type") == "text")
            self.text = self.committed_text
            return [("message.replace", {"content": self.text, "attempt_id": self.attempt_id,
                                          "committed": True, "native_sequence": seq})]
        if event.get("type") == "assistant/attempt":
            self.text = self.committed_text
            return [("message.replace", {"content": self.text, "attempt_id": self.attempt_id,
                                          "committed": True, "native_sequence": seq})]
        if event.get("type") == "compaction/end":
            if data.get("error"):
                return [("task.progress", {"message": "上下文压缩未完成。", "error_code": "harness_compaction_failed"})]
            return [("context.compacted", {"engine": "harness", "native_sequence": seq})]
        if event.get("type") == "llm/retry":
            return [("task.progress", {"message": "模型请求暂未完成，正在按有限重试策略恢复。"})]
        return []


def native_usage(event: dict) -> dict | None:
    """原生输入量不含缓存，平台输入量按三部分之和记账。"""
    data = event.get("data") or {}
    usage = data.get("usage")
    if not usage:
        for record in data.get("stream") or []:
            chunk = record.get("chunk") or {}
            if chunk.get("type") == "usage":
                usage = chunk.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = sum(int(usage.get(key) or 0) for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))
    completion = int(usage.get("outputTokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": int(usage.get("totalTokens") or prompt + completion),
            "prompt_tokens_details": {"cached_tokens": int(usage.get("cacheReadTokens") or 0)}}
