"""Versioned business contracts; identity comes from the server, never a command body.

JSON documents are copied at serialization boundaries. Repository adapters must persist
canonical snapshots rather than retain mutable references to a validated request.
"""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

type JsonObject = dict[str, JsonValue]
type Scenario = Literal["alert", "quality"]
type WorkspaceRole = Literal["owner", "admin", "editor", "normal", "dataset_operator"]
type Action = Literal["read", "manage", "run", "review"]
type RunStatus = Literal["queued", "claimed", "dispatched", "uncertain", "succeeded", "failed", "cancelled"]
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_command_document(value: JsonObject) -> JsonObject:
    """Configuration references credentials; it never embeds credential-shaped fields."""
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > 262144:
        raise ValueError("Command document exceeds 256 KiB")
    stack: list[tuple[JsonValue, int]] = [(value, 0)]
    credential_keys = {
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "client_secret",
        "private_key",
    }
    while stack:
        item, depth = stack.pop()
        if depth > 32:
            raise ValueError("Command document nesting exceeds the limit")
        if isinstance(item, dict):
            if any(key.lower().replace("-", "_") in credential_keys for key in item):
                raise ValueError("Credentials must be referenced, not embedded")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Principal(Contract):
    actor_id: Identifier
    workspace_id: Identifier
    workspace_role: WorkspaceRole
    display_name: str

    def can(self, action: Action) -> bool:
        """Initial server-side policy; authentication alone never grants management."""
        if action == "read":
            return True
        if action == "review":
            return self.workspace_role in {"owner", "admin"}
        return self.workspace_role in {"owner", "admin", "editor"}


class DeviceCreate(Contract):
    device_code: Identifier
    name: str = Field(min_length=1, max_length=200)
    department: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)


class DeviceUpdate(DeviceCreate):
    """Complete editable representation used by revision-checked PUT, not a patch."""


class Device(DeviceCreate):
    id: Identifier
    workspace_id: Identifier
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class BindingWrite(Contract):
    app_id: Identifier
    workflow_id: UUID
    specification_revision: Identifier
    secret_ref: Identifier
    source_id: Identifier
    source_revision: Identifier
    read_id: Identifier
    read_revision: Identifier
    manifest: JsonObject = Field(default_factory=dict)

    @field_validator("manifest")
    @classmethod
    def validate_manifest(cls, value: JsonObject) -> JsonObject:
        return validate_command_document(value)


class Binding(BindingWrite):
    id: Identifier
    workspace_id: Identifier
    device_id: Identifier
    scenario: Scenario
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    active_run_id: str | None = None


class RunSpec(BindingWrite):
    binding_id: Identifier
    binding_revision: int = Field(ge=1)
    device_id: Identifier
    scenario: Scenario
    parameters: JsonObject = Field(default_factory=dict)
    recompute_of: Identifier | None = None

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: JsonObject) -> JsonObject:
        return validate_command_document(value)

    @classmethod
    def from_binding(cls, binding: Binding) -> "RunSpec":
        return cls(
            **binding.model_dump(
                exclude={"id", "workspace_id", "revision", "created_at", "updated_at", "active_run_id"}
            ),
            binding_id=binding.id,
            binding_revision=binding.revision,
        )


class BusinessResult(Contract):
    scenario: Scenario
    conclusion: Literal["normal", "issues", "no_data", "incomplete", "passed", "failed", "review"]
    complete: StrictBool
    evidence: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_verdict(self) -> "BusinessResult":
        allowed = (
            {"normal", "issues", "no_data", "incomplete"}
            if self.scenario == "alert"
            else {"passed", "failed", "review", "incomplete"}
        )
        if self.conclusion not in allowed:
            raise ValueError("Conclusion does not belong to this scenario")
        if self.conclusion in {"normal", "passed", "review"} and not self.complete:
            raise ValueError("A normal, passed or review result requires complete evidence")
        if self.conclusion in {"no_data", "incomplete"} and self.complete:
            raise ValueError("Missing data is not complete evidence")
        canonical_json(self.evidence)
        return self


class Run(Contract):
    id: Identifier
    workspace_id: Identifier
    actor_id: Identifier
    request_key: Identifier
    payload_hash: str
    spec: RunSpec
    input_snapshot: JsonObject | None = None
    status: RunStatus
    dispatch_nonce: str | None = None
    dify_run_id: str | None = None
    result: BusinessResult | None = None
    result_digest: str | None = None
    reason_code: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AuditEvent(Contract):
    sequence: int
    workspace_id: Identifier
    resource_id: Identifier
    run_id: str | None = None
    actor_id: Identifier
    action: str
    data: JsonObject = Field(default_factory=dict)
    created_at: AwareDatetime


class Page[T](Contract):
    items: tuple[T, ...]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
