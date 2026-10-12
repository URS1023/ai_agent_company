"""对话注册工具必须经过真实网关鉴权、严格配置校验和原子发布。"""
from __future__ import annotations

import pytest
from sqlalchemy import event, func, select

from app.errors import SchemaValidationError, ToolExecutionError
from app.models import AuditLog, Connector, ConnectorVersion, Tool, ToolVersion
from app.modules.capabilities import service as capabilities
from app.providers.tool.gateway import ToolGateway, build_context
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/capabilities"


def _arguments(**changes):
    """默认地址只用于配置持久化，测试不发送网络请求。"""
    return {
        "name": "inspection.lookup", "description": "读取巡检状态",
        "url": "https://api.example.com/v1/inspection", "method": "POST",
        "input_schema": {"type": "object", "properties": {"device": {"type": "string"}},
                         "required": ["device"], "additionalProperties": False},
        **changes,
    }


def _context(client, headers, db, workspace_id):
    user_id = client.get("/api/auth/me", headers=headers).json()["user"]["id"]
    return build_context(db=db, workspace_id=workspace_id, user_id=user_id, run_id="registration-test")


def _counts(db):
    """用明确的测试表统计校验是否残留写入，避免成员视图隐藏半成品。"""
    return tuple(db.scalar(select(func.count()).select_from(model.__table__))
                 for model in (Connector, ConnectorVersion, Tool, ToolVersion))


def _denied(gateway, context, arguments):
    """网关的参数异常与业务失败回执都必须有清晰原因，且不能误报注册成功。"""
    try:
        result = gateway.execute(context["workspace_id"], "add_tool", arguments, context)
    except SchemaValidationError as exc:
        return exc.code, exc.message
    assert result.status != "success", result.to_dict()
    assert result.error_summary
    return result.error_code, result.error_summary


@pytest.mark.parametrize("permissions", [
    ["home.view"], ["home.view", "connectors.view"],
    ["home.view", "connectors.create"], ["home.view", "connectors.publish"],
])
def test_gateway_requires_both_create_and_publish(client, administrator, db, permissions):
    role = create_role(client, administrator, code="runtime_registrar", permissions=permissions)
    create_user(client, administrator, [role["id"]], username="runtime-registrar")
    member = activate_user(client, username="runtime-registrar")
    context = _context(client, member, db, administrator["X-Workspace-Id"])
    gateway = ToolGateway(db)
    before = _counts(db)
    code, message = _denied(gateway, context, _arguments())
    assert code == "forbidden", (code, message, before, _counts(db))
    assert _counts(db) == before
    assert "add_tool" not in {item.name for item in gateway.list_available(
        context["workspace_id"], execution_context=context)}


@pytest.mark.parametrize("changes", [
    {"url": "http://127.0.0.1:8011/private"},
    {"url": "http://192.168.6.18/private"},
    {"url": "http://169.254.169.254/latest/meta-data"},
    {"url": "https://user:plain-password@api.example.com/v1"},
    {"url": "https://api.example.com/v1?token=plain-secret"},
    {"url": "https://api.example.com/v1#required"},
    {"url": "https://api.example.com:99999/v1"},
    {"method": "PUT"}, {"method": "PATCH"}, {"method": "DELETE"},
    {"name": "工具名不合法"},
    {"name": "read_file"},
    {"input_schema": {"type": "object", "$ref": "https://outside.example/schema.json"}},
    {"input_schema": {"type": "array", "items": {"type": "string"}}},
    {"headers": {"Authorization": "Bearer plain-secret"}},
])
def test_gateway_rejects_unsupported_or_unsafe_registration(client, administrator, db, changes):
    context = _context(client, administrator, db, administrator["X-Workspace-Id"])
    before = _counts(db)
    _, message = _denied(ToolGateway(db), context, _arguments(**changes))
    assert "plain-password" not in message and "plain-secret" not in message
    assert _counts(db) == before


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("fixed_query", ["", "?fixed=required"])
def test_gateway_registration_commits_once_and_preserves_contract(client, administrator, db, method, fixed_query):
    role = create_role(client, administrator, code="runtime_registrar", permissions=[
        "home.view", "connectors.create", "connectors.publish",
    ])
    create_user(client, administrator, [role["id"]], username="runtime-registrar")
    member = activate_user(client, username="runtime-registrar")
    context = _context(client, member, db, administrator["X-Workspace-Id"])
    commits = []
    event.listen(db, "after_commit", lambda session: commits.append(True))
    arguments = _arguments(method=method, url=_arguments()["url"] + fixed_query)
    result = ToolGateway(db).execute(context["workspace_id"], "add_tool", arguments, context)
    assert result.status == "success", (result.error_code, result.error_summary)
    assert commits == [True]
    connector_id = result.data["connector_id"]
    connector = db.get(Connector, connector_id)
    assert connector.owner_user_id == context["user_id"] and connector.status == "published"
    version = db.get(ConnectorVersion, connector.published_version_id)
    assert version.status == "published" and version.spec["method"] == method
    assert version.spec["base_url"] + version.spec["path"] == arguments["url"]
    assert version.spec["auth"]["type"] == "none" and version.spec["allow_private"] is False
    tool = db.scalar(select(Tool).where(Tool.connector_id == connector_id))
    tool_version = db.get(ToolVersion, tool.published_version_id)
    assert tool_version.status == "published" and tool_version.connector_version_id == version.id
    assert tool_version.spec["input_schema"] == _arguments()["input_schema"]
    audits = set(db.scalars(select(AuditLog.__table__.c.action).where(
        AuditLog.__table__.c.resource_id == connector_id)).all())
    assert audits == {"connector.create", "connector.publish"}


