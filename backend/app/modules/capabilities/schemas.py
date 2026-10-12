"""Strict public contracts for tools, HTTP/database/MCP connectors and capability reuse.

中文补充（接口契约层）：
- 本模块是"工具与能力中心"所有请求/响应模型的唯一来源，字段名对前端 camelCase、
  对内部 snake_case（pydantic alias），因此不要在 service 层再写取别名的兼容代码。
- 校验策略是"越界即拒绝"：工具名/权限码有正则约束、Schema 与连接配置有 128 KB 上限、
  认证配置不接受明文、分类与方法补丁必须携带 version 做乐观锁。
  这些约束是安全边界的一部分（防止超长 Schema 拖垮校验、防止重名遮蔽已发布工具），
  放宽前请先确认调用方与数据库列宽。
- 依赖：app.common.contracts.ApiModel（统一 extra 策略与别名行为）。
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel


class ToolDefinition(ApiModel):
    """单个工具的创建/更新契约（连接器版本内的一条工具定义）。

    不变式：名称必须英文开头（AI 与工作流的引用稳定性依赖它）、权限码格式受限、
    Schema 体积受限；timeout_seconds 上限 120 秒与 provider 侧超时保持一致。
    """

    name: str = Field(min_length=1, max_length=100, description="稳定工具名；发布后供 AI 和工作流引用")
    description: str = Field(default="", max_length=2000)
    input_schema: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False},
        alias="inputSchema",
    )
    output_schema: dict = Field(default_factory=dict, alias="outputSchema")
    permissions: list[str] = Field(default_factory=list, max_length=30)
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds", ge=1, le=120)
    risk_level: Literal["low", "medium", "high"] = Field(default="low", alias="riskLevel")
    requires_approval: bool = Field(default=False, alias="requiresApproval")

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """名称必须匹配 ``[A-Za-z][A-Za-z0-9_.-]{0,99}``：字母开头，便于模型稳定引用。"""
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", value):
            raise ValueError("工具名只能使用英文字母、数字、点、下划线或短横线，且必须以字母开头")
        return value

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        """权限码格式校验并去重（保持首次出现顺序）；实际存在性由 service 对白名单校验。"""
        if any(not re.fullmatch(r"[a-z][a-z0-9_.]{1,99}", item) for item in value):
            raise ValueError("工具权限编码格式不正确")
        return list(dict.fromkeys(value))

    @field_validator("input_schema", "output_schema")
    @classmethod
    def bounded_schema(cls, value: dict) -> dict:
        """限制单个 Schema 的序列化体积（128 KB），避免超长定义拖慢校验与模型上下文。"""
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 128 * 1024:
            raise ValueError("单个工具 Schema 不能超过 128 KB")
        return value


class ConnectorInput(ApiModel):
    """新建连接器的请求体：名称、类型、可见性与至少一个工具定义。

    ``provider`` 只允许 http/database/mcp（内置工具不走连接器）；
    ``visibility=private`` 表示仅创建者可见，workspace 表示工作区共享。
    """

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    provider: Literal["http", "database", "mcp"]
    visibility: Literal["private", "workspace"] = "workspace"
    config: dict = Field(default_factory=dict)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """连接器名称去空白；全空白直接拒绝（列表与日志里不允许出现无名记录）。"""
        value = value.strip()
        if not value:
            raise ValueError("连接器名称不能为空白")
        return value

    @model_validator(mode="after")
    def unique_tool_names(self):
        """同一连接器内工具名不得重复，且整体连接配置不超过 128 KB。"""
        names = [item.name for item in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("同一连接器中的工具名不能重复")
        if len(json.dumps(self.config, ensure_ascii=False).encode("utf-8")) > 128 * 1024:
            raise ValueError("连接配置不能超过 128 KB")
        return self


class ConnectorVersionInput(ConnectorInput):
    """新建连接器版本的请求体：在 ConnectorInput 基础上必须携带当前版本号做乐观锁。"""

    version: int = Field(ge=1, description="界面读取到的当前连接器版本")


class PublishInput(ApiModel):
    """发布请求：version 必须是当前最新版本号，否则要求重新加载。"""

    version: int = Field(ge=1)


class ToolArgumentsInput(ApiModel):
    """参数校验/试运行请求：直接透传待校验的参数对象。"""

    arguments: dict = Field(default_factory=dict)


class ToolCompileInput(ApiModel):
    """参数编译请求：自然语言意图 + 调用方提供的引用数据（用于 x-lookup 规范化）。"""

    intent: str = Field(min_length=1, max_length=10000)
    references: dict = Field(default_factory=dict)

    @field_validator("intent")
    @classmethod
    def clean_intent(cls, value: str) -> str:
        """去掉首尾空白；全空白视为无效（编译需要有意义的业务描述）。"""
        value = value.strip()
        if not value:
            raise ValueError("业务意图不能为空白")
        return value


class ConnectorTestInput(ApiModel):
    """连接器试运行请求：指定版本、工具名与参数（前端字段为 toolName）。"""

    version: int = Field(ge=1)
    tool_name: str = Field(alias="toolName", min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


class FromRunInput(ApiModel):
    """从运行记录沉淀能力的请求：目标类型只能是 skill 或 plugin。"""

    run_id: str = Field(alias="runId", min_length=1, max_length=26)
    target: Literal["skill", "plugin"]


class ToolCategoryInput(ApiModel):
    """新建工具分类的请求体；code 供导出与引用，name 为同级显示名。"""

    parent_id: str | None = Field(default=None, alias="parentId", max_length=26)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    sort_order: int = Field(default=0, alias="sortOrder", ge=-100000, le=100000)
    enabled: bool = True

    @field_validator("code", "name")
    @classmethod
    def clean_category_text(cls, value: str) -> str:
        """分类编码与名称去空白；两者都不能为空白（否则列表里会出现空名称节点）。"""
        value = value.strip()
        if not value:
            raise ValueError("分类编码和名称不能为空白")
        return value


class ToolCategoryPatch(ApiModel):
    """修改工具分类的请求体：全量字段覆盖 + version 乐观锁。"""

    version: int = Field(ge=1)
    parent_id: str | None = Field(default=None, alias="parentId", max_length=26)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    sort_order: int = Field(default=0, alias="sortOrder", ge=-100000, le=100000)
    enabled: bool = True

    @field_validator("code", "name")
    @classmethod
    def clean_category_patch_text(cls, value: str) -> str:
        """与新建分类保持同一套清洗规则，避免"改后变空白"绕过约束。"""
        value = value.strip()
        if not value:
            raise ValueError("分类编码和名称不能为空白")
        return value


class ToolMethodPatch(ApiModel):
    """工具方法治理信息补丁：分类、展示名、启停与标签。

    ``version`` 默认 0 表示"该方法还没有治理元数据行"，此时 service 会新建；
    已有元数据必须传当前版本号，避免基于过期视图覆盖他人改动。
    """

    version: int = Field(default=0, ge=0, description="未配置的方法传 0，已配置的方法传当前版本")
    category_id: str | None = Field(default=None, alias="categoryId", max_length=26)
    code: str = Field(default="", max_length=100, pattern=r"^$|^[A-Za-z][A-Za-z0-9_.-]*$")
    display_name: str = Field(default="", alias="displayName", max_length=200)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("code", "display_name", "description")
    @classmethod
    def clean_method_text(cls, value: str) -> str:
        """统一去空白；空字符串表示"沿用原值"（service 对 code 有 ``or tool_name`` 兜底）。"""
        return value.strip()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        """标签去空白、去重、单个不超过 60 字符（避免变成变相的描述字段）。"""
        result = []
        for item in value:
            item = item.strip()
            if item and item not in result:
                if len(item) > 60:
                    raise ValueError("单个标签不能超过 60 个字符")
                result.append(item)
        return result


class ToolMethodBatchItem(ToolMethodPatch):
    """批量改方法时的单条：在补丁基础上必须指明工具名（前端字段 toolName）。"""

    tool_name: str = Field(alias="toolName", min_length=1, max_length=100)


class ToolMethodBatchInput(ApiModel):
    """批量改方法请求：1–200 条，整批原子生效或整体回滚。"""

    items: list[ToolMethodBatchItem] = Field(min_length=1, max_length=200)


class ConnectorCloneInput(ApiModel):
    """复制连接器请求：新名称（留空则由 service 生成"源名称 副本"）与工具名前缀。"""

    name: str = Field(default="", max_length=200)
    tool_prefix: str = Field(
        default="", alias="toolPrefix", max_length=40,
        pattern=r"^$|^[A-Za-z][A-Za-z0-9_.-]*$",
        description="为复制出来的方法增加唯一前缀；留空时由系统生成",
    )


class ConnectorShareInput(ApiModel):
    """修改连接器共享范围的请求：version 乐观锁 + 目标可见性（private/workspace）。"""

    version: int = Field(ge=1)
    visibility: Literal["private", "workspace"]
