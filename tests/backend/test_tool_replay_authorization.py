"""缓存重放仍须按当前授权读取；全部使用隔离 SQLite，禁止重新执行真实业务工具。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.common.deps import AuthContext
from app.errors import AuthError, ConflictError
from app.models import Connector, ConnectorVersion, Conversation, DataSource, File, Message, Run, Tool, ToolCall, ToolVersion, User, Workspace, WorkspaceMember
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.capabilities.models import ToolMethodMetadata
from app.modules.system.models import MemberProfile, ResourceGrant, Role, UserRole
from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway, build_context


@pytest.fixture
def replay_actor(db, administrator, monkeypatch):
    """保留真实用户、角色、资源范围与调用记录，只阻止任何执行器被调用。"""
    stamp = datetime(2026, 10, 4)
    workspace_id = administrator["X-Workspace-Id"]
    user = User(id="replay-user", display_name="回放用户", status="active", created_at=stamp, updated_at=stamp)
    role = Role(id="replay-role", workspace_id=workspace_id, code="replay-role", name="回放角色", data_scope="self",
        permissions=["runs.view", "chat.view", "files.view", "analytics.execute", "datasources.view", "datasources.execute",
                     "connectors.view", "connectors.execute", "devices.view"])
    db.add_all([user, role, WorkspaceMember(id="replay-member", workspace_id=workspace_id,
        user_id=user.id, role="member", created_at=stamp), MemberProfile(workspace_id=workspace_id, user_id=user.id, roles_initialized=True),
        UserRole(workspace_id=workspace_id, user_id=user.id, role_id=role.id),
        Conversation(id="replay-conversation", workspace_id=workspace_id, owner_user_id=user.id,
            title="缓存授权", created_at=stamp, updated_at=stamp),
        Message(id="replay-assistant", workspace_id=workspace_id, conversation_id="replay-conversation", role="assistant",
            sequence=1, content="", parts_json=[], status="running", created_at=stamp),
        Run(id="replay-run", workspace_id=workspace_id, conversation_id="replay-conversation", started_by_user_id=user.id,
            status="running", created_at=stamp, context_snapshot={"chat_engine": "dsh", "assistant_message_id": "replay-assistant"})])
    db.commit()

    def no_execution(*args, **kwargs):
        pytest.fail("缓存授权不得调用执行器、解密密钥或重复业务操作")

    monkeypatch.setattr(ToolGateway, "execute", no_execution)
    monkeypatch.setattr(ToolGateway, "_dispatch", no_execution)
    monkeypatch.setattr(ToolGateway, "_prepare_secrets", no_execution)

    def cache(name, arguments, *, version=None, state="completed"):
        identity = chat.session_identity(workspace_id, user.id, "replay-conversation")
        key = "dsh:" + hashlib.sha256(f"{identity}:7:cached-call".encode()).hexdigest()
        receipt = {"status": "success", "data": {"private_text": "不能在撤权后再次披露"}}
        call = ToolCall(id="replay-call", workspace_id=workspace_id, run_id="replay-run", tool_name=name,
            arguments_json=arguments, result_json=receipt if state == "completed" else None,
            status=state, idempotency_key=key, tool_version_id=version)
        db.add(call)
        db.commit()
        return receipt

    def replay(name, arguments):
        return tools.execute("replay-run", "cached-call", 7, name, arguments)

    return SimpleNamespace(db=db, workspace_id=workspace_id, user=user, role=role, stamp=stamp, cache=cache, replay=replay)


def add_source(actor, owner="other-user"):
    """只登记数据源元数据，不建立数据库连接。"""
    row = DataSource(id="replay-source", workspace_id=actor.workspace_id, owner_user_id=owner,
        name="隔离源", provider="mysql", connection_config={}, status="active", created_at=actor.stamp, updated_at=actor.stamp)
    actor.db.add(row)
    actor.db.commit()
    return row


def grant(actor, kind, identity, actions):
    """为他人资源提供可撤销的显式授权。"""
    row = ResourceGrant(workspace_id=actor.workspace_id, resource_type=kind, resource_id=identity,
        subject_type="user", subject_id=actor.user.id, actions=actions)
    actor.db.add(row)
    actor.db.commit()
    return row


def assert_denied(actor, result, code=None):
    """拒绝不能返回旧业务数据，也不能覆盖已保存的原始回执。"""
    assert result["status"] == "failed"
    if code:
        assert result["error_code"] == code
    assert "不能在撤权后再次披露" not in str(result)
    actor.db.expire_all()
    assert actor.db.get(ToolCall, "replay-call").result_json["data"]["private_text"] == "不能在撤权后再次披露"


def test_replay_checks_current_tool_permission_and_governance(replay_actor):
    actor = replay_actor
    args = {"datasource_id": add_source(actor, actor.user.id).id, "query": "查询", "mode": "schema"}
    receipt = actor.cache("query_business_data", args)
    assert actor.replay("query_business_data", args) == receipt
    actor.role.permissions = [permission for permission in actor.role.permissions if permission != "analytics.execute"]
    actor.db.commit()
    assert_denied(actor, actor.replay("query_business_data", args), "tool_not_available")


def test_replay_checks_tool_disable_without_reexecuting(replay_actor):
    actor = replay_actor
    receipt = actor.cache("current_datetime", {})
    assert actor.replay("current_datetime", {}) == receipt
    actor.db.add(ToolMethodMetadata(workspace_id=actor.workspace_id, tool_name="current_datetime", code="current_datetime",
        enabled=False, created_by_user_id=actor.user.id, updated_by_user_id=actor.user.id))
    actor.db.commit()
    assert_denied(actor, actor.replay("current_datetime", {}), "tool_not_available")


def test_replay_checks_explicit_datasource_grant(replay_actor):
    actor = replay_actor
    source = add_source(actor)
    share = grant(actor, "datasources", source.id, ["view"])
    args = {"datasource_id": source.id, "query": "查询", "mode": "schema"}
    receipt = actor.cache("query_business_data", args)
    assert actor.replay("query_business_data", args) == receipt
    actor.db.delete(share)
    actor.db.commit()
    assert_denied(actor, actor.replay("query_business_data", args))


def test_indirect_query_reference_cannot_use_single_source_replay_proof(replay_actor):
    actor = replay_actor
    source = add_source(actor, actor.user.id)
    args = {"datasource_id": source.id, "query": "查询", "query_id": "unknown-multi-source-result"}
    actor.cache("query_business_data", args)
    assert_denied(actor, actor.replay("query_business_data", args), "replay_authorization_required")


def test_missing_runs_view_does_not_hide_internal_idempotency_receipt(replay_actor):
    """运行列表查看权与执行回执去重分离，撤销列表权限不能造成再次执行。"""
    actor = replay_actor
    source = add_source(actor, actor.user.id)
    args = {"datasource_id": source.id, "query": "查询", "mode": "schema"}
    receipt = actor.cache("query_business_data", args)
    actor.role.permissions = [permission for permission in actor.role.permissions if permission != "runs.view"]
    actor.db.commit()
    assert actor.replay("query_business_data", args) == receipt


def test_replay_checks_explicit_file_grant(replay_actor):
    actor = replay_actor
    row = File(id="replay-file", workspace_id=actor.workspace_id, owner_user_id="other-user", name="凭证.txt",
        mime_type="text/plain", size=10, storage_key="no-file-read", sha256="0" * 64, created_at=actor.stamp)
    actor.db.add(row)
    actor.db.commit()
    share = grant(actor, "files", row.id, ["view"])
    args = {"file_id": row.id}
    receipt = actor.cache("read_file", args)
    assert actor.replay("read_file", args) == receipt
    actor.db.delete(share)
    actor.db.commit()
    assert_denied(actor, actor.replay("read_file", args))


def add_connector(actor, source_id):
    """构造发布连接器及不可变版本，不解析地址、不访问 HTTP/MCP/数据库。"""
    actor.db.add_all([
        Connector(id="replay-connector", workspace_id=actor.workspace_id, owner_user_id="other-user", name="隔离连接器",
            provider="database", status="published", published_version_id="replay-cv", created_at=actor.stamp, updated_at=actor.stamp),
        ConnectorVersion(id="replay-cv", connector_id="replay-connector", version_number=1, status="published",
            spec={"data_source_id": source_id}, created_by=actor.user.id, created_at=actor.stamp),
        Tool(id="replay-tool", workspace_id=actor.workspace_id, connector_id="replay-connector", name="fixture.query",
            status="published", published_version_id="replay-tv", created_at=actor.stamp, updated_at=actor.stamp),
        ToolVersion(id="replay-tv", tool_id="replay-tool", connector_version_id="replay-cv", version_number=1,
            status="published", spec={"name": "fixture.query", "provider": "database", "permissions": ["devices.view"]},
            created_by=actor.user.id, created_at=actor.stamp)])
    actor.db.commit()
    return grant(actor, "connectors", "replay-connector", ["view", "execute"])


@pytest.mark.parametrize("revocation", ["connector", "datasource", "version"])
def test_connector_replay_rechecks_connector_bound_source_and_version(replay_actor, revocation):
    actor = replay_actor
    source = add_source(actor)
    source_share = grant(actor, "datasources", source.id, ["view", "execute"])
    connector_share = add_connector(actor, source.id)
    args = {"query": "saved statement"}
    receipt = actor.cache("fixture.query", args, version="replay-tv")
    assert actor.replay("fixture.query", args) == receipt
    if revocation == "connector":
        actor.db.delete(connector_share)
    elif revocation == "datasource":
        actor.db.delete(source_share)
    else:
        actor.db.get(ToolCall, "replay-call").tool_version_id = None
    actor.db.commit()
    assert_denied(actor, actor.replay("fixture.query", args), "replay_authorization_required" if revocation == "version" else None)


def test_aggregate_cache_without_resource_provenance_is_not_replayed(replay_actor):
    actor = replay_actor
    actor.cache("list_files", {})
    assert_denied(actor, actor.replay("list_files", {}), "replay_authorization_required")


def test_first_call_records_connector_version_and_unchanged_replay_does_not_execute(replay_actor, monkeypatch):
    """实际创建调用记录，第二次只复核授权，不能再次进入首次调用的执行入口。"""
    actor = replay_actor
    source = add_source(actor, actor.user.id)
    add_connector(actor, source.id)
    dispatched = []

    def execute_once(*args, **kwargs):
        dispatched.append(True)
        return ToolResult(status="success", data={"saved": True})

    monkeypatch.setattr(ToolGateway, "execute", execute_once)
    first = actor.replay("fixture.query", {"query": "original"})
    assert first["status"] == "success"
    assert actor.replay("fixture.query", {"query": "original"}) == first
    assert dispatched == [True]
    saved = actor.db.scalar(select(ToolCall).where(ToolCall.run_id == "replay-run"))
    assert saved.tool_version_id == "replay-tv"


def test_missing_runs_view_cannot_hide_unknown_side_effect_guard(replay_actor, monkeypatch):
    """保留聊天和工具权但撤销运行查看权，更换调用标识也不能绕过未知副作用保护。"""
    from app.providers.tool.side_effects import uncertain_result

    actor = replay_actor
    source = add_source(actor, actor.user.id)
    add_connector(actor, source.id)
    actor.db.get(Connector, "replay-connector").provider = "http"
    actor.db.commit()
    dispatched = []

    def uncertain_once(_gateway, workspace_id, name, arguments, context):
        dispatched.append(arguments)
        return ToolResult(**uncertain_result(key=context["idempotency_key"], provider="http", error_summary="原操作状态未知"))

    monkeypatch.setattr(ToolGateway, "execute", uncertain_once)
    assert actor.replay("fixture.query", {"query": "original"})["error_code"] == "tool_result_unconfirmed"
    actor.role.permissions = [permission for permission in actor.role.permissions if permission != "runs.view"]
    actor.db.commit()
    blocked = tools.execute("replay-run", "changed-call", 8, "fixture.query", {"query": "changed"})
    assert blocked["error_code"] == "tool_result_unconfirmed"
    assert blocked["metadata"]["replayBlocked"] is True
    assert dispatched == [{"query": "original"}]
    saved = actor.db.scalars(select(ToolCall).where(ToolCall.run_id == "replay-run")).all()
    assert len(saved) == 2


def test_cached_identity_cannot_be_changed_to_pass_authorization(replay_actor):
    actor = replay_actor
    actor.cache("current_datetime", {})
    with pytest.raises(ConflictError):
        actor.replay("list_files", {})
    with pytest.raises(ConflictError):
        actor.replay("current_datetime", {"changed": True})


def test_disabled_account_cannot_replay_cached_data(replay_actor):
    actor = replay_actor
    actor.cache("current_datetime", {})
    actor.user.status = "disabled"
    actor.db.commit()
    with pytest.raises(AuthError):
        actor.replay("current_datetime", {})


def test_gateway_keeps_client_scope_ceiling_when_refreshing_administrator_identity(db, administrator, monkeypatch):
    """管理员执行身份也不能放大服务客户端的权限上限。"""
    workspace = db.get(Workspace, administrator["X-Workspace-Id"])
    member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.role == "admin"))
    user = db.get(User, member.user_id)
    supplied = AuthContext(db, user, workspace, "admin", "", frozenset({"chat.view", "runs.view"}), "limited-client")
    context = build_context(db=db, workspace_id=workspace.id, user_id=user.id, run_id="scope-fixture")
    context["auth_context"] = supplied
    refreshed = auth_context_from_tool(context)
    assert refreshed.client_id == "limited-client"
    assert refreshed.client_scopes == supplied.client_scopes
    assert refreshed is not supplied
    monkeypatch.setattr(ToolGateway, "_dispatch", lambda *args: pytest.fail("受限客户端不得执行无权限工具"))
    gateway = ToolGateway(db)
    assert "read_file" not in {spec.name for spec in gateway.list_available(workspace.id, execution_context=context)}
    assert gateway.execute(workspace.id, "read_file", {"file_id": "unreadable"}, context).error_code == "forbidden"
    assert context["auth_context"].client_scopes == supplied.client_scopes
