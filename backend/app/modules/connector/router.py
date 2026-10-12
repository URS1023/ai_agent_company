# -*- coding: utf-8 -*-
"""旧版连接器别名统一委托严格的 v1 能力服务，保留客户端地址与响应兼容。

旧入口不得保存能力中心会拒绝的配置。

- 路由前缀 ``/api/connectors``，全部端点标记 deprecated=True，仅用于兼容老客户端；
  新代码请使用 /api/v1/capabilities 下的对应接口。
- 兼容策略的关键点：本层**不自己写库**，而是把请求转换成 capabilities 的严格模型
  （ConnectorInput / ConnectorVersionInput）后交给同一套 service，
  因此旧客户端也无法通过这里保存管理台会拒绝的配置（地址换主机、明文凭据等）。
- derive 端点：先检查创建权限，再调用 AI 推导草稿并严格校验；不支持的方法/映射明确拒绝，
  不悄悄改写原接口。认证固定为 none（AI 输出永不被当作凭据），allowPrivate 固定 False。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError as ModelValidationError, field_validator

from ...common.authorization import require_permission
from ...common.deps import AuthContext, get_current_context
from ...errors import ValidationError
from ..capabilities import service as capability_service
from ..capabilities.schemas import ConnectorInput, ConnectorVersionInput
from .derive import derive_definition

router = APIRouter(prefix="/api/connectors", tags=["connectors (legacy)"], deprecated=True)


class ConnectorCreate(BaseModel):
    """旧版创建请求：扁平字段 + 原始 connector_spec（内部会转成严格的 v1 模型）。"""

    name: str
    provider: str
    connector_spec: dict = Field(default_factory=dict)
    tools: List[dict] = Field(default_factory=list)
    data_source_id: Optional[str] = None


class DeriveRequest(BaseModel):
    """AI 推导请求：粘贴的接口说明文本（1–20000 字符）。"""

    description: str = Field(min_length=1, max_length=20_000)

    @field_validator("description")
    @classmethod
    def meaningful_description(cls, value: str) -> str:
        """空白说明不能触发模型调用。"""
        if not value.strip():
            raise ValueError("接口说明不能为空白")
        return value.strip()


class TestInvoke(BaseModel):
    """旧版试调用请求：工具名与参数。"""

    tool_name: str
    arguments: dict = Field(default_factory=dict)


class NewVersion(BaseModel):
    """旧版新建版本请求：仅提交新的连接配置（工具沿用当前版本）。"""

    connector_spec: dict


def _database_tools() -> list[dict]:
    """数据库连接器的三个固定只读工具定义（旧版接口的内置默认值）。

    字段名用 inputSchema（camelCase）以匹配旧客户端的期望；additionalProperties=False
    与新版保持一致，避免旧接口成为夹带额外参数的旁路。
    """
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return [
        {"name": "database.list_tables", "description": "列出可访问表", "inputSchema": empty},
        {"name": "database.describe_table", "description": "查看表字段", "inputSchema": {
            "type": "object", "properties": {"table": {"type": "string"}},
            "required": ["table"], "additionalProperties": False,
        }},
        {"name": "database.query", "description": "执行只读 SQL", "inputSchema": {
            "type": "object", "properties": {"sql": {"type": "string"}},
            "required": ["sql"], "additionalProperties": False,
        }},
    ]


def _legacy_result(record: dict) -> dict:
    """Preserve historical IDs while also returning the redacted v1 record.

    中文补充：旧客户端只认 connector_id / connector_version_id / tools 三个键，
    因此从 v1 版本视图里抽出这些 ID（取最新版本），同时把完整的脱敏记录放在 record 里，
    让新旧调用方都能用同一份响应。
    """
    latest = record.get("versions", [{}])[0]
    return {
        "connector_id": record["id"],
        "connector_version_id": latest.get("id"),
        "tools": [
            {"tool_id": item.get("id"), "tool_version_id": item.get("versionId"), "name": item.get("name")}
            for item in latest.get("tools", [])
        ],
        "record": record,
    }


def _tools_from_version(version: dict) -> list[dict]:
    """把 v1 版本视图里的工具还原成旧版请求所需的 inputSchema 形态（新建版本时复用）。"""
    tools = []
    for item in version.get("tools", []):
        spec = item.get("spec") or {}
        tools.append({
            "name": item.get("name"),
            "description": item.get("description") or "",
            "inputSchema": spec.get("inputSchema") or {
                "type": "object", "properties": {}, "additionalProperties": False,
            },
            "outputSchema": spec.get("outputSchema") or {},
            "permissions": spec.get("permissions") or [],
            "timeoutSeconds": spec.get("timeoutSeconds") or 30,
            "riskLevel": spec.get("riskLevel") or "low",
            "requiresApproval": bool(spec.get("requiresApproval")),
        })
    return tools


@router.post("", status_code=201)
def create(payload: ConnectorCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/connectors：旧版创建连接器（201），返回旧版 ID 结构 + 新版脱敏记录。

    provider=database 时忽略 connector_spec，改用 data_source_id 构造配置并使用内置只读工具集。
    """
    config = {"dataSourceId": payload.data_source_id} if payload.provider == "database" else payload.connector_spec
    tools = payload.tools or (_database_tools() if payload.provider == "database" else [])
    strict = ConnectorInput.model_validate({
        "name": payload.name, "provider": payload.provider, "visibility": "workspace",
        "config": config, "tools": tools,
    })
    return _legacy_result(capability_service.create_connector(ctx, strict))


