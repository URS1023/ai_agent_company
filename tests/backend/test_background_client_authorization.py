"""后台服务客户端授权使用真实隔离 SQLite；模型与工具只用本机进程内桩，不访问业务库。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.business_worker import chat, ledger, tools
from app.errors import AuthError, PermissionErrorEx
from app.models import ModelCall, Run
from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway
from business_worker_fakes import request


@pytest.fixture
def client_run(client, administrator, db, monkeypatch):
    """经正式客户端签发和 chat.prepare 建单，只替换模型配置、预算与工具实际执行。"""
    from app.providers import model
    from app.modules.model import service as model_service

    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "model": "fixture", "base_url": "http://127.0.0.1:12349/v1", "api_key": "fixture",
        "capabilities": {"runtime_options": {"thinking": "disabled"}}})
    dispatched, budgets = [], []
    monkeypatch.setattr(model_service, "enforce_budget", lambda workspace_id: budgets.append(workspace_id))

    def execute_fixture(_gateway, spec, arguments, name, context):
        dispatched.append(name)
        return ToolResult(status="success", data={"fixture": True})

    monkeypatch.setattr(ToolGateway, "_dispatch", execute_fixture)
    scopes = ["chat.create", "chat.view", "chat.execute", "runs.view", "files.view"]
    response = client.post("/api/v1/system-tools/integration-clients", headers=administrator,
        json={"name": "后台客户端授权验收", "redirectUris": [], "scopes": scopes})
    assert response.status_code == 201
    configured = response.json()
    response = client.post("/api/auth/client-token", json={"clientId": configured["clientId"],
        "clientSecret": configured["clientSecret"], "workspaceId": administrator["X-Workspace-Id"]})
    assert response.status_code == 200
    raw_token = response.json()["accessToken"]
    headers = {"Authorization": "Bearer " + raw_token, "X-Workspace-Id": administrator["X-Workspace-Id"]}
    response = client.post("/api/chat/conversations", headers=headers, json={"title": "长任务授权"})
    assert response.status_code == 201, response.text
    conversation_id = response.json()["id"]
    prepared = chat.prepare(request(headers, f"/api/chat/conversations/{conversation_id}/send",
        {"content": "读取文件并分析"}, "POST"))
    run = db.get(Run, prepared["run_id"])
    configured_row = db.get(IntegrationClient, configured["id"])
    token = db.scalar(select(IntegrationAccessToken).where(IntegrationAccessToken.client_record_id == configured_row.id))
    return SimpleNamespace(db=db, run=run, client=configured_row, token=token, raw_token=raw_token,
        scopes=scopes, dispatched=dispatched, budgets=budgets, headers=headers)


def invoke_tool(fixture, call_id="new-call", sequence=1):
    """只走网关授权与进程内执行桩；不会读取真实文件。"""
    return tools.execute(fixture.run.id, call_id, sequence, "read_file", {"file_id": "fixture-file"})


def invoke_model(fixture):
    """只请求模型台账准入，不发模型 HTTP 请求。"""
    return ledger.authorize(run_id=fixture.run.id, provider="fixture", model="fixture")


def test_new_run_saves_only_internal_token_provenance_and_valid_calls_work(client_run):
    fixture = client_run
    actor = fixture.run.context_snapshot["actor"]
    assert actor["client_token_id"] == fixture.token.id
    assert actor["client_id"] == fixture.client.client_id
    assert actor["client_scopes"] == sorted(fixture.scopes)
    assert fixture.raw_token not in str(actor)
    assert fixture.token.token_digest not in str(actor)
    assert invoke_tool(fixture)["status"] == "success"
    assert invoke_model(fixture)["allowed"] is True
    assert fixture.dispatched == ["read_file"]
    assert len(fixture.budgets) == 1


@pytest.mark.parametrize("change", ["disable_client", "revoke_token", "delete_token", "expire_token", "token_workspace", "token_user", "client_owner", "client_identity"])
def test_revocation_expiry_and_ownership_changes_stop_new_tools_and_models(client_run, change):
    fixture = client_run
    if change == "disable_client":
        fixture.client.enabled = False
    elif change == "revoke_token":
        fixture.token.revoked_at = datetime.utcnow()
    elif change == "delete_token":
        fixture.db.delete(fixture.token)
    elif change == "expire_token":
        fixture.token.expires_at = datetime.utcnow() - timedelta(seconds=1)
    elif change == "token_workspace":
        fixture.token.workspace_id = "another-workspace"
    elif change == "token_user":
        fixture.token.actor_user_id = "another-user"
    elif change == "client_owner":
        fixture.client.created_by = "another-user"
    else:
        fixture.client.client_id = "another-client-identity"
    fixture.db.commit()
    with pytest.raises(AuthError):
        invoke_tool(fixture)
    with pytest.raises(AuthError):
        invoke_model(fixture)
    assert fixture.dispatched == []
    assert fixture.budgets == []
    assert fixture.db.scalar(select(ModelCall).where(ModelCall.run_id == fixture.run.id)) is None


@pytest.mark.parametrize("scope_owner", ["client", "token"])
def test_withdrawing_chat_execution_scope_stops_tools_and_models(client_run, scope_owner):
    fixture = client_run
    row = getattr(fixture, scope_owner)
    row.scopes = [scope for scope in row.scopes if scope != "chat.execute"]
    fixture.db.commit()
    with pytest.raises(PermissionErrorEx):
        invoke_tool(fixture)
    with pytest.raises(PermissionErrorEx):
        invoke_model(fixture)
    assert fixture.dispatched == []
    assert fixture.budgets == []


def test_tool_scope_reduction_takes_effect_without_stopping_unrelated_model_work(client_run):
    fixture = client_run
    fixture.client.scopes = [scope for scope in fixture.client.scopes if scope != "files.view"]
    fixture.db.commit()
    result = invoke_tool(fixture)
    assert result["error_code"] == "tool_not_available"
    assert fixture.dispatched == []
    assert invoke_model(fixture)["allowed"] is True


def test_expanded_live_scopes_cannot_exceed_original_run_snapshot(client_run):
    fixture = client_run
    actor = {**fixture.run.context_snapshot["actor"], "client_scopes": ["chat.view", "chat.execute", "runs.view"]}
    fixture.run.context_snapshot = {**fixture.run.context_snapshot, "actor": actor}
    fixture.client.scopes = fixture.scopes + ["analytics.execute"]
    fixture.token.scopes = fixture.scopes + ["analytics.execute"]
    fixture.db.commit()
    current = chat.run_actor(fixture.db, fixture.run)
    assert current.client_scopes == frozenset(actor["client_scopes"])
    assert invoke_tool(fixture)["error_code"] == "tool_not_available"
    assert fixture.dispatched == []


def test_missing_snapshot_scopes_never_fall_back_to_administrator_rights(client_run):
    fixture = client_run
    actor = dict(fixture.run.context_snapshot["actor"])
    actor.pop("client_scopes")
    fixture.run.context_snapshot = {**fixture.run.context_snapshot, "actor": actor}
    fixture.db.commit()
    with pytest.raises(PermissionErrorEx):
        invoke_model(fixture)
    assert fixture.budgets == []


def test_legacy_snapshot_without_token_id_checks_client_but_cannot_prove_token_revocation(client_run):
    """旧数据没有唯一原令牌证据，明确验证兼容边界，不能宣称具备单令牌吊销检查。"""
    fixture = client_run
    actor = dict(fixture.run.context_snapshot["actor"])
    actor.pop("client_token_id")
    fixture.run.context_snapshot = {**fixture.run.context_snapshot, "actor": actor}
    fixture.token.revoked_at = datetime.utcnow()
    fixture.client.scopes = ["chat.view", "chat.execute", "runs.view"]
    fixture.db.commit()
    current = chat.run_actor(fixture.db, fixture.run)
    assert current.client_scopes == frozenset(fixture.client.scopes)
    assert invoke_tool(fixture)["error_code"] == "tool_not_available"
    fixture.client.enabled = False
    fixture.db.commit()
    with pytest.raises(AuthError):
        invoke_model(fixture)
    assert fixture.dispatched == []
    assert fixture.budgets == []


def test_background_authorization_does_not_write_http_usage_heartbeat(client_run):
    fixture = client_run
    old = datetime.utcnow() - timedelta(minutes=5)
    fixture.token.last_used_at = old
    fixture.client.last_used_at = old
    fixture.db.commit()
    chat.run_actor(fixture.db, fixture.run)
    fixture.db.commit()
    fixture.db.refresh(fixture.token)
    fixture.db.refresh(fixture.client)
    assert fixture.token.last_used_at == old
    assert fixture.client.last_used_at == old


def test_http_authentication_keeps_existing_usage_heartbeat(client_run, client):
    """公共校验抽取后，正式 HTTP 请求仍按原有节流规则记录客户端使用时间。"""
    fixture = client_run
    old = datetime.utcnow() - timedelta(minutes=5)
    fixture.token.last_used_at = old
    fixture.client.last_used_at = old
    fixture.db.commit()
    response = client.get("/api/chat/conversations", headers=fixture.headers)
    assert response.status_code == 200, response.text
    fixture.db.refresh(fixture.token)
    fixture.db.refresh(fixture.client)
    assert fixture.token.last_used_at > old
    assert fixture.client.last_used_at > old
