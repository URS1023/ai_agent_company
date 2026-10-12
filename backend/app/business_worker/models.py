"""DSH 主会话与业务辅助请求共享的模型参数映射。"""
from __future__ import annotations

from app.modules.harness.context import model_configuration as legacy_configuration


def model_configuration(config):
    """规范关闭思考的语义，拒绝冲突档位，不静默改回 high。"""
    from app.errors import ValidationError
    runtime = (config.get("capabilities") or {}).get("runtime_options") or {}
    result = legacy_configuration(config)
    effort = runtime.get("reasoning_effort")
    if runtime.get("thinking") == "disabled":
        if effort and effort != "off":
            raise ValidationError("关闭思考时推理等级必须为 off，不能同时选择其它等级")
        result["thinking"] = "disabled"
        if result["protocol"] == "deepseek-messages":
            result["reasoningEffort"] = "off"
        else:
            result.pop("reasoningEffort", None)
            result["reasoningEfforts"] = False
    elif effort == "off":
        result["thinking"] = "disabled"
    return result
