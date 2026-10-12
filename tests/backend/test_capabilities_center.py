"""Tools/connectors: secure configuration, versions, scope and real execution traces.

中文说明：能力中心：HTTP/MCP/数据库连接器的安全配置（拒绝明文凭据、SSRF、stdio、非法 schema）、
版本管理、按角色数据范围过滤，以及真实的调用轨迹。
隔离方式：client 夹具（内存 SQLite + TestClient）+ 本地 HTTP 桩，真实走接口，不连生产库。
边界：旧版连接器别名不能绕过新校验；历史版本必须归档而不是丢失。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.ids import new_id
from app.models import Connector, DataSource, Run, RunStep, Tool, ToolCall
from app.modules.secret.service import create_secret
from app.providers.tool.base import ToolResult
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/capabilities"


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def http_payload(name="设备状态接口", tool_name="factory.device_status", secret_id=None):
    auth = {"type": "bearer", "secretId": secret_id} if secret_id else {"type": "none"}
    return {
        "name": name,
        "description": "读取已授权设备的当前状态",
        "provider": "http",
        "visibility": "workspace",
        "config": {
            "baseUrl": "https://api.example.com",
            "path": "/v1/devices/status",
            "method": "POST",
            "bodyMode": "json",
            "headers": {"X-Client": "qknow"},
            "auth": auth,
            "timeoutSeconds": 20,
            "allowPrivate": False,
        },
        "tools": [{
            "name": tool_name,
            "description": "按设备编码读取状态",
            "inputSchema": {
                "type": "object",
                "properties": {"deviceCode": {"type": "string", "minLength": 1}},
                "required": ["deviceCode"],
                "additionalProperties": False,
            },
            "permissions": ["devices.view"],
            "timeoutSeconds": 20,
        }],
    }


def create_http(client, headers, payload=None):
    return result(client.post(API + "/connectors", headers=headers, json=payload or http_payload()), 201)


def test_http_connector_versions_tools_and_real_test_trace(client, administrator, db, monkeypatch):
    admin_id = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    secret = create_secret(
        db, workspace_id=administrator["X-Workspace-Id"], owner_user_id=admin_id,
        name="设备接口令牌", provider="api_key", value="never-return-this-token",
    )
    db.commit()
    created = create_http(client, administrator, http_payload(secret_id=secret.id))
    assert created["version"] == 1 and created["status"] == "draft"
    config = created["versions"][0]["config"]
    assert config["auth"]["hasSecret"] is True
    assert "never-return-this-token" not in str(created)

    published = result(client.post(
        API + f"/connectors/{created['id']}/publish", headers=administrator, json={"version": 1},
    ))
    assert published["status"] == "published"
    tools = result(client.get(API + "/tools", headers=administrator))
    custom = next(item for item in tools["items"] if item["name"] == "factory.device_status")
    assert custom["connectorId"] == created["id"] and custom["provider"] == "http"

    invalid = client.post(
        API + "/tools/factory.device_status/validate", headers=administrator,
        json={"arguments": {"wrong": "A-01"}},
    )
    assert invalid.status_code == 422 and "deviceCode" in invalid.text
    valid = result(client.post(
        API + "/tools/factory.device_status/validate", headers=administrator,
        json={"arguments": {"deviceCode": "A-01"}},
    ))
    assert valid["valid"] is True

    def fake_execute(_gateway, spec, arguments, connector_spec, execution_context):
        assert spec.name == "factory.device_status"
        assert arguments == {"deviceCode": "A-01"}
        assert execution_context["allow_network"] is True
        assert connector_spec["auth"]["secret_id"] == secret.id
        return ToolResult(status="success", data={"deviceCode": "A-01", "state": "running"})

    monkeypatch.setattr("app.providers.tool.gateway.ToolGateway.execute_spec", fake_execute)
    tested = result(client.post(
        API + f"/connectors/{created['id']}/test", headers=administrator,
        json={"version": 1, "toolName": "factory.device_status", "arguments": {"deviceCode": "A-01"}},
    ))
    assert tested["status"] == "success" and tested["runId"]
    run = db.get(Run, tested["runId"])
    assert run.status == "completed" and run.id == tested["runId"] and not run.id.startswith("test_")
    assert db.scalar(select(RunStep).where(RunStep.run_id == run.id)).status == "completed"
    call = db.scalar(select(ToolCall).where(ToolCall.run_id == run.id))
    assert call.status == "completed" and call.tool_version_id

    updated_payload = http_payload(tool_name="factory.device_health")
    updated_payload["version"] = 1
    updated = result(client.post(
        API + f"/connectors/{created['id']}/versions", headers=administrator, json=updated_payload,
    ), 201)
    assert updated["version"] == 2 and updated["status"] == "draft"
    result(client.post(
        API + f"/connectors/{created['id']}/publish", headers=administrator, json={"version": 2},
    ))
    db.expire_all()
    old = db.scalar(select(Tool).where(Tool.name == "factory.device_status"))
    assert old.status == "archived" and old.published_version_id is None


def test_connector_rejects_plain_credentials_ssrf_stdio_and_bad_schema(client, administrator):
    raw = http_payload()
    raw["config"]["auth"] = {"type": "bearer", "token": "plain-secret-value"}
    response = client.post(API + "/connectors", headers=administrator, json=raw)
    assert response.status_code == 422 and "plain-secret-value" not in response.text

    header = http_payload(tool_name="factory.header_test")
    header["config"]["headers"] = {"Authorization": "Bearer another-plain-secret"}
    response = client.post(API + "/connectors", headers=administrator, json=header)
    assert response.status_code == 422 and "another-plain-secret" not in response.text

    private = http_payload(tool_name="factory.private_test")
    private["config"]["baseUrl"] = "http://127.0.0.1:9000"
    assert client.post(API + "/connectors", headers=administrator, json=private).status_code == 422

    mcp = {
        "name": "不安全 MCP", "provider": "mcp", "config": {
            "transport": "stdio", "command": "powershell", "args": ["-Command", "whoami"],
        },
        "tools": [{"name": "mcp.unsafe", "inputSchema": {"type": "object", "properties": {}}}],
    }
    response = client.post(API + "/connectors", headers=administrator, json=mcp)
    assert response.status_code == 422 and "本机命令" in response.text

    bad_schema = http_payload(tool_name="factory.bad_schema")
    bad_schema["tools"][0]["inputSchema"]["$ref"] = "https://outside.example/schema.json"
    assert client.post(API + "/connectors", headers=administrator, json=bad_schema).status_code == 422


def test_database_connector_requires_scoped_source_and_readonly_tools(client, administrator, db):
    me = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    source = DataSource(
        id=new_id(), workspace_id=administrator["X-Workspace-Id"], owner_user_id=me,
        name="生产数据库", provider="sqlite", connection_config={"database": "safe.sqlite"},
        status="active", visibility="workspace", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(source)
    db.commit()
    payload = {
        "name": "生产只读查询", "provider": "database", "config": {"dataSourceId": source.id},
        "tools": [{
            "name": "database.query", "description": "执行只读 SQL",
            "inputSchema": {
                "type": "object", "properties": {"sql": {"type": "string"}},
                "required": ["sql"], "additionalProperties": False,
            },
        }],
    }
    created = result(client.post(API + "/connectors", headers=administrator, json=payload), 201)
    permissions = created["versions"][0]["tools"][0]["spec"]["permissions"]
    assert "datasources.execute" in permissions
    payload["tools"][0]["name"] = "database.drop_table"
    assert client.post(API + "/connectors", headers=administrator, json=payload).status_code == 422


def test_connector_and_tools_follow_role_data_scope(client, administrator):
    created = create_http(client, administrator)
    result(client.post(
        API + f"/connectors/{created['id']}/publish", headers=administrator, json={"version": 1},
    ))
    role = create_role(client, administrator, code="capability_self", permissions=[
        "home.view", "capabilities.view", "connectors.view",
    ], scope="self")
    create_user(client, administrator, [role["id"]], username="capability-reader")
    headers = activate_user(client, username="capability-reader")
    connectors = result(client.get(API + "/connectors", headers=headers))
    assert connectors["total"] == 0
    tools = result(client.get(API + "/tools", headers=headers))
    assert all(item["name"] != "factory.device_status" for item in tools["items"])
    assert client.get(API + f"/connectors/{created['id']}", headers=headers).status_code == 404


def test_argument_compile_and_run_to_skill_use_workspace_model_and_trace(client, administrator, db, monkeypatch):
    monkeypatch.setattr("app.providers.tool.compiler.chat_for_run", lambda *args, **kwargs: {
        "content": '{}', "usage": {"total_tokens": 10},
    })
    compiled = result(client.post(
        API + "/tools/current_datetime/compile", headers=administrator,
        json={"intent": "读取当前时间", "references": {}},
    ))
    assert compiled["arguments"] == {}
    assert db.get(Run, compiled["runId"]).status == "completed"

    admin_id = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    source_run = Run(
        id=new_id(), workspace_id=administrator["X-Workspace-Id"], conversation_id="",
        started_by_user_id=admin_id, status="completed", created_at=datetime.utcnow(),
        completed_at=datetime.utcnow(), context_snapshot={}, model_profile="reasoning",
    )
    db.add(source_run)
    db.commit()
    observed = {}

    def generated(*args, **kwargs):
        observed["workspace_id"] = kwargs.get("workspace_id")
        return {"content": '{"name":"设备复核","description":"复核设备异常","instructions":"核对设备、指标和原始数据。","rules":["不得改写原始判断"],"recommended_tools":[],"required_resources":[]}' }

    monkeypatch.setattr("app.modules.capability.service.chat_for_run", generated)
    skill = result(client.post(
        API + "/from-run", headers=administrator,
        json={"runId": source_run.id, "target": "skill"},
    ), 201)
    assert skill["kind"] == "skill" and skill["created"]["skill_id"]
    assert observed["workspace_id"] == administrator["X-Workspace-Id"]


def test_legacy_connector_alias_cannot_bypass_v1_validation_and_archives_history(client, administrator, db):
    legacy = {
        "name": "旧接口安全兼容",
        "provider": "http",
        "connector_spec": {
            "base_url": "https://api.example.com", "path": "/v1/status",
            "method": "POST", "body_mapping": "json", "headers": {},
            "auth": {"type": "bearer", "token": "must-never-persist"},
            "timeout_seconds": 20, "allow_private": False,
        },
        "tools": [{
            "name": "legacy.safe_status", "description": "读取状态",
            "input_schema": {
                "type": "object", "properties": {"code": {"type": "string"}},
                "required": ["code"], "additionalProperties": False,
            },
        }],
    }
    rejected = client.post("/api/connectors", headers=administrator, json=legacy)
    assert rejected.status_code == 422
    assert "must-never-persist" not in rejected.text

    legacy["connector_spec"]["auth"] = {"type": "none"}
    created_response = client.post("/api/connectors", headers=administrator, json=legacy)
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    connector_id = created["connector_id"]
    assert created["record"]["version"] == 1
    assert "must-never-persist" not in str(created)

    detail = client.get(f"/api/connectors/{connector_id}", headers=administrator)
    assert detail.status_code == 200 and detail.json()["id"] == connector_id
    published = client.post(f"/api/connectors/{connector_id}/publish", headers=administrator)
    assert published.status_code == 200 and published.json()["status"] == "published"
    archived = client.delete(f"/api/connectors/{connector_id}", headers=administrator)
    assert archived.status_code == 200 and archived.json()["history_retained"] is True
    db.expire_all()
    assert db.get(Connector, connector_id).status == "archived"
