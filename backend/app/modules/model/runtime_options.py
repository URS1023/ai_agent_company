"""模型运行选项的有限契约，不能借配置写入密钥引用或任意上游参数。"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError as SchemaError, model_validator

from app.config import settings
from app.errors import ValidationError


class ModelCompatibility(BaseModel):
    """只开放当前 OpenAI 兼容路由明确支持的标量开关。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    maxTokensField: Literal["max_tokens", "max_completion_tokens"] | None = None
    thinkingFormat: Literal["openai", "deepseek", "openrouter", "together", "zai", "qwen", "string-thinking", "ant-ling"] | None = None
    supportsStore: StrictBool | None = None
    supportsDeveloperRole: StrictBool | None = None
    supportsReasoningEffort: StrictBool | None = None
    supportsUsageInStreaming: StrictBool | None = None
    supportsFinishReason: StrictBool | None = None
    requiresToolResultName: StrictBool | None = None
    requiresAssistantAfterToolResult: StrictBool | None = None
    requiresThinkingAsText: StrictBool | None = None
    requiresReasoningContentOnAssistantMessages: StrictBool | None = None


class ModelRuntimeOptions(BaseModel):
    """显式声明模型协议、预算和能力；省略的项目保持部署默认。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: Literal["deepseek-messages", "openai-completions"] | None = None
    context_window: int | None = Field(default=None, ge=4096, le=4_194_304)
    max_output_tokens: int | None = Field(default=None, ge=128, le=1_048_576)
    reasoning_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    thinking: Literal["enabled", "disabled"] | None = None
    input_modalities: list[Literal["text", "image"]] | None = Field(default=None, min_length=1, max_length=2)
    compat: ModelCompatibility | None = None

    @model_validator(mode="after")
    def valid_budget_and_modalities(self):
        """输出预算必须为上下文留出输入空间，声明的模态不能重复。"""
        window = self.context_window or settings.HARNESS_CONTEXT_WINDOW
        output = self.max_output_tokens or settings.HARNESS_MAX_OUTPUT_TOKENS
        if window <= output + 1024:
            raise ValueError("上下文窗口必须大于输出上限加 1024 token")
        if self.input_modalities and (len(set(self.input_modalities)) != len(self.input_modalities) or "text" not in self.input_modalities):
            raise ValueError("输入模态必须包含 text 且不能重复")
        return self


def normalize_runtime_options(value: dict | None, endpoint: str) -> dict:
    """服务层再次校验，避免非 HTTP 调用绕过字段与协议边界。"""
    try:
        options = ModelRuntimeOptions.model_validate(value or {}).model_dump(exclude_none=True)
    except SchemaError as exc:
        raise ValidationError("模型运行选项字段或预算无效") from exc
    protocol = options.get("protocol") or ("deepseek-messages" if urlsplit(endpoint).hostname == "api.deepseek.com" else "openai-completions")
    if protocol == "deepseek-messages":
        if options.get("reasoning_effort") not in {None, "off", "low", "high", "max"}:
            raise ValidationError("DeepSeek Messages 的推理等级仅支持 off、low、high、max")
        if options.get("compat"):
            raise ValidationError("compat 仅适用于 OpenAI 兼容协议")
    return options


def public_runtime_options(capabilities: dict) -> dict:
    """只输出经过白名单校验的运行参数，不返回 capabilities_json 的内部字段。"""
    try:
        return ModelRuntimeOptions.model_validate(capabilities.get("runtime_options") or {}).model_dump(exclude_none=True)
    except SchemaError:
        return {}
