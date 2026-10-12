"""以真实 HTTP/ToolGateway 和隔离 SQLite 验证共享知识检索的临时作用域。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import select

from app.common.data_scope import bind_data_scope
from app.common.deps import AuthContext, resolve_principal
from app.errors import AuthError, NotFoundError, PermissionErrorEx, ValidationError
from app.ids import new_id
from app.models import Artifact, File, KnowledgeBase, Workspace, WorkspaceMember
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.files.service import get_file
from app.modules.knowledge import service
from app.providers.tool import builtin_provider
from app.providers.tool.gateway import ToolGateway, build_context
from test_knowledge_search_authority import create_segment, index
from test_system_foundation import activate_user, create_role, create_user


SCOPE_KEYS = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
TEXT = "设备温度上限为 80 摄氏度"


@pytest.fixture
def shared(client, administrator, index, db):
    """文件与知识库都由管理员拥有，受限成员不能靠自身所有权通过测试。"""
    kb_id, chunk = create_segment(client, administrator, TEXT)
    source = index.documents[chunk["id"]]
    return {"kb_id": kb_id, "chunk_id": chunk["id"], "file_id": source["file_id"],
            "workspace_id": administrator["X-Workspace-Id"], "source": deepcopy(source)}


def reader(client, administrator, *, file_permissions=False):
    permissions = ["home.view", "knowledge.view"]
    if file_permissions:
        permissions += ["files.view", "files.export"]
    role = create_role(client, administrator, code="knowledge_scope_reader", permissions=permissions)
    user = create_user(client, administrator, [role["id"]])
    return {"headers": activate_user(client), "role_id": role["id"], "user_id": user["id"]}


def grant(client, administrator, member, resource_id, *, domain="knowledge", subject="role", actions=None):
    response = client.post("/api/v1/system/resource-grants", headers=administrator, json={
        "resource_type": domain, "resource_id": resource_id, "subject_type": subject,
        "subject_id": member["role_id" if subject == "role" else "user_id"], "actions": actions or ["view"],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def context(headers, db, workspace_id):
    """从真实签发令牌解析身份，服务客户端的 scope 与 client_id 完整传到网关。"""
    principal = resolve_principal(db, headers["Authorization"].removeprefix("Bearer "))
    workspace = db.get(Workspace, workspace_id)
    membership = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == principal.user.id))
    actor = AuthContext(db=db, user=principal.user, workspace=workspace, role=membership.role, token="",
        client_scopes=principal.client_scopes, client_id=principal.client_id)
    result = build_context(db=db, workspace_id=workspace_id, user_id=actor.user_id, run_id="knowledge-scope-test")
    result["auth_context"] = actor
    return result


def search_http(client, headers, kb_ids=None):
    payload = {"query": "设备温度", "mode": "hybrid", "top_k": 5}
    if kb_ids is not None:
        payload["knowledge_base_ids"] = kb_ids
    return client.post("/api/knowledge/search", headers=headers, json=payload)


def search_tool(ctx, kb_ids=None):
    args = {"query": "设备温度", "top_k": 5}
    if kb_ids is not None:
        args["knowledge_base_ids"] = kb_ids
    return ToolGateway(ctx["db"]).execute(ctx["workspace_id"], "search_knowledge", args, ctx)


def hits(result):
    assert result.status == "success", result.to_dict()
    return result.data["results"]


@pytest.mark.parametrize("file_permissions", [False, True])
@pytest.mark.parametrize("index_state", ["current", "empty", "stale"])
def test_shared_source_http_and_real_gateway_return_same_current_content(
    client, administrator, db, index, shared, file_permissions, index_state,
):
    member = reader(client, administrator, file_permissions=file_permissions)
    grant(client, administrator, member, shared["kb_id"])
    if index_state == "empty":
        index.candidates = []
    elif index_state == "stale":
        index.documents[shared["chunk_id"]]["content"] = "设备温度上限为错误的 99 摄氏度"
    response = search_http(client, member["headers"], [shared["kb_id"]])
    assert response.status_code == 200, response.text
    expected = response.json()["items"]
    assert [item["content"] for item in expected] == [TEXT]
    ctx = context(member["headers"], db, shared["workspace_id"])
    assert db.scalar(select(File.owner_user_id).where(File.id == shared["file_id"])) != ctx["user_id"]
    assert hits(search_tool(ctx, [shared["kb_id"]])) == expected


@pytest.mark.parametrize("subject", ["role", "user"])
def test_explicit_grant_revocation_applies_to_next_gateway_call_with_stale_candidates(
    client, administrator, db, index, shared, subject,
):
    member = reader(client, administrator)
    grant_id = grant(client, administrator, member, shared["kb_id"], subject=subject)
    other_kb, other = create_segment(client, administrator, "设备温度：仍可读取的标准")
    grant(client, administrator, member, other_kb, subject=subject)
    ctx = context(member["headers"], db, shared["workspace_id"])
    assert [item["chunk_id"] for item in hits(search_tool(ctx, [shared["kb_id"]]))] == [shared["chunk_id"]]
    response = client.delete("/api/v1/system/resource-grants/" + grant_id, headers=administrator)
    assert response.status_code == 200, response.text
    # 模拟旧 ES 候选未删除；新调用必须按提交后的授权重建，且继续提供仍授权的 SQL 回退。
    db.rollback()
    index.candidates = [shared["source"]]
    expected = search_http(client, member["headers"])
    assert expected.status_code == 200, expected.text
    assert [item["chunk_id"] for item in expected.json()["items"]] == [other["id"]]
    assert hits(search_tool(ctx)) == expected.json()["items"]
    assert search_http(client, member["headers"], [shared["kb_id"]]).status_code == 404
    assert search_tool(ctx, [shared["kb_id"]]).error_code == "not_found"


def test_foreign_workspace_and_ungranted_sources_never_enter_gateway_results(
    client, administrator, db, index, shared,
):
    member = reader(client, administrator)
    grant(client, administrator, member, shared["kb_id"])
    private_kb, private = create_segment(client, administrator, "设备温度：未共享内部正文")
    original_workspace = db.get(Workspace, shared["workspace_id"])
    foreign = Workspace(id=new_id(), organization_id=original_workspace.organization_id,
        name="隔离测试第二工作区", created_at=datetime.utcnow())
    owner = db.scalar(select(KnowledgeBase.owner_user_id).where(KnowledgeBase.id == shared["kb_id"]))
    db.add(foreign)
    db.add(WorkspaceMember(id=new_id(), workspace_id=foreign.id, user_id=owner, role="admin",
                           created_at=datetime.utcnow()))
    db.commit()
    foreign_headers = {**administrator, "X-Workspace-Id": foreign.id}
    foreign_kb, foreign_chunk = create_segment(client, foreign_headers, "设备温度：其他工作区正文")
    # 索引故意给合法 chunk 伪造跨区来源；输出引用仍必须从真实 SQL 关系重建。
    index.documents[shared["chunk_id"]].update(file_id=index.documents[foreign_chunk["id"]]["file_id"],
        knowledge_base_id=foreign_kb, document_version_id="伪造文档引用")
    ctx = context(member["headers"], db, shared["workspace_id"])
    current = hits(search_tool(ctx))
    assert [item["chunk_id"] for item in current] == [shared["chunk_id"]]
    assert current[0]["file_id"] == shared["file_id"] and current[0]["knowledge_base_id"] == shared["kb_id"]
    assert current == search_http(client, member["headers"]).json()["items"]
    for forbidden in (private_kb, foreign_kb):
        assert search_http(client, member["headers"], [forbidden]).status_code == 404
        assert search_tool(ctx, [forbidden]).error_code == "not_found"
    assert private["id"] not in str(current) and foreign_chunk["id"] not in str(current)


@pytest.mark.parametrize("file_permissions", [False, True])
def test_search_does_not_authorize_original_download_read_file_or_artifact(
    client, administrator, db, index, shared, file_permissions,
):
    member = reader(client, administrator, file_permissions=file_permissions)
    grant(client, administrator, member, shared["kb_id"])
    artifact_id = new_id()
    owner = db.scalar(select(KnowledgeBase.owner_user_id).where(KnowledgeBase.id == shared["kb_id"]))
    db.add(Artifact(id=artifact_id, workspace_id=shared["workspace_id"], run_id="fixture-run", owner_user_id=owner,
        type="document", name="其他成员附件.txt", mime_type="text/plain", size=0, storage_key=None,
        external_url="https://example.invalid/never-requested", sha256=None, metadata_json={},
        status="ready", created_at=datetime.utcnow()))
    db.commit()
    ctx = context(member["headers"], db, shared["workspace_id"])
    assert [item["content"] for item in hits(search_tool(ctx))] == [TEXT]
    assert db.scalar(select(File).where(File.id == shared["file_id"])) is None
    assert db.scalar(select(Artifact).where(Artifact.id == artifact_id)) is None
    with pytest.raises(NotFoundError):
        get_file(db, shared["workspace_id"], shared["file_id"])
    for path in (f"/api/files/{shared['file_id']}/text", f"/api/files/{shared['file_id']}/download",
                 f"/api/artifacts/{artifact_id}", f"/api/artifacts/{artifact_id}/download"):
        response = client.get(path, headers=member["headers"])
        assert response.status_code == (404 if file_permissions else 403), (path, response.text)
    result = ToolGateway(db).execute(shared["workspace_id"], "read_file", {"file_id": shared["file_id"]}, ctx)
    assert result.error_code == ("not_found" if file_permissions else "forbidden"), result.to_dict()
    if file_permissions:
        grant(client, administrator, member, shared["file_id"], domain="files", actions=["view", "export"])
        db.rollback()
        preview = client.get(f"/api/files/{shared['file_id']}/text", headers=member["headers"])
        assert preview.status_code == 200, preview.text
        assert client.get(f"/api/files/{shared['file_id']}/download", headers=member["headers"]).status_code == 200
        allowed = ToolGateway(db).execute(shared["workspace_id"], "read_file", {"file_id": shared["file_id"]}, ctx)
        # 手工分段的源文件是容器说明，原文字节不等于 KnowledgeChunk 中维护的分段正文。
        assert allowed.status == "success" and allowed.data["text"] == preview.json()["text"], allowed.to_dict()
        assert db.scalar(select(Artifact).where(Artifact.id == artifact_id)) is None


@pytest.mark.parametrize("scopes", [["home.view"], ["knowledge.view"]])
def test_current_client_scopes_bound_both_http_and_gateway(client, administrator, db, shared, scopes):
    response = client.post("/api/v1/system-tools/integration-clients", headers=administrator,
        json={"name": "知识作用域客户端", "redirectUris": [], "scopes": scopes})
    assert response.status_code == 201, response.text
    configured = response.json()
    response = client.post("/api/auth/client-token", json={"clientId": configured["clientId"],
        "clientSecret": configured["clientSecret"], "workspaceId": shared["workspace_id"]})
    assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + response.json()["accessToken"], "X-Workspace-Id": shared["workspace_id"]}
    ctx = context(headers, db, shared["workspace_id"])
    http_result, tool_result = search_http(client, headers), search_tool(ctx)
    assert ctx["auth_context"].client_id == configured["clientId"]
    assert ctx["auth_context"].client_scopes == frozenset(scopes)
    if "knowledge.view" in scopes:
        assert http_result.status_code == 200, http_result.text
        assert [item["content"] for item in hits(tool_result)] == [TEXT]
        assert hits(tool_result) == http_result.json()["items"]
    else:
        assert http_result.status_code == 403 and tool_result.error_code == "forbidden"
    assert client.get(f"/api/files/{shared['file_id']}/text", headers=headers).status_code == 403
    assert client.get(f"/api/files/{shared['file_id']}/download", headers=headers).status_code == 403
    assert ToolGateway(db).execute(shared["workspace_id"], "read_file", {"file_id": shared["file_id"]}, ctx).error_code == "forbidden"


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("prebound", [False, True])
def test_direct_scope_restores_three_session_fields_on_success_and_exception(
    client, administrator, db, shared, monkeypatch, failure, prebound,
):
    member = reader(client, administrator, file_permissions=True)
    grant(client, administrator, member, shared["kb_id"])
    ctx = context(member["headers"], db, shared["workspace_id"])
    actor = auth_context_from_tool(ctx)
    actor.request_permission = "files.export"
    ctx["auth_context"] = actor
    bind_data_scope(actor, "files.export")
    if not prebound:
        for key in SCOPE_KEYS:
            db.info.pop(key, None)
        # 无缓存身份的直接入口也必须在身份重建之前保存原作用域。
        ctx.pop("auth_context")
    marker = object()
    db.info["unrelated_fixture"] = marker
    before = dict(db.info)
    original_search = service.search

    def inspect_scope(session, **kwargs):
        assert session.scalar(select(File).where(File.id == shared["file_id"])) is not None
        assert actor.request_permission == "files.export"
        session.info["search_diagnostic"] = "保留检索期间与授权无关的状态"
        if failure:
            raise ValidationError("隔离测试检索阶段失败")
        return original_search(session, **kwargs)

    monkeypatch.setattr(service, "search", inspect_scope)
    if failure:
        with pytest.raises(ValidationError, match="检索阶段失败"):
            builtin_provider._search_knowledge({"query": "设备温度"}, ctx)
    else:
        assert [item["content"] for item in hits(builtin_provider._search_knowledge({"query": "设备温度"}, ctx))] == [TEXT]
    for key in SCOPE_KEYS:
        assert (key in db.info) == (key in before)
        if key in before:
            assert db.info[key] is before[key]
    assert db.info["unrelated_fixture"] is marker
    assert db.info["search_diagnostic"] == "保留检索期间与授权无关的状态"
    assert actor.request_permission == "files.export"
    if prebound:
        assert db.scalar(select(File).where(File.id == shared["file_id"])) is None
        with pytest.raises(NotFoundError):
            builtin_provider._read_file({"file_id": shared["file_id"]}, ctx)


def test_nested_search_restores_outer_scope_and_then_original_scope(client, administrator, db, shared, monkeypatch):
    member = reader(client, administrator)
    grant(client, administrator, member, shared["kb_id"])
    ctx = context(member["headers"], db, shared["workspace_id"])
    ctx["auth_context"] = auth_context_from_tool(ctx)
    original = db.info["resource_access_criteria"]
    search = service.search
    depth = 0

    def nested(session, **kwargs):
        nonlocal depth
        current = session.info["resource_access_criteria"]
        assert session.scalar(select(File).where(File.id == shared["file_id"])) is not None
        if depth == 0:
            depth += 1
            assert len(hits(builtin_provider._search_knowledge({"query": "设备温度"}, ctx))) == 1
            assert session.info["resource_access_criteria"] is current
        return search(session, **kwargs)

    monkeypatch.setattr(service, "search", nested)
    assert len(hits(builtin_provider._search_knowledge({"query": "设备温度"}, ctx))) == 1
    assert db.info["resource_access_criteria"] is original
    assert db.scalar(select(File).where(File.id == shared["file_id"])) is None


def test_partial_binding_failure_restores_the_callers_scope(client, administrator, db, shared, monkeypatch):
    member = reader(client, administrator)
    ctx = context(member["headers"], db, shared["workspace_id"])
    ctx["auth_context"] = auth_context_from_tool(ctx)
    before = dict(db.info)

    def incomplete_binding(actor, permission):
        assert permission == "knowledge.view"
        actor.db.info["resource_access_criteria"] = ()
        actor.db.info["actor_user_id"] = "绑定中途未完成"
        raise ValidationError("隔离测试作用域绑定失败")

    monkeypatch.setattr("app.common.data_scope.bind_data_scope", incomplete_binding)
    with pytest.raises(ValidationError, match="作用域绑定失败"):
        builtin_provider._search_knowledge({"query": "设备温度"}, ctx)
    assert db.info == before


@pytest.mark.parametrize("reason", ["permission", "identity"])
def test_direct_entry_rejects_missing_permission_or_mismatched_actor_without_search(
    client, administrator, db, shared, monkeypatch, reason,
):
    ctx = context(administrator, db, shared["workspace_id"])
    if reason == "permission":
        ctx["auth_context"].client_scopes = frozenset()
    else:
        ctx["user_id"] = "不属于该身份的用户"
    before = dict(db.info)
    monkeypatch.setattr(service, "search", lambda *args, **kwargs: pytest.fail("拒绝后不能发起检索或模型请求"))
    with pytest.raises(PermissionErrorEx if reason == "permission" else AuthError):
        builtin_provider._search_knowledge({"query": "设备温度"}, ctx)
    assert db.info == before


@pytest.mark.parametrize("invalid", ["file_deleted", "file_unready", "kb_inactive", "segment_disabled"])
def test_shared_scope_keeps_current_file_join_and_lifecycle_checks(
    client, administrator, db, index, shared, invalid,
):
    from app.modules.knowledge.models import KnowledgeChunkMetadata

    member = reader(client, administrator)
    grant(client, administrator, member, shared["kb_id"])
    if invalid == "file_deleted":
        db.get(File, shared["file_id"]).deleted_at = datetime.utcnow()
    elif invalid == "file_unready":
        db.get(File, shared["file_id"]).status = "failed"
    elif invalid == "kb_inactive":
        db.get(KnowledgeBase, shared["kb_id"]).status = "archived"
    else:
        db.scalar(select(KnowledgeChunkMetadata).where(KnowledgeChunkMetadata.chunk_id == shared["chunk_id"])).enabled = False
    db.commit()
    ctx = context(member["headers"], db, shared["workspace_id"])
    assert hits(search_tool(ctx)) == []
    response = search_http(client, member["headers"])
    assert response.status_code == 200 and response.json()["items"] == []
