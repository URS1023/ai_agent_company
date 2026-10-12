"""隔离验证连接器推导的权限前置、模型输出校验与旧版响应契约。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.errors import PermissionErrorEx, ValidationError
from app.models import Connector, ConnectorVersion, Tool, ToolVersion
from app.modules.connector import derive as derivation
from app.modules.connector import router as connector_router
from test_system_foundation import activate_user, create_role, create_user


def definition(**changes):
    """使用仅用于配置校验的合成公网地址，不执行该接口。"""
    return {
        "name": "设备查询推导草稿", "base_url": "https://connector-fixture.invalid",
        "path": "/devices/status", "method": "GET", "body_mapping": "query",
        "timeout_seconds": 30,
        "tools": [{"name": "fixture.device_status", "description": "查询设备状态",
                   "input_schema": {"type": "object", "properties": {
                       "device": {"type": "string", "description": "设备编号"}},
                       "required": ["device"], "additionalProperties": False}}],
        **changes,
    }


def install_model(monkeypatch, content):
    calls = []

    def respond(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"content": content}

    monkeypatch.setattr(derivation, "chat_for_run", respond)
    return calls


def counts(db):
    """失败前后核对连接器、版本与工具表，防止部分记录落库。"""
    db.expire_all()
    return tuple(db.scalar(select(func.count()).select_from(model))
                 for model in (Connector, ConnectorVersion, Tool, ToolVersion))


def test_unauthorized_derivation_does_not_call_model(client, administrator, db, monkeypatch):
    role = create_role(client, administrator, code="derive_reader", permissions=["connectors.view"])
    create_user(client, administrator, [role["id"]], username="derive-reader")
    headers = activate_user(client, username="derive-reader")
    calls = install_model(monkeypatch, json.dumps(definition(), ensure_ascii=False))
    before = counts(db)
    response = client.post("/api/connectors/derive", headers=headers, json={"description": "读取设备状态"})
    assert response.status_code == 403, response.text
    assert calls == [], "无创建权限时必须先拒绝，不能发送接口说明或消耗模型调用"
    assert counts(db) == before


def test_direct_route_checks_creation_permission_before_deriving(monkeypatch):
    """HTTP中间件已有保护；直接调用路由时也必须先鉴权，避免迁移后退化。"""
    calls = install_model(monkeypatch, json.dumps(definition()))

    def reject(*args, **kwargs):
        raise PermissionErrorEx("没有创建连接器的权限")

    monkeypatch.setattr(connector_router, "require_permission", reject, raising=False)
    monkeypatch.setattr(connector_router.capability_service, "create_connector", reject)
    with pytest.raises(PermissionErrorEx):
        connector_router.derive(connector_router.DeriveRequest(description="读取设备状态"),
                                SimpleNamespace(workspace_id="isolated-workspace"))
    assert calls == []


def test_json_extraction_preserves_braces_and_escaped_quotes_in_strings():
    plan = definition(description='字段描述含 }、{、转义引号 " 与路径 \\device')
    text = "以下是接口定义：\n```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```\n请确认。"
    assert derivation._extract_json(text) == plan


def test_model_prompt_uses_supported_date_format_keyword():
    """提示词须与保存及执行校验一致，日期输入形态不能冒充格式关键字。"""
    assert "format=date" in derivation._DERIVE_SYSTEM
    assert "format=YYYY-MM-DD" not in derivation._DERIVE_SYSTEM
    assert "YYYY-MM-DD" in derivation._DERIVE_SYSTEM


@pytest.mark.parametrize("content", ["[]", '[{"tools": []}]', '"文本"', "null", "true", "12"])
def test_json_extraction_rejects_non_object_roots(content):
    with pytest.raises(ValidationError):
        derivation._extract_json(content)


@pytest.mark.parametrize("changes", [
    {"tools": "bad"}, {"tools": {}}, {"tools": ["bad"]}, {"tools": [None]},
    {"tools": []}, {"timeout_seconds": "bad"}, {"timeout_seconds": []},
    {"timeout_seconds": True}, {"timeout_seconds": 1.5}, {"timeout_seconds": 0},
    {"timeout_seconds": 121}, {"name": []}, {"method": {}}, {"body_mapping": []},
    {"tools": [{"name": "bad tool", "input_schema": {"type": "object"}}]},
    {"tools": [{"name": "fixture.bad_schema", "input_schema": []}]},
])
def test_dirty_model_definition_is_422_without_partial_records(client, administrator, db, monkeypatch, changes):
    install_model(monkeypatch, json.dumps(definition(**changes), ensure_ascii=False))
    before = counts(db)
    response = client.post("/api/connectors/derive", headers=administrator, json={"description": "读取设备状态"})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error", response.text
    assert counts(db) == before


@pytest.mark.parametrize("content", [[], {}, None, "[]", "null", '{"tools": ['])
def test_invalid_model_content_is_422_without_records(client, administrator, db, monkeypatch, content):
    install_model(monkeypatch, content)
    before = counts(db)
    response = client.post("/api/connectors/derive", headers=administrator, json={"description": "读取设备状态"})
    assert response.status_code == 422, response.text
    assert counts(db) == before


@pytest.mark.parametrize("changes", [{"method": "PUT"}, {"method": "PATCH"}, {"method": "DELETE"},
                                     {"body_mapping": "form"}])
def test_unsupported_http_semantics_are_rejected_not_rewritten(client, administrator, db, monkeypatch, changes):
    install_model(monkeypatch, json.dumps(definition(**changes)))
    before = counts(db)
    response = client.post("/api/connectors/derive", headers=administrator, json={"description": "保留接口原始方法"})
    assert response.status_code == 422, response.text
    assert "GET/POST" in response.text or "json/query" in response.text or "JSON/查询" in response.text
    assert counts(db) == before


@pytest.mark.parametrize("method,body_mode", [("get", "query"), ("POST", "json")])
def test_valid_derivation_keeps_legacy_response_and_draft_semantics(client, administrator, db, monkeypatch, method, body_mode):
    plan = definition(method=method, body_mapping=body_mode)
    calls = install_model(monkeypatch, "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```")
    before = counts(db)
    response = client.post("/api/connectors/derive", headers=administrator, json={"description": "  读取设备状态  "})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["plan"] == plan
    created = body["created"]
    assert created["connector_id"] and created["connector_version_id"] and created["tools"][0]["tool_id"]
    assert created["record"]["status"] == "draft"
    config = created["record"]["versions"][0]["config"]
    assert config["method"] == method.upper() and config["body_mapping"] == body_mode
    assert config["timeout_seconds"] == 30 and config["allow_private"] is False
    assert config["auth"]["type"] == "none"
    assert counts(db) == tuple(value + 1 for value in before)
    assert len(calls) == 1 and calls[0]["kwargs"]["workspace_id"] == administrator["X-Workspace-Id"]


@pytest.mark.parametrize("failure", [PermissionErrorEx("模型权限不足"), asyncio.CancelledError()])
def test_model_permission_and_cancellation_are_not_swallowed(monkeypatch, failure):
    def respond(*args, **kwargs):
        raise failure

    monkeypatch.setattr(derivation, "chat_for_run", respond)
    with pytest.raises(type(failure)) as captured:
        derivation.derive_definition("isolated-workspace", "读取设备状态")
    assert captured.value is failure