def test_gateway_duplicate_name_preserves_original_registration(client, administrator, db):
    context = _context(client, administrator, db, administrator["X-Workspace-Id"])
    gateway = ToolGateway(db)
    original = gateway.execute(context["workspace_id"], "add_tool", _arguments(), context)
    assert original.status == "success", (original.error_code, original.error_summary)
    before = _counts(db)
    code, _ = _denied(gateway, context, _arguments(url="https://api.example.com/other"))
    assert code == "conflict" and _counts(db) == before
    connector = db.get(Connector, original.data["connector_id"])
    assert db.get(ConnectorVersion, connector.published_version_id).spec["path"] == "/v1/inspection"


def test_gateway_publication_failure_rolls_back_every_registration_row(client, administrator, db):
    context = _context(client, administrator, db, administrator["X-Workspace-Id"])
    before = _counts(db)
    commits = []

    def fail_publication(session):
        """在真实提交边界注入故障，不替换创建、发布或网关实现。"""
        session.flush()
        if session.scalar(select(Connector.__table__.c.id).where(
                Connector.__table__.c.status == "published")):
            raise ToolExecutionError("隔离测试：发布提交失败")

    event.listen(db, "before_commit", fail_publication)
    event.listen(db, "after_commit", lambda session: commits.append(True))
    _, message = _denied(ToolGateway(db), context, _arguments())
    assert "发布" in message
    assert _counts(db) == before
    assert not commits
    assert not db.scalar(select(AuditLog.__table__.c.id).where(
        AuditLog.__table__.c.action.in_(["connector.create", "connector.publish"])))


def test_gateway_publication_stage_failure_does_not_leave_a_draft(client, administrator, db, monkeypatch):
    context = _context(client, administrator, db, administrator["X-Workspace-Id"])
    before = _counts(db)
    publish = capabilities._publish_connector_record

    def reject_publication(*args, **kwargs):
        """真实发布检查和状态变更后注入故障，验证草稿与发布状态一起回滚。"""
        publish(*args, **kwargs)
        raise ToolExecutionError("隔离测试：发布后置检查失败")

    monkeypatch.setattr(capabilities, "_publish_connector_record", reject_publication)
    _denied(ToolGateway(db), context, _arguments())
    db.commit()
    assert _counts(db) == before
    assert not db.scalar(select(AuditLog.__table__.c.id).where(
        AuditLog.__table__.c.action.in_(["connector.create", "connector.publish"])))


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_registered_tool_keeps_fixed_query_when_dispatched(client, administrator, db, monkeypatch, method):
    from app.providers.tool import http_provider

    context = _context(client, administrator, db, administrator["X-Workspace-Id"])
    calls = []

    def transport_stub(config, body, credentials, **kwargs):
        """只接收内存中的请求规格，不进行 DNS、套接字或业务库访问。"""
        calls.append((config.url, config.method, body))
        return {"state": "ready"}, {}

    monkeypatch.setattr(http_provider, "request_json", transport_stub)
    # 启用 provider 的派发分支；其传输函数已替换为上面的纯内存桩。
    context["allow_network"] = True
    gateway = ToolGateway(db)
    created = gateway.execute(context["workspace_id"], "add_tool", _arguments(
        method=method, url="https://api.example.com/v1/inspection?fixed=required"), context)
    assert created.status == "success", (created.error_code, created.error_summary)
    assert not calls
    result = gateway.execute(context["workspace_id"], "inspection.lookup", {"device": "A-01"}, context)
    assert result.status == "success", (result.error_code, result.error_summary)
    expected_url = "https://api.example.com/v1/inspection?fixed=required"
    if method == "GET":
        expected_url += "&device=A-01"
    assert calls == [(expected_url, method, None if method == "GET" else {"device": "A-01"})]
    assert result.data == {"state": "ready"}


def test_management_api_create_stays_draft_and_publish_has_separate_permission(client, administrator, db):
    role = create_role(client, administrator, code="draft_author", permissions=[
        "home.view", "connectors.create", "connectors.view",
    ])
    create_user(client, administrator, [role["id"]], username="draft-author")
    member = activate_user(client, username="draft-author")
    response = client.post(API + "/connectors", headers=member, json={
        "name": "只建草稿", "provider": "http",
        "config": {"baseUrl": "https://api.example.com", "path": "/draft", "method": "GET"},
        "tools": [{"name": "draft.lookup", "inputSchema": _arguments()["input_schema"]}],
    })
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["status"] == "draft" and created["publishedVersionId"] is None
    denied = client.post(API + f"/connectors/{created['id']}/publish", headers=member, json={"version": 1})
    assert denied.status_code == 403, denied.text
    assert db.get(Connector, created["id"]).status == "draft"
    published = client.post(API + f"/connectors/{created['id']}/publish", headers=administrator, json={"version": 1})
    assert published.status_code == 200 and published.json()["data"]["status"] == "published"
