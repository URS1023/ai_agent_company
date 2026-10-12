"""离线检查聊天投影与模型配置：只抽取纯定义，不加载应用或连接服务。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


def definition(relative: str, name: str, namespace: dict):
    """抽取目标定义执行，避免执行模块导入及应用初始化。"""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    node = next(item for item in tree.body if isinstance(item, (ast.ClassDef, ast.FunctionDef)) and item.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, relative, "exec"), namespace)
    return namespace[name]


projection_class = definition("backend/app/modules/harness/projection.py", "NativeProjection", {"HarnessUnavailable": RuntimeError})
projection = projection_class()
projection.feed({"type": "snapshot", "cursor": -1})
projection.feed({"type": "assistant-stream", "frame": {"type": "start", "attemptId": "test-attempt"}})
reasoning = projection.feed({"type": "assistant-stream", "frame": {
    "type": "chunk", "attemptId": "test-attempt", "index": 0,
    "chunk": {"type": "reasoning-delta", "index": 0, "text": "仅用于离线测试的思考块"},
}})
assert reasoning == [], reasoning
text = projection.feed({"type": "assistant-stream", "frame": {
    "type": "chunk", "attemptId": "test-attempt", "index": 1,
    "chunk": {"type": "text-delta", "index": 1, "text": "正文可见"},
}})
assert text[0][0] == "message.delta" and text[0][1]["delta"] == "正文可见", text
durable = projection.feed({"type": "event", "event": {"seq": 0, "type": "assistant/message", "data": {"message": {"content": [
    {"type": "reasoning", "text": "历史思考块"}, {"type": "text", "text": "历史正文"},
]}}}})
assert durable[0][1]["content"] == "历史正文", durable
native_tool = projection.feed({"type": "event", "event": {"seq": 1, "type": "tool/call", "data": {
    "name": "exit_plan_mode", "callId": "plan-test", "arguments": '{"plan":"# 测试计划"}',
}}})
assert native_tool == [], native_tool

configure = definition("backend/app/modules/harness/context.py", "model_configuration", {
    "urlsplit": urlsplit,
    "settings": SimpleNamespace(HARNESS_CONTEXT_WINDOW=100000, HARNESS_MAX_OUTPUT_TOKENS=8000),
})
config = {"base_url": "https://api.deepseek.com", "model": "offline-placeholder", "capabilities": {
    "runtime_options": {"protocol": "deepseek-messages", "thinking": "disabled"},
}}
disabled_only = configure(config)
assert disabled_only["thinking"] == "enabled", disabled_only
config["capabilities"]["runtime_options"]["reasoning_effort"] = "off"
disabled_and_off = configure(config)
assert disabled_and_off["thinking"] == "disabled", disabled_and_off

print(json.dumps({
    "passed": 6,
    "reasoning_delta_projected": False,
    "text_delta_projected": True,
    "durable_reasoning_kept": False,
    "native_plan_tool_projected": False,
    "deepseek_messages_thinking_disabled_alone": disabled_only["thinking"],
    "deepseek_messages_thinking_disabled_with_effort_off": disabled_and_off["thinking"],
    "service_starts": 0,
    "database_connections": 0,
    "network_requests": 0,
}, ensure_ascii=False, indent=2))
