"""Behavioral checks for real user, role, organization and resource boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.ids import new_id
from app.models import AuthIdentity, File, LocalCredential, SessionToken, User, Workspace
from app.modules.system.models import MemberProfile, Role
from app.seed import bootstrap

SYSTEM = "/api/v1/system"


def create_role(client, headers, code="file_reader", permissions=None, scope="self"):
    response = client.post(SYSTEM + "/roles", headers=headers, json={
        "code": code, "name": "资料查看者", "permissions": permissions or ["home.view", "files.view"], "data_scope": scope,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_user(client, headers, role_ids, username="test-reader"):
    response = client.post(SYSTEM + "/users", headers=headers, json={
        "username": username, "display_name": "测试成员", "password": "Temporary12345", "role_ids": role_ids,
    })
    assert response.status_code == 201, response.text
    return response.json()


def activate_user(client, username="test-reader"):
    login = client.post("/api/auth/login", json={"username": username, "password": "Temporary12345"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    response = client.post("/api/auth/password", headers=headers, json={
        "current_password": "Temporary12345", "new_password": "ChangedPassword12345",
    })
    assert response.status_code == 200, response.text
    login = client.post("/api/auth/login", json={"username": username, "password": "ChangedPassword12345"})
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["token"]}


def test_startup_has_all_required_modules_and_unique_routes(client):
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    assert "modules.system.router" in ready.json()["modules"]
    keys = [(route.path, method) for route in client.app.routes for method in getattr(route, "methods", [])]
    assert len(keys) == len(set(keys))
    spec = client.app.openapi()
    ids = [operation["operationId"] for methods in spec["paths"].values() for operation in methods.values() if isinstance(operation, dict) and "operationId" in operation]
    assert len(ids) == len(set(ids))


def test_bootstrap_does_not_reset_password_or_unlock_account(client, administrator, db):
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    credential = db.scalar(select(LocalCredential).where(LocalCredential.auth_identity_id == identity.id))
    before = (credential.password_hash, credential.credential_version)
    credential.failed_attempt_count = 3
    credential.locked_until = datetime.utcnow() + timedelta(minutes=5)
    db.commit()
    bootstrap()
    db.refresh(credential)
    assert (credential.password_hash, credential.credential_version) == before
    assert credential.failed_attempt_count == 3
    assert credential.locked_until is not None


def test_organization_tree_rejects_cycle_and_preserves_record(client, administrator):
    root = client.post(SYSTEM + "/departments", headers=administrator, json={"code": "root", "name": "研发中心"}).json()
    child = client.post(SYSTEM + "/departments", headers=administrator, json={"code": "child", "name": "设备组", "parent_id": root["id"]}).json()
    response = client.patch(SYSTEM + "/departments/" + root["id"], headers=administrator,
                            json={"version": root["version"], "parent_id": child["id"]})
    assert response.status_code == 422, response.text
    assert client.get(SYSTEM + "/departments/" + root["id"], headers=administrator).json()["parent_id"] is None
    assert client.delete(SYSTEM + "/departments/" + root["id"], headers=administrator).status_code == 409


def test_role_validation_and_optimistic_concurrency(client, administrator):
    invalid = client.post(SYSTEM + "/roles", headers=administrator, json={"code": "bad", "name": "非法角色", "permissions": ["invented.all"]})
    assert invalid.status_code == 422
    role = create_role(client, administrator)
    updated = client.patch(SYSTEM + "/roles/" + role["id"], headers=administrator,
                           json={"version": role["version"], "name": "已修改"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] > role["version"]
    stale = client.patch(SYSTEM + "/roles/" + role["id"], headers=administrator,
                         json={"version": role["version"], "name": "旧表单覆盖"})
    assert stale.status_code == 409


def test_temporary_password_and_direct_api_permissions(client, administrator):
    role = create_role(client, administrator)
    user = create_user(client, administrator, [role["id"]])
    login = client.post("/api/auth/login", json={"username": "test-reader", "password": "Temporary12345"}).json()
    temporary = {"Authorization": "Bearer " + login["token"]}
    assert login["user"]["must_change_password"] is True
    assert client.get("/api/files", headers=temporary).status_code == 403
    headers = activate_user(client)
    assert client.get("/api/files", headers=headers).status_code == 200
    assert client.get(SYSTEM + "/users", headers=headers).status_code == 403
    assert client.delete("/api/files/not-owned", headers=headers).status_code == 403
    assert client.get("/api/auth/me", headers=temporary).status_code == 401


def test_explicit_empty_roles_never_fall_back_after_restart(client, administrator):
    role = create_role(client, administrator)
    user = create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    response = client.patch(SYSTEM + "/users/" + user["id"], headers=administrator,
                            json={"version": user["version"], "role_ids": []})
    assert response.status_code == 200, response.text
    assert client.get("/api/files", headers=headers).status_code == 403
    bootstrap()
    assert client.get("/api/files", headers=headers).status_code == 403


def test_role_changes_apply_to_existing_sessions(client, administrator):
    role = create_role(client, administrator)
    create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    assert client.get("/api/files", headers=headers).status_code == 200
    response = client.patch(SYSTEM + "/roles/" + role["id"], headers=administrator,
                            json={"version": role["version"], "permissions": ["home.view"]})
    assert response.status_code == 200, response.text
    assert client.get("/api/files", headers=headers).status_code == 403


def test_disabling_account_and_password_reset_revoke_sessions(client, administrator):
    role = create_role(client, administrator)
    user = create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    response = client.post(SYSTEM + "/users/" + user["id"] + "/password-reset", headers=administrator,
                           json={"new_password": "AnotherTemporary123"})
    assert response.status_code == 200, response.text
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert "AnotherTemporary123" not in response.text
    disabled = client.patch(SYSTEM + "/users/" + user["id"], headers=administrator,
                            json={"version": user["version"], "status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    assert client.post("/api/auth/login", json={"username": "test-reader", "password": "AnotherTemporary123"}).status_code == 401


def test_final_administrator_cannot_be_removed_disabled_or_demoted(client, administrator):
    me = client.get("/api/auth/me", headers=administrator).json()
    user = client.get(SYSTEM + "/users/" + me["user"]["id"], headers=administrator).json()
    for fields in ({"status": "disabled"}, {"role_ids": []}):
        response = client.patch(SYSTEM + "/users/" + user["id"], headers=administrator, json={"version": user["version"], **fields})
        assert response.status_code == 409, response.text
    assert client.delete(SYSTEM + "/users/" + user["id"], headers=administrator).status_code == 409


def test_foreign_workspace_role_ids_are_rejected(client, administrator, db):
    current = db.get(Workspace, administrator["X-Workspace-Id"])
    workspace = Workspace(id=new_id(), organization_id=current.organization_id, name="隔离工作区", created_at=datetime.utcnow())
    db.add(workspace)
    db.flush()
    role = Role(workspace_id=workspace.id, code="foreign", name="其他工作区角色", permissions=["system.admin"], data_scope="workspace")
    db.add(role)
    db.commit()
    response = client.post(SYSTEM + "/users", headers=administrator, json={
        "username": "cross-workspace", "display_name": "越权用户", "password": "Temporary12345", "role_ids": [role.id],
    })
    assert response.status_code == 404, response.text
    assert client.get(SYSTEM + "/roles/" + role.id, headers=administrator).status_code == 404


def test_self_data_scope_filters_real_files_and_downloads(client, administrator, db):
    role = create_role(client, administrator, permissions=["home.view", "files.view", "files.export"])
    user = create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    admin_id = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    ids = []
    for owner, name in [(user["id"], "自己的资料.txt"), (admin_id, "其他人的财务数据.txt")]:
        record = File(id=new_id(), workspace_id=administrator["X-Workspace-Id"], owner_user_id=owner,
                      name=name, mime_type="text/plain", size=0, storage_key=new_id(), sha256="0" * 64,
                      source_type="upload", status="ready", created_at=datetime.utcnow())
        db.add(record)
        ids.append(record.id)
    db.commit()
    response = client.get("/api/files", headers=headers)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [ids[0]]
    assert client.get("/api/files/" + ids[1] + "/download", headers=headers).status_code in {403, 404}
    assert len(client.get("/api/files", headers=administrator).json()["items"]) == 2


def test_validation_does_not_echo_password_and_settings_are_typed(client, administrator):
    invalid = client.post(SYSTEM + "/users", headers=administrator,
                          json={"username": "bad", "display_name": "错误", "password": "secret"})
    assert invalid.status_code == 422
    assert "secret" not in invalid.text
    assert client.put(SYSTEM + "/settings/ui.page_size", headers=administrator, json={"value": 1000}).status_code == 422
    assert client.put(SYSTEM + "/settings/model.api_key", headers=administrator, json={"value": "do-not-store"}).status_code == 422
    rejected = client.put(SYSTEM + "/settings/site.name", headers=administrator, json={"value": "其他产品名称"})
    assert rejected.status_code == 422
    assert "智慧云脑 Cloud Brain" in rejected.text
    response = client.put(SYSTEM + "/settings/site.name", headers=administrator, json={"value": "智慧云脑 Cloud Brain"})
    assert response.status_code == 200
    assert client.get("/api/v1/platform/navigation", headers=administrator).json()["settings"]["site.name"] == "智慧云脑 Cloud Brain"


def test_session_expiry_is_enforced(client, administrator, db):
    token = administrator["Authorization"].removeprefix("Bearer ")
    import hashlib
    session = db.scalar(select(SessionToken).where(SessionToken.token_digest == hashlib.sha256(token.encode()).hexdigest()))
    session.idle_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=administrator).status_code == 401


def test_api_collection_and_spec_include_system_actions_without_tokens(client, administrator):
    spec = client.get("/api/v1/platform/openapi.json", headers=administrator)
    assert spec.status_code == 200
    assert SYSTEM + "/users" in spec.json()["paths"]
    collection = client.get("/api/v1/platform/api-collection", headers=administrator)
    assert collection.status_code == 200
    assert "{{access_token}}" in collection.text
    assert administrator["Authorization"].removeprefix("Bearer ") not in collection.text
    assert any(group["name"] == "系统管理" for group in collection.json()["item"])


def test_business_home_is_not_ai_and_menu_name_can_be_restored(client, administrator):
    assert 'id="platform-root"' in client.get("/").text
    assert 'id="prompt-textarea"' not in client.get("/").text
    assert 'id="prompt-textarea"' in client.get("/ai").text
    assert client.get("/system/users").status_code == 200
    api_legacy = client.get("/system/api", follow_redirects=False)
    assert api_legacy.status_code == 307
    assert api_legacy.headers["location"] == "/system/development"
    assert client.get("/system/not-a-real-page").status_code == 404
    menu_rows = client.get(SYSTEM + "/menus", headers=administrator).json()["items"]
    consolidated = {item["id"]: item for item in menu_rows if item["id"] in {"runs", "monitor", "audit", "api"}}
    assert set(consolidated) == {"runs", "monitor", "audit", "api"}
    assert all(item["available"] and not item["visible"] for item in consolidated.values())
    renamed = client.put(SYSTEM + "/menus/files", headers=administrator,
                         json={"label": "资料目录", "visible": True, "sort_order": 20})
    assert renamed.status_code == 200
    restored = client.put(SYSTEM + "/menus/files", headers=administrator,
                          json={"label": "  ", "visible": True, "sort_order": 20})
    assert restored.status_code == 200, restored.text
    assert restored.json()["label"] == "文件与生成结果"


def test_people_scope_applies_to_list_detail_edits_and_password_resets(client, administrator, db):
    department = client.post(SYSTEM + "/departments", headers=administrator,
                             json={"code": "quality", "name": "质量部门"}).json()
    role = create_role(client, administrator, "department-manager", permissions=[
        "home.view", "system.users.view", "system.users.manage",
        "system.users.reset_password", "system.permissions.view",
    ], scope="department")
    manager = create_user(client, administrator, [role["id"]], "department-manager")
    colleague = create_user(client, administrator, [], "department-colleague")
    outsider = create_user(client, administrator, [], "other-department")
    for user_id in (manager["id"], colleague["id"]):
        profile = db.scalar(select(MemberProfile).where(MemberProfile.user_id == user_id))
        profile.department_id = department["id"]
    db.commit()
    headers = activate_user(client, "department-manager")
    visible = client.get(SYSTEM + "/users", headers=headers)
    assert visible.status_code == 200, visible.text
    assert {row["id"] for row in visible.json()["items"]} == {manager["id"], colleague["id"]}
    assert client.get(SYSTEM + "/users/" + outsider["id"], headers=headers).status_code == 403
    assert client.get(SYSTEM + "/users/" + outsider["id"] + "/permissions", headers=headers).status_code == 403
    assert client.patch(SYSTEM + "/users/" + outsider["id"], headers=headers,
                        json={"version": outsider["version"], "phone": "12345"}).status_code == 403
    assert client.post(SYSTEM + "/users/" + outsider["id"] + "/password-reset", headers=headers,
                       json={"new_password": "ResetPassword123"}).status_code == 403
    reset = client.post(SYSTEM + "/users/" + colleague["id"] + "/password-reset", headers=headers,
                        json={"new_password": "ResetPassword123"})
    assert reset.status_code == 200, reset.text


def test_api_contract_requires_authentication_and_declares_bearer_security(client, administrator):
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/api/v1/platform/openapi.json").status_code == 401
    spec = client.get("/api/v1/platform/openapi.json", headers=administrator).json()
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert spec["paths"][SYSTEM + "/users"]["get"]["security"] == [{"HTTPBearer": []}]


def test_model_and_audit_viewers_do_not_need_blanket_admin_permissions(client, administrator):
    role = create_role(client, administrator, "operations-reader", permissions=["home.view", "models.view", "audit.view"], scope="workspace")
    create_user(client, administrator, [role["id"]], "operations-reader")
    headers = activate_user(client, "operations-reader")
    assert client.get("/api/models/profiles", headers=headers).status_code == 200
    assert client.get("/api/audit", headers=headers).status_code == 200
    assert client.get(SYSTEM + "/users", headers=headers).status_code == 403
    assert client.post("/api/models/probe/reasoning", headers=headers).status_code == 403
