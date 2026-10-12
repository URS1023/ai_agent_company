"""Bot 通过真实 HTTP 身份与版本链执行；只使用临时 SQLite 和内存模型、传输桩。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ids import new_id
from app.models import Connector, File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, Skill, ToolCall
from app.modules.knowledge_graph.models import BotDebugRun
from app.modules.system.models import Role
from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
from test_capabilities_center import create_http, http_payload
from test_knowledge_graph import result
from test_system_foundation import activate_user, create_role, create_user


BOT_API = "/api/v1/bots"
CAP_API = "/api/v1/capabilities/connectors"


@pytest.fixture
def memory_providers(monkeypatch):
    """仅替换外部边界，保留模型选择、资源授权和 ToolGateway 的完整调用链。"""
    from app.providers import model
    from app.providers.tool import http_provider

    state = SimpleNamespace(models=[], requests=[], before_selection=None)

    def chat(profile, messages, **kwargs):
        state.models.append((messages, kwargs))
        if kwargs.get("tools"):
            if state.before_selection:
                state.before_selection()
            name = kwargs["tools"][0]["function"]["name"]
            return {"content": "", "tool_calls": [{"id": "memory-call", "function": {
                "name": name, "arguments": '{"deviceCode":"A-01"}',
            }}]}
        return {"content": "已依据固定版本回答 [1]"}

    def transport(config, body, credentials, **kwargs):
        state.requests.append((config.url, body))
        return {"url": config.url, "state": "ready"}, {}

    monkeypatch.setattr(model, "chat_for_run", chat)
    monkeypatch.setattr(http_provider, "request_json", transport)
    return state


def knowledge_fixture(client, administrator, db):
    """构造完整知识关系，正文不经文件解析或外部索引写入。"""
    workspace_id = administrator["X-Workspace-Id"]
    owner = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    now = datetime.utcnow()
    kb = KnowledgeBase(id=new_id(), workspace_id=workspace_id, owner_user_id=owner,
        name="Bot 授权知识", description="", visibility="workspace", status="active",
        created_at=now, updated_at=now)
    file = File(id=new_id(), workspace_id=workspace_id, owner_user_id=owner, name="巡检.txt",
        mime_type="text/plain", size=10, storage_key=new_id(), sha256="0" * 64,
        status="ready", created_at=now)
    document = KnowledgeDocument(id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb.id,
        file_id=file.id, status="ready", parser_version="test", created_at=now, updated_at=now)
    chunk = KnowledgeChunk(id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb.id,
        document_id=document.id, ordinal=0, text="设备温度必须低于 80 摄氏度", location={"page": 0},
        embedding_status="done", created_at=now)
    db.add_all([kb, file, document, chunk])
    db.commit()
    return kb


def published_bot(client, headers, **bindings):
    """通过管理接口创建、发布并签发专属 Key，正式调用不直接拼快照。"""
    bot = result(client.post(BOT_API, headers=headers,
        json={"name": "授权 Bot " + new_id(), "apiEnabled": True, **bindings}), 201)
    bot = result(client.post(f"{BOT_API}/{bot['id']}/publish", headers=headers,
        json={"version": bot["version"]}))
    key = result(client.post(f"{BOT_API}/{bot['id']}/api-keys", headers=headers,
        json={"name": "隔离调用"}), 201)
    return bot, key


def invoke(client, bot, key):
    return client.post(f"/api/public/v1/bots/{bot['id']}/invoke",
        headers={"X-Bot-Key": key["key"]}, json={"query": "设备温度 A-01", "mode": "retrieval"})


def service_headers(client, administrator, scopes):
    """签发真实服务令牌；不通过伪造 AuthContext 绕过 HTTP 认证。"""
    response = client.post("/api/v1/system-tools/integration-clients", headers=administrator,
        json={"name": "Bot 客户端 " + new_id(), "redirectUris": [], "scopes": scopes})
    assert response.status_code == 201, response.text
    configured = response.json()
    response = client.post("/api/auth/client-token", json={"clientId": configured["clientId"],
        "clientSecret": configured["clientSecret"], "workspaceId": administrator["X-Workspace-Id"]})
    assert response.status_code == 200, response.text
    return configured, {"Authorization": "Bearer " + response.json()["accessToken"],
        "X-Workspace-Id": administrator["X-Workspace-Id"]}


@pytest.mark.parametrize("knowledge_scope", [False, True])
def test_published_debug_keeps_service_scopes(client, administrator, db, memory_providers, knowledge_scope):
    kb = knowledge_fixture(client, administrator, db)
    bot, _ = published_bot(client, administrator, knowledgeBaseIds=[kb.id])
    scopes = ["bots.execute", "runs.view", "chat.view"] + (["knowledge.view"] if knowledge_scope else [])
    _, headers = service_headers(client, administrator, scopes)
    response = client.post(f"{BOT_API}/{bot['id']}/debug", headers=headers,
        json={"query": "设备温度", "mode": "retrieval", "usePublished": True})
    if knowledge_scope:
        assert response.status_code == 200, response.text
        assert any(item["type"] == "knowledge_chunk" for item in response.json()["data"]["references"])
    else:
        assert response.status_code == 403, response.text
        assert "80 摄氏度" not in response.text
    assert not memory_providers.models and not memory_providers.requests


def member_bot_owner(client, administrator, extra_permissions=()):
    permissions = ["home.view", "bots.create", "bots.view", "bots.publish", "bots.execute",
        "knowledge.view", "runs.view", "chat.view", *extra_permissions]
    role = create_role(client, administrator, code="bot-owner", permissions=permissions, scope="workspace")
    create_user(client, administrator, [role["id"]], username="bot-owner")
    return role, activate_user(client, username="bot-owner")


def test_public_key_checks_owner_bot_execution_permission(client, administrator, db, memory_providers):
    kb = knowledge_fixture(client, administrator, db)
    role, owner = member_bot_owner(client, administrator)
    bot, key = published_bot(client, owner, knowledgeBaseIds=[kb.id])
    assert invoke(client, bot, key).status_code == 200
    row = db.get(Role, role["id"])
    row.permissions = [value for value in row.permissions if value != "bots.execute"]
    db.commit()
    before = db.scalar(select(func.count()).select_from(BotDebugRun))
    response = invoke(client, bot, key)
    assert response.status_code == 403, response.text
    assert db.scalar(select(func.count()).select_from(BotDebugRun)) == before


def published_tool(client, headers):
    payload = http_payload(tool_name="bot.asset_lookup")
    payload["tools"][0]["permissions"] = []
    payload["config"]["path"] = "/v1/original"
    connector = create_http(client, headers, payload)
    connector = result(client.post(f"{CAP_API}/{connector['id']}/publish", headers=headers,
        json={"version": 1}))
    return connector, payload


@pytest.mark.parametrize("resource", ["skill", "tool"])
def test_public_bot_keeps_historical_published_version(client, administrator, db, memory_providers, resource):
    if resource == "skill":
        response = client.post("/api/skills", headers=administrator,
            json={"name": "固定方法", "spec": {"instructions": "只能使用旧版本方法"}})
        assert response.status_code == 201, response.text
        skill_id = response.json()["skill_id"]
        original = response.json()["version_id"]
        assert client.post(f"/api/skills/{skill_id}/publish", headers=administrator).status_code == 200
        bot, key = published_bot(client, administrator, skillIds=[skill_id])
        assert client.post(f"/api/skills/{skill_id}/versions", headers=administrator,
            json={"spec": {"instructions": "不能进入旧 Bot 的新版本"}}).status_code == 201
        assert client.post(f"/api/skills/{skill_id}/publish", headers=administrator).status_code == 200
    else:
        connector, payload = published_tool(client, administrator)
        tool = connector["versions"][0]["tools"][0]
        original = tool["versionId"]
        bot, key = published_bot(client, administrator, toolIds=[tool["id"]])
        payload["version"] = 1
        payload["config"]["path"] = "/v2/new"
        assert client.post(f"{CAP_API}/{connector['id']}/versions", headers=administrator,
            json=payload).status_code == 201
        assert client.post(f"{CAP_API}/{connector['id']}/publish", headers=administrator,
            json={"version": 2}).status_code == 200
    response = invoke(client, bot, key)
    assert response.status_code == 200, response.text
    references = response.json()["data"]["references"]
    field = "skillVersionId" if resource == "skill" else "toolVersionId"
    assert any(item.get(field) == original for item in references), references
    if resource == "tool":
        assert memory_providers.requests == [("https://api.example.com/v1/original", {"deviceCode": "A-01"})]
        source = db.get(Connector, connector["id"])
    else:
        source = db.get(Skill, skill_id)
    source.status = "archived"
    db.commit()
    before = len(memory_providers.requests)
    withdrawn = invoke(client, bot, key)
    assert len(memory_providers.requests) == before
    if resource == "skill":
        assert withdrawn.status_code == 422, withdrawn.text
    else:
        assert withdrawn.status_code == 200, withdrawn.text
        assert not any(item.get("type") == "tool_execution"
            for item in withdrawn.json()["data"]["references"])


def test_public_tool_resolution_uses_execution_scope_before_query(client, administrator, db, memory_providers):
    connector, _ = published_tool(client, administrator)
    tool = connector["versions"][0]["tools"][0]
    role, owner = member_bot_owner(client, administrator, ["connectors.view", "connectors.execute"])
    bot, key = published_bot(client, owner, toolIds=[tool["id"]])
    row = db.get(Role, role["id"])
    row.permissions = [value for value in row.permissions if value != "connectors.view"]
    db.commit()
    response = invoke(client, bot, key)
    assert response.status_code == 200, response.text
    assert len(memory_providers.requests) == 1, response.text
    assert any(item.get("type") == "tool_execution" for item in response.json()["data"]["references"])
    row.permissions = [value for value in row.permissions if value != "connectors.execute"]
    db.commit()
    denied = invoke(client, bot, key)
    assert denied.status_code == 200, denied.text
    assert len(memory_providers.requests) == 1
    assert not any(item.get("type") == "tool_execution" for item in denied.json()["data"]["references"])


@pytest.mark.parametrize("change", ["client_disabled", "token_revoked", "token_expired", "token_deleted",
    "token_workspace", "token_user", "client_owner", "client_identity", "client_scopes", "token_scopes"])
def test_service_revocation_between_selection_and_dispatch_stops_action(
    client, administrator, db, memory_providers, change,
):
    """在内存模型返回前提交明确撤权，下一动作阶段不得沿用请求入口的权限快照。"""
    connector, _ = published_tool(client, administrator)
    tool = connector["versions"][0]["tools"][0]
    bot, _ = published_bot(client, administrator, toolIds=[tool["id"]])
    scopes = ["bots.execute", "connectors.execute", "runs.view", "chat.view"]
    configured, headers = service_headers(client, administrator, scopes)
    configured_row = db.get(IntegrationClient, configured["id"])
    token = db.scalar(select(IntegrationAccessToken).where(
        IntegrationAccessToken.client_record_id == configured_row.id))

    def withdraw():
        if change == "client_disabled":
            configured_row.enabled = False
        elif change == "token_revoked":
            token.revoked_at = datetime.utcnow()
        elif change == "token_expired":
            token.expires_at = datetime.utcnow() - timedelta(seconds=1)
        elif change == "token_deleted":
            db.delete(token)
        elif change == "token_workspace":
            token.workspace_id = new_id()
        elif change == "token_user":
            token.actor_user_id = new_id()
        elif change == "client_owner":
            configured_row.created_by = new_id()
        elif change == "client_identity":
            configured_row.client_id = "another-client"
        elif change == "client_scopes":
            configured_row.scopes = [scope for scope in scopes if scope != "connectors.execute"]
        else:
            token.scopes = [scope for scope in scopes if scope != "connectors.execute"]
        db.commit()

    memory_providers.before_selection = withdraw
    response = client.post(f"{BOT_API}/{bot['id']}/debug", headers=headers,
        json={"query": "查询设备 A-01", "mode": "retrieval", "usePublished": True})
    assert response.status_code == (403 if change.endswith("scopes") else 401), response.text
    assert len(memory_providers.models) == 1 and not memory_providers.requests
    assert db.scalar(select(func.count()).select_from(ToolCall)) == 0


def test_expanded_service_scopes_never_exceed_request_snapshot(client, administrator, db, memory_providers):
    payload = http_payload(tool_name="bot.scoped_lookup")
    connector = create_http(client, administrator, payload)
    connector = result(client.post(f"{CAP_API}/{connector['id']}/publish", headers=administrator,
        json={"version": 1}))
    tool = connector["versions"][0]["tools"][0]
    bot, _ = published_bot(client, administrator, toolIds=[tool["id"]])
    scopes = ["bots.execute", "connectors.execute", "runs.view", "chat.view"]
    configured, headers = service_headers(client, administrator, scopes)
    configured_row = db.get(IntegrationClient, configured["id"])
    token = db.scalar(select(IntegrationAccessToken).where(
        IntegrationAccessToken.client_record_id == configured_row.id))

    def expand():
        configured_row.scopes = [*scopes, "devices.view"]
        token.scopes = [*scopes, "devices.view"]
        db.commit()

    memory_providers.before_selection = expand
    response = client.post(f"{BOT_API}/{bot['id']}/debug", headers=headers,
        json={"query": "查询设备 A-01", "mode": "retrieval", "usePublished": True})
    assert response.status_code == 422, response.text
    assert not memory_providers.requests
    call = db.scalar(select(ToolCall))
    assert call.error_code == "forbidden"


def test_public_key_wrong_bot_and_revoke_fail_before_any_new_run(client, administrator, db, memory_providers):
    bot, key = published_bot(client, administrator)
    other, _ = published_bot(client, administrator)
    assert invoke(client, other, key).status_code == 404
    assert db.scalar(select(func.count()).select_from(BotDebugRun)) == 0
    response = client.delete(f"{BOT_API}/{bot['id']}/api-keys/{key['id']}?version={key['version']}",
        headers=administrator)
    assert response.status_code == 200, response.text
    assert invoke(client, bot, key).status_code == 404
    assert db.scalar(select(func.count()).select_from(BotDebugRun)) == 0