@router.post("/derive", status_code=201)
def derive(payload: DeriveRequest, ctx: AuthContext = Depends(get_current_context)):
    """先验证创建权限，再生成草稿并采用与手工创建相同的确定性校验。

    GET/POST、json/query 与 1–120 秒范围外的定义明确返回422，不修改原接口语义。
    认证固定 none，密钥由用户后续显式绑定；allowPrivate 固定 False。
    返回 {"plan": AI 原始结果, "created": 旧版 ID 结构}，创建结果仍是草稿。
    """
    # 旧 HTTP 入口已有中间件保护；本地显式校验保证内部复用时也不会先发送接口说明。
    require_permission(ctx, "connectors.create")
    plan = derive_definition(ctx.workspace_id, payload.description.strip())
    method = str(plan.get("method") or "POST").upper()
    body_mode = str(plan.get("body_mapping") or "json").lower()
    timeout = int(plan.get("timeout_seconds") or 30)
    if method not in {"GET", "POST"}:
        raise ValidationError("HTTP 工具目前只支持 GET/POST，不能改写原接口方法")
    if body_mode not in {"json", "query"}:
        raise ValidationError("HTTP 工具目前只支持 json/query 映射，不能改写原接口请求体")
    tools = [{
        "name": item.get("name"), "description": item.get("description") or "",
        "inputSchema": item.get("input_schema") or {
            "type": "object", "properties": {}, "additionalProperties": False,
        },
        "outputSchema": item.get("output_schema") or {},
        "timeoutSeconds": timeout,
    } for item in plan.get("tools", [])]
    try:
        strict = ConnectorInput.model_validate({
            "name": plan.get("name") or "新连接器", "description": plan.get("description") or "",
            "provider": "http", "visibility": "workspace",
            "config": {
                "baseUrl": plan.get("base_url") or "", "path": plan.get("path") or "/",
                "method": method, "bodyMode": body_mode, "headers": {},
                # 模型输出不能作为凭据，用户必须另外显式绑定密钥记录。
                "auth": {"type": "none"}, "timeoutSeconds": timeout,
                "allowPrivate": False,
            },
            "tools": tools,
        })
    except ModelValidationError as exc:
        fields = [".".join(map(str, item["loc"])) for item in exc.errors(include_input=False)][:10]
        raise ValidationError("AI 推导的连接器未通过字段校验：" + "、".join(fields)) from None
    record = capability_service.create_connector(ctx, strict)
    return {"plan": plan, "created": _legacy_result(record)}


@router.get("")
def list_all(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/connectors：旧版列表（最多 200 条），返回 items + count。"""
    result = capability_service.list_connectors(ctx, limit=200)
    return {"items": result["items"], "count": result["total"]}


@router.get("/{connector_id}")
def detail(connector_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/connectors/{connector_id}：详情（含版本与脱敏配置），越权/不存在返回 404。"""
    return capability_service.get_connector(ctx, connector_id)


@router.post("/{connector_id}/publish")
def publish(connector_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/connectors/{connector_id}/publish：发布当前最新版本。

    版本号取自服务端读到的最新值（旧客户端无需传 version）；仍需 connectors.publish 权限。
    """
    record = capability_service.get_connector(ctx, connector_id)
    latest = record["versions"][0]
    published = capability_service.publish_connector(ctx, connector_id, latest["version"])
    return {"ok": True, "connector_id": connector_id, "status": "published", "record": published}


@router.post("/{connector_id}/versions")
def new_version(connector_id: str, payload: NewVersion, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/connectors/{connector_id}/versions：按提交的连接配置创建新草稿版本。

    名称/可见性沿用当前连接器，工具定义从当前最新版本还原（旧版接口不支持改工具）；
    版本号用服务端最新值做乐观锁。
    """
    current = capability_service.get_connector(ctx, connector_id)
    latest = current["versions"][0]
    strict = ConnectorVersionInput.model_validate({
        "name": current["name"], "provider": current["provider"], "visibility": current["visibility"],
        "version": current["version"], "config": payload.connector_spec,
        "tools": _tools_from_version(latest),
    })
    record = capability_service.create_version(ctx, connector_id, strict)
    return _legacy_result(record)


@router.post("/{connector_id}/test-invoke")
def test_invoke(connector_id: str, payload: TestInvoke, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/connectors/{connector_id}/test-invoke：试调用最新版本中的工具。

    与正式调用同构（同网关、同权限、同 Schema 校验），并会留下 Run/Step/ToolCall 追踪记录。
    """
    current = capability_service.get_connector(ctx, connector_id)
    return capability_service.test_connector(
        ctx, connector_id, version_number=current["version"],
        tool_name=payload.tool_name, arguments=payload.arguments,
    )


@router.delete("/{connector_id}")
def delete(connector_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/connectors/{connector_id}：归档连接器（软删除，保留版本与调用历史）。"""
    current = capability_service.get_connector(ctx, connector_id)
    capability_service.archive_connector(ctx, connector_id, current["version"])
    return {"ok": True, "connector_id": connector_id, "status": "archived", "history_retained": True}
