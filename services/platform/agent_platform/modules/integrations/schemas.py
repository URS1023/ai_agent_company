"""集成配置 DTO；持久化仅保存安全凭据引用，不接收明文令牌。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IntegrationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    engine: Literal["dify", "openwebui"]
    base_url: str = Field(min_length=1, max_length=1000)
    console_url: str = Field(min_length=1, max_length=1000)
    credential_ref: str | None = None
    dataset_credential_ref: str | None = None
    callback_secret_ref: str | None = None
    delegation_service_identity_id: str | None = Field(default=None, min_length=1, max_length=80)
    delegation_jwt_secret_ref: str | None = Field(default=None, min_length=1, max_length=80)
    chat_model_group_id: str | None = Field(default=None, min_length=1, max_length=80)
    chat_model_ids: list[str] | None = Field(default=None, max_length=32)
    enabled: bool = True
    timeout_seconds: float = Field(default=30, ge=1, le=120)


class IntegrationPatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=1, max_length=1000)
    console_url: str | None = Field(default=None, min_length=1, max_length=1000)
    credential_ref: str | None = None
    dataset_credential_ref: str | None = None
    callback_secret_ref: str | None = None
    delegation_service_identity_id: str | None = Field(default=None, min_length=1, max_length=80)
    delegation_jwt_secret_ref: str | None = Field(default=None, min_length=1, max_length=80)
    chat_model_group_id: str | None = Field(default=None, min_length=1, max_length=80)
    chat_model_ids: list[str] | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)


class BindingCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    integration_id: str
    native_id: str = Field(min_length=1, max_length=200)
    app_mode: Literal["workflow", "chatflow", "agent-chat", "agent_v2", "openwebui_model"] = "workflow"
    release_id: str | None = Field(default=None, max_length=200)
    credential_ref: str | None = None
    enabled: bool = True
    input_schema: dict[str, Any] | None = None


class BindingPatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    native_id: str | None = Field(default=None, min_length=1, max_length=200)
    release_id: str | None = Field(default=None, max_length=200)
    credential_ref: str | None = None
    enabled: bool | None = None
    input_schema: dict[str, Any] | None = None


class InvocationInput(StrictModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    query: str | None = Field(default=None, max_length=20000)
    conversation_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=160)


class ActivationInput(StrictModel):
    revision: int = Field(ge=1)
    enabled: bool


class WorkflowEventInput(StrictModel):
    automation_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=2, max_length=150)
    inputs: dict[str, Any] = Field(default_factory=dict)


class IdentityCreate(StrictModel):
    integration_id: str
    platform_user_id: str
    external_user_id: str = Field(min_length=1, max_length=200)
    external_workspace_id: str | None = None
    # 仅使用该用户专属凭据，不能退回共享管理员令牌读取私人对话。
    credential_ref: str | None = None
    enabled: bool = True


class IdentityPatch(StrictModel):
    revision: int = Field(ge=1)
    external_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    external_workspace_id: str | None = None
    credential_ref: str | None = None
    enabled: bool | None = None


class WorkspaceMapCreate(StrictModel):
    integration_id: str
    external_workspace_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class WorkspaceMapPatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    external_workspace_id: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None


class SpaceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    integration_id: str
    native_id: str = Field(min_length=1, max_length=200)
    credential_ref: str | None = None
    enabled: bool = True
    description: str = Field(default="", max_length=2000)


class SpacePatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    credential_ref: str | None = None
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=2000)


class ImportInput(StrictModel):
    file_id: str
    name: str | None = Field(default=None, max_length=200)
    indexing_technique: Literal["high_quality", "economy"] = "high_quality"


class SearchInput(StrictModel):
    space_ids: list[str] = Field(min_length=1, max_length=10)
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0, ge=0, le=1)


class ReindexInput(StrictModel):
    process_rule: dict[str, Any] = Field(default_factory=lambda: {"mode": "automatic"})


class LaunchInput(StrictModel):
    target: Literal["apps", "workflow", "agent", "agent_v2", "knowledge", "plugins", "chat", "model_chat", "models", "tools", "admin"]
    native_id: str | None = None


class ModelProfileCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    integration_id: str
    role: Literal["chat", "reasoning", "embedding", "reranker", "vision", "speech", "transcription"]
    native_model_id: str = Field(min_length=1, max_length=300)
    credential_ref: str | None = None
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelProfilePatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    native_model_id: str | None = Field(default=None, min_length=1, max_length=300)
    credential_ref: str | None = None
    enabled: bool | None = None
    parameters: dict[str, Any] | None = None


class ToolCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal["openapi", "mcp"]
    integration_id: str
    endpoint: str = Field(min_length=1, max_length=1000)
    credential_ref: str | None = None
    schema_document: dict[str, Any] | None = None
    enabled: bool = True


class ToolPatch(StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    credential_ref: str | None = None
    schema_document: dict[str, Any] | None = None


class CallbackInput(StrictModel):
    event_id: str = Field(min_length=1, max_length=160)
    run_id: str
    native_run_id: str
    sequence: int = Field(ge=0)
    status: Literal["running", "succeeded", "failed", "cancelled"]
    outputs: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=2000)
