# -*- coding: utf-8 -*-
"""Tool abstraction: ToolSpec (unified contract) and ToolResult.

工具层的统一数据契约：所有 provider（http / mcp / database / builtin / sandbox）
与 ToolGateway 之间只通过 ToolSpec（声明）与 ToolResult（结果）交互，不直接传递
各自的连接配置或 SDK 对象。

关键约束（改动前必读）：
- ``ToolSpec.permissions`` 是执行期必须逐项校验的权限声明，不是描述性元数据：
  网关在 resolve 与 execute 两个入口都会用它做鉴权，缺失会导致越权执行。
- ``provider`` 取值限定为 http | mcp | database | builtin | sandbox 五类，
  网关按该字段分发到对应 provider 模块的 ``invoke()``；新增 provider 需同步
  在 ``gateway._PROVIDERS`` 注册。
- 连接器类工具的 ``connector_id`` / ``connector_version_id`` / ``tool_id``
  由网关在 resolve 阶段回填，数据库里持久化的 spec 中可以缺省，不要据此做判空。
- 本模块只做数据承载，不含任何鉴权或 I/O 副作用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolSpec:
    """工具能力声明（不可执行的纯数据对象）。

    代表"某个工具对模型暴露什么、执行时要求什么权限"，由内置 provider 硬编码或
    由连接器版本（ConnectorVersion.spec）中保存的 JSON 反序列化而来。

    生命周期：由 gateway.resolve / list_available 构造 → 交给
    validator.validate_arguments 校验入参 → 交给 provider.invoke 执行。
    网关还会在实例上挂 ``_connector_spec`` / ``_tool_id`` 等私有属性传递上下文，
    这些字段不在 to_dict 中，不会被持久化或返回给模型。

    关键不变式：``permissions`` 必须覆盖该工具实际会触达的资源类型（数据源、
    连接器等），否则 require_resource 阶段会漏判。
    """

    name: str
    description: str
    provider: str  # http | mcp | database | builtin | sandbox
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    timeout_seconds: int = 120
    retry_policy: dict = field(default_factory=dict)
    version: int = 1
    risk_level: str = "low"
    requires_approval: bool = False
    connector_id: Optional[str] = None
    connector_version_id: Optional[str] = None
    tool_id: Optional[str] = None

    def to_dict(self) -> dict:
        """序列化为可持久化的 dict，键名与前端/连接器编辑器可见字段保持一致。"""
        return {
            "name": self.name,
            "description": self.description,
            "provider": self.provider,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": self.permissions,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": self.retry_policy,
            "version": self.version,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "connector_id": self.connector_id,
            "connector_version_id": self.connector_version_id,
            "tool_id": self.tool_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolSpec":
        """从持久化 dict 还原；字段缺失或为 None 时回落到默认值。

        历史数据可能把 ``input_schema`` 存成 null、把 ``timeout_seconds`` 存成
        字符串，因此这里统一做 ``or {}`` / ``int()`` 兼容，调用方不必再判空。
        """
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            provider=d.get("provider", "builtin"),
            input_schema=d.get("input_schema", {}) or {},
            output_schema=d.get("output_schema", {}) or {},
            permissions=d.get("permissions", []) or [],
            timeout_seconds=int(d.get("timeout_seconds", 120) or 120),
            retry_policy=d.get("retry_policy", {}) or {},
            version=int(d.get("version", 1) or 1),
            risk_level=d.get("risk_level", "low"),
            requires_approval=bool(d.get("requires_approval", False)),
            connector_id=d.get("connector_id"),
            connector_version_id=d.get("connector_version_id"),
            tool_id=d.get("tool_id"),
        )

    def to_openai_function(self) -> dict:
        """转换为 OpenAI function-calling 工具描述，供模型侧工具列表使用。

        只有 name / description / input_schema 会进入模型上下文；
        权限、超时、风险等级等治理字段不下发，避免模型据此做绕过尝试。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolResult:
    """工具执行结果（网关归一化后的统一形态）。

    ``status`` 是模型与编排层唯一可依赖的状态位：success | running | failed |
    cancelled；其中 running 表示异步任务已受理但未完成（如轮询型 MCP 调用）。

    失败结果不抛异常，而是带 ``error_code`` / ``error_summary`` 返回，便于模型
    看到失败原因后自行重试或改参数；只有参数校验失败与用户取消会向上抛。
    """

    status: str  # success | running | failed | cancelled
    data: Any = None
    artifacts: List[dict] = field(default_factory=list)  # [{type,name,url,storage_key...}]
    metadata: dict = field(default_factory=dict)
    error_code: Optional[str] = None
    error_summary: Optional[str] = None

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的 dict，作为最终回传给模型的工具消息体。"""
        return {
            "status": self.status,
            "data": self.data,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
            "error_code": self.error_code,
            "error_summary": self.error_summary,
        }
