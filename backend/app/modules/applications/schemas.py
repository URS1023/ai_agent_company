"""Strict public contracts for application composition and publication."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel


ResourceType = Literal["bot", "workflow", "dashboard", "plugin", "skill", "knowledge", "datasource", "document"]
ApplicationKind = Literal["assistant", "automation", "dashboard", "solution"]


def _validate_schema(value: dict) -> dict:
    """校验启动入参 schema 的合法性（类型/必填/嵌套结构），返回规范化结果。异常：422。"""
    if not isinstance(value, dict) or value.get("type", "object") != "object":
        raise ValueError("应用输入输出 Schema 根节点必须是 object")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 128 * 1024:
        raise ValueError("应用 Schema 不能超过 128 KB")
    pending = [(value, 0)]
    seen = 0
    while pending:
        node, depth = pending.pop()
        seen += 1
        if seen > 5000 or depth > 24:
            raise ValueError("应用 Schema 节点过多或嵌套过深")
        if not isinstance(node, dict):
            raise ValueError("应用 Schema 节点必须是对象")
        if "$ref" in node:
            raise ValueError("应用 Schema 不允许外部或递归 $ref")
        properties = node.get("properties") or {}
        if not isinstance(properties, dict) or len(properties) > 300:
            raise ValueError("应用 Schema 的 properties 格式不正确")
        for name, child in properties.items():
            if not isinstance(name, str) or not name or name in {"__proto__", "prototype", "constructor"}:
                raise ValueError("应用 Schema 含不安全字段名")
            pending.append((child, depth + 1))
        if isinstance(node.get("items"), dict):
            pending.append((node["items"], depth + 1))
        for keyword in ("anyOf", "oneOf", "allOf"):
            branches = node.get(keyword) or []
            if branches and (not isinstance(branches, list) or len(branches) > 20):
                raise ValueError(f"应用 Schema 的 {keyword} 格式不正确")
            pending.extend((child, depth + 1) for child in branches)
    return value


class ApplicationBinding(ApiModel):
    """应用绑定的一项资源：类型（bot/workflow/...）、id、角色（入口或辅助）、是否必填与用途说明。"""
    type: ResourceType
    id: str = Field(min_length=1, max_length=80)
    role: Literal["entry", "dependency"] = "dependency"
    purpose: str = Field(default="", max_length=500)
    required: bool = True


class ApplicationAudience(ApiModel):
    """发布受众：可见范围为指定受众时，哪些用户与角色可以访问该应用。"""
    mode: Literal["workspace", "restricted", "private"] = "workspace"
    role_ids: list[str] = Field(default_factory=list, alias="roleIds", max_length=100)
    user_ids: list[str] = Field(default_factory=list, alias="userIds", max_length=200)

    @model_validator(mode="after")
    def unique_targets(self):
        """校验受众的目标（用户/角色）不重复，避免同一主体出现多条受众记录。异常：422。"""
        self.role_ids = list(dict.fromkeys(self.role_ids))
        self.user_ids = list(dict.fromkeys(self.user_ids))
        if self.mode == "restricted" and not (self.role_ids or self.user_ids):
            raise ValueError("受限应用至少选择一个角色或用户")
        if self.mode != "restricted" and (self.role_ids or self.user_ids):
            raise ValueError("仅受限应用可以配置角色或用户名单")
        return self


class ApplicationSpec(ApiModel):
    """应用规格：绑定资源 + 受众 + 工作流修订冻结信息，是版本不可变内容的载体。"""
    workflow_revisions: dict[str, int] = Field(default_factory=dict, alias="workflowRevisions", description="发布时由服务端固定的工作流修订")
    welcome_message: str = Field(default="", alias="welcomeMessage", max_length=1000)
    instructions: str = Field(default="", max_length=20000)
    bindings: list[ApplicationBinding] = Field(min_length=1, max_length=50)
    audience: ApplicationAudience = Field(default_factory=ApplicationAudience)
    input_schema: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False},
        alias="inputSchema",
    )
    output_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}}, alias="outputSchema")

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value: dict) -> dict:
        """校验入参 schema 合法（必须为对象类型等），返回规范化结果。异常：422。"""
        return _validate_schema(value)

    @model_validator(mode="after")
    def valid_bindings(self):
        """校验绑定列表：类型必须在支持集合内、同一 (类型,id) 不重复、入口资源唯一。异常：422。"""
        keys = [(item.type, item.id) for item in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("同一资源不能重复绑定")
        entries = [item for item in self.bindings if item.role == "entry"]
        if len(entries) != 1:
            raise ValueError("应用必须且只能指定一个启动入口")
        if entries[0].type not in {"bot", "workflow", "dashboard", "plugin"}:
            raise ValueError("启动入口只支持 Bot、工作流、大屏或 Plugin")
        return self


class ApplicationInput(ApiModel):
    """应用新建/新建版本入参：code、名称、类型、可见性、规格与受众。"""
    code: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    kind: ApplicationKind = "solution"
    visibility: Literal["workspace", "restricted", "private"] = "workspace"
    spec: ApplicationSpec
    version: int | None = Field(default=None, ge=1, description="界面读取到的应用记录乐观锁版本")

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        """校验应用 code 格式（小写字母/数字/短横线），保证它可作为稳定的外部标识被引用。异常：422。"""
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,79}", value):
            raise ValueError("应用编码须以英文字母开头，只能包含小写字母、数字、下划线或短横线")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """清洗应用名称（去首尾空白），空名报错。异常：422。"""
        value = value.strip()
        if not value:
            raise ValueError("应用名称不能为空白")
        return value

    @model_validator(mode="after")
    def audience_matches_visibility(self):
        """跨字段校验：可见性为 workspace 时不应再配受众，为指定受众时必须给出受众。异常：422。"""
        if self.spec.audience.mode != self.visibility:
            raise ValueError("应用可见范围必须与发布受众一致")
        return self


class PublishInput(ApiModel):
    """发布入参：记录乐观锁 + 目标发布版本号（防止把过期草稿当成最新版发布）。"""
    version: int = Field(ge=1, description="应用记录乐观锁版本")
    release_version: int = Field(alias="releaseVersion", ge=1, description="要发布的最新应用版本")


class RecordVersionInput(ApiModel):
    """停用/归档等操作入参：只带记录乐观锁 version。"""
    version: int = Field(ge=1)


class CloneInput(ApiModel):
    """复制应用入参：新 code 与新名称。"""
    name: str = Field(default="", max_length=200)
    code: str = Field(min_length=3, max_length=80)

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        """校验复制应用时的新 code（与新建同一套格式规则），并统一转小写。异常：422。"""
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,79}", value):
            raise ValueError("应用编码格式不正确")
        return value


class LaunchInput(ApiModel):
    """启动应用入参：入参对象、渠道标识、是否确认执行写操作、以及可选的幂等键。
    confirmWrites 是写操作的显式确认开关，避免误触发有副作用的资源。
    """
    channel: Literal["ui", "ai", "api"] = "ui"
    input: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    confirm_writes: bool = Field(default=False, alias="confirmWrites", strict=True)

    @field_validator("input")
    @classmethod
    def bounded_input(cls, value: dict) -> dict:
        """限制启动入参体积/层数，防止超大 JSON 打爆运行记录与下游工作流。异常：422。"""
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise ValueError("应用启动参数不能超过 64 KB")
        return value
