# -*- coding: utf-8 -*-
"""Connector service: create versioned connectors + tools, publish, test invoke.

中文补充（连接器服务——较早期的简版实现）：
- 与 modules/capabilities.service 的关系：两者操作同一批表（Connector/
  ConnectorVersion/Tool/ToolVersion），本模块提供更轻的创建-发布-试调用流程，
  被"运行时注册工具"（builtin_provider._add_tool）等内部路径使用；
  管理台的完整校验流程（配置白名单、权限码白名单、名称冲突检查）在 capabilities 侧，
  因此外部请求不要走这里。
- 发布语义：published_version_id 决定工具网关解析哪个版本；改配置必须新建版本，
  已发布版本不可覆盖。
- 试调用 test_invoke 与生产同构：构造 run_id=test_* 的上下文交给统一网关，
  仍会经过权限与 Schema 校验，只是不落 Run/Step 追踪（trace 由 capabilities 侧提供）。
- 权限：本模块只做工作区隔离，不重复做资源级鉴权；调用方须已鉴权。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError, ValidationError
from ...ids import new_id
from ...models import Connector, ConnectorVersion, Tool as ToolRow, ToolVersion
from ...providers.tool.base import ToolSpec
from ...providers.tool.gateway import ToolGateway, build_context

logger = logging.getLogger("connector")

_ROUTINES = [
    # 数据库连接器固定暴露的三个只读方法；description 会直接进入模型工具列表，
    # 因此措辞里明确"只读"，避免模型尝试写入。真正的只读保证在 provider/SQL 授权层。
    {"name": "database.list_tables",
     "description": "列出当前数据库连接中的所有表名。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "database.describe_table",
     "description": "查看指定表的字段结构（列名、类型、是否可空）。",
     "input_schema": {"type": "object",
                      "properties": {"table": {"type": "string", "description": "表名"}},
                      "required": ["table"]}},
    {"name": "database.query",
     "description": "对数据库执行只读 SQL 查询（仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH）。返回列与行数据。",
     "input_schema": {"type": "object",
                      "properties": {"sql": {"type": "string", "description": "只读 SQL，例如 SELECT * FROM ..."}},
                      "required": ["sql"]}},
]

DATABASE_TOOLS = [dict(t) for t in _ROUTINES]


def _now():
    """统一的 UTC 写入时间。"""
    return datetime.utcnow()


def latest_connector_version(db: Session, connector_id: str) -> ConnectorVersion:
    """取连接器最新版本（version_number 最大）；无版本返回 None。"""
    return db.execute(select(ConnectorVersion).where(ConnectorVersion.connector_id == connector_id)
                      .order_by(ConnectorVersion.version_number.desc()).limit(1)).scalar_one_or_none()


def latest_tool_versions(db: Session, connector_version_id: str):
    """取某连接器版本下的全部工具版本（按工具版本号倒序）。"""
    return db.execute(select(ToolVersion).where(ToolVersion.connector_version_id == connector_version_id)
                      .order_by(ToolVersion.version_number.desc())).scalars().all()


def create_connector(db: Session, *, workspace_id: str, owner_user_id: str, name: str,
                     provider: str, connector_spec: dict, tools: Optional[List[dict]] = None,
                     visibility: str = "workspace") -> dict:
    """创建连接器草稿 + 第 1 个草稿版本 + 工具定义（不 commit，由调用方提交）。

    参数：``connector_spec`` 是 provider 侧的连接配置（http/database/mcp 各自结构）；
    ``tools`` 每项 name/description/input_schema；无 name 的条目会被跳过。
    工具超时优先取工具级配置，其次取连接配置里同名字段的兜底值。
    返回 {"connector_id","connector_version_id","tools":[{tool_id,tool_version_id,name}]}。
    异常：provider 不在支持集合内抛 ValidationError。
    """
    if provider not in ("http", "database", "mcp", "builtin", "sandbox"):
        raise ValidationError(f"不支持的 Connector provider: {provider}")
    connector = Connector(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
                          name=name, provider=provider, visibility=visibility, status="draft",
                          created_at=_now(), updated_at=_now())
    db.add(connector)
    db.flush()
    cv = ConnectorVersion(id=new_id(), connector_id=connector.id, version_number=1,
                          status="draft", spec=connector_spec or {},
                          created_by=owner_user_id, created_at=_now())
    db.add(cv)
    db.flush()
    tool_specs = []
    for t in (tools or []):
        tname = t.get("name", "")
        if not tname:
            continue
        tool = ToolRow(id=new_id(), workspace_id=workspace_id, connector_id=connector.id,
                       name=tname, description=t.get("description", ""), status="draft",
                       created_at=_now(), updated_at=_now())
        db.add(tool)
        db.flush()
        tv = ToolVersion(
            id=new_id(), tool_id=tool.id, connector_version_id=cv.id, version_number=1,
            status="draft",
            spec={"name": tname, "description": t.get("description", ""),
                  "provider": provider,
                  "input_schema": t.get("input_schema") or {"type": "object", "properties": {}},
                  "output_schema": t.get("output_schema") or {},
                  "timeout_seconds": int(t.get("timeout_seconds") or connector_spec.get("timeout_seconds") or 120),
                  "risk_level": t.get("risk_level", "low"),
                  "retry_policy": {}},
            created_by=owner_user_id, created_at=_now())
        db.add(tv)
        db.flush()
        tool_specs.append({"tool_id": tool.id, "tool_version_id": tv.id, "name": tname})
    db.flush()
    return {"connector_id": connector.id, "connector_version_id": cv.id, "tools": tool_specs}


def create_database_connector(db: Session, *, workspace_id: str, owner_user_id: str,
                              name: str, data_source_id: str) -> dict:
    """快捷创建数据库连接器：绑定数据源并带上三个固定只读方法（list_tables/describe_table/query）。"""
    spec = {"data_source_id": data_source_id,
            "description": "通过只读数据库连接提供查询能力（SELECT/SHOW/DESCRIBE）"}
    return create_connector(db, workspace_id=workspace_id, owner_user_id=owner_user_id,
                            name=name, provider="database", connector_spec=spec,
                            tools=DATABASE_TOOLS)


def get_connector(db: Session, workspace_id: str, connector_id: str) -> Connector:
    """按 ID 取连接器并校验工作区归属；不存在或跨工作区统一抛 NotFoundError。"""
    row = db.get(Connector, connector_id)
    if not row or row.workspace_id != workspace_id:
        raise NotFoundError("Connector 不存在")
    return row


def list_connectors(db: Session, workspace_id: str):
    """列出本工作区全部连接器（含草稿与已归档，按创建时间倒序）；过滤由调用方决定。"""
    return db.execute(select(Connector).where(Connector.workspace_id == workspace_id)
                      .order_by(Connector.created_at.desc())).scalars().all()


def publish(db: Session, connector_id: str, user_id: str | None = None) -> None:
    """发布最新版本：连接器与本次版本内全部工具（及其 Tool 行）一起置为 published。

    只有最新版本可发布（历史草稿无法直接上线）；发布即 commit。
    发布后网关只解析 published_version_id 指向的版本，旧版本仅用于历史追溯。
    """
    connector = db.get(Connector, connector_id)
    if not connector:
        raise NotFoundError("Connector 不存在")
    cv = latest_connector_version(db, connector_id)
    if not cv:
        raise ValidationError("连接器没有版本")
    cv.status = "published"
    cv.published_at = _now()
    connector.status = "published"
    connector.published_version_id = cv.id
    connector.updated_at = _now()
    for tv in latest_tool_versions(db, cv.id):
        tv.status = "published"
        tv.published_at = _now()
        tool = db.get(ToolRow, tv.tool_id)
        if tool:
            tool.status = "published"
            tool.published_version_id = tv.id
            tool.updated_at = _now()
    db.commit()


def new_draft_version(db: Session, connector_id: str, user_id: str, connector_spec: dict) -> ConnectorVersion:
    """基于最新版本追加新草稿版本（记录 based_on_version_id），并把连接器退回 draft。

    本函数内部 commit 并返回新版本行；不含工具定义（工具由 capabilities 侧的完整流程维护）。
    """
    connector = db.get(Connector, connector_id)
    if not connector:
        raise NotFoundError("Connector 不存在")
    cv = latest_connector_version(db, connector_id)
    next_ver = (cv.version_number + 1) if cv else 1
    base = cv.id if cv else None
    new_cv = ConnectorVersion(id=new_id(), connector_id=connector_id, version_number=next_ver,
                              based_on_version_id=base, status="draft", spec=connector_spec,
                              created_by=user_id, created_at=_now())
    db.add(new_cv)
    db.flush()
    connector.status = "draft"
    db.commit()
    return new_cv


def test_invoke(db: Session, *, workspace_id: str, connector_id: str, tool_name: str,
                arguments: dict, user_id: str) -> dict:
    """试调用连接器最新版本里的某个工具，返回网关结果 dict。

    与生产执行同构（同一网关、同一份版本 spec、同样的权限与 Schema 校验），
    但使用 ``run_id="test_*"`` 的临时上下文，因此不会产生 Run/Step 追踪记录；
    需要完整追踪的试运行请用 capabilities.service.test_connector。
    异常：连接器/版本/工具不存在抛业务错误；参数校验失败由网关抛出。
    """
    connector = db.get(Connector, connector_id)
    if not connector or connector.workspace_id != workspace_id:
        raise NotFoundError("Connector 不存在")
    cv = latest_connector_version(db, connector_id)
    if not cv:
        raise ValidationError("连接器没有版本")
    tool_version = db.execute(select(ToolVersion).join(ToolRow, ToolRow.id == ToolVersion.tool_id)
                              .where(ToolVersion.connector_version_id == cv.id, ToolRow.name == tool_name)
                              .order_by(ToolVersion.version_number.desc()).limit(1)).scalar_one_or_none()
    if not tool_version:
        raise NotFoundError(f"连接器下不存在工具 {tool_name}")
    spec = ToolSpec.from_dict(tool_version.spec)
    spec.provider = connector.provider
    spec.connector_id = connector.id
    spec.connector_version_id = cv.id
    gateway = ToolGateway(db)
    ctx = build_context(db=db, workspace_id=workspace_id, user_id=user_id,
                        run_id="test_" + new_id()[:16], conversation_id=None)
    result = gateway.execute_spec(spec, arguments, cv.spec, ctx)
    return result.to_dict()

