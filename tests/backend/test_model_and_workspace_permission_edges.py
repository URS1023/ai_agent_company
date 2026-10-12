"""隔离验证模型动作、资源范围及旧账号入口，全部模型响应由本地桩给出。"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import AuthIdentity, ModelProbeResult, ModelProfile, Secret, User, WorkspaceMember
from app.modules.system.models import MemberProfile, Role, UserRole
from test_system_foundation import activate_user, create_role, create_user


def profile_payload(name="protected", **overrides):
    return {"profile_name": name, "provider": "isolated", "model": "original-model",
            "endpoint": "https://model-fixture.invalid/v1", "api_key": "",
            "capabilities": ["chat"], **overrides}


def member(client, administrator, permissions, *, scope="workspace", username="model-operator"):
    role = create_role(client, administrator, code=username, permissions=permissions, scope=scope)
    create_user(client, administrator, [role["id"]], username=username)
    return activate_user(client, username)


@pytest.mark.parametrize("scope", ["workspace", "self"])
def test_create_permission_cannot_replace_visible_or_hidden_profile(client, administrator, db, scope):
    saved = client.post("/api/models/profiles", headers=administrator,
                        json=profile_payload(api_key="synthetic-private-key"))
    assert saved.status_code == 201
    headers = member(client, administrator, ["models.create"], scope=scope)
    fresh = client.post("/api/models/profiles", headers=headers, json=profile_payload("fresh-profile"))
    assert fresh.status_code == 201, fresh.text
    before_secret_count = db.scalar(select(func.count()).select_from(Secret))
    response = client.post("/api/models/profiles", headers=headers,
                           json=profile_payload(model="unauthorized-model", api_key="synthetic-replacement"))
    assert response.status_code == 403, response.text
    db.expire_all()
    rows = db.scalars(select(ModelProfile).where(ModelProfile.profile_name == "protected")).all()
    assert len(rows) == 1 and rows[0].model == "original-model"
    assert db.scalar(select(func.count()).select_from(Secret)) == before_secret_count


def test_post_upsert_requires_edit_scope_even_when_create_scope_is_workspace(client, administrator, db):
    assert client.post("/api/models/profiles", headers=administrator, json=profile_payload()).status_code == 201
    create = create_role(client, administrator, code="model-create", permissions=["models.create"], scope="workspace")
    edit = create_role(client, administrator, code="model-edit", permissions=["models.edit"], scope="self")
    create_user(client, administrator, [create["id"], edit["id"]])
    response = client.post("/api/models/profiles", headers=activate_user(client),
                           json=profile_payload(model="unauthorized-model"))
    assert response.status_code == 403, response.text
    db.expire_all()
    assert db.scalar(select(ModelProfile)).model == "original-model"


@pytest.mark.parametrize("name,permission", [("export", "models.export"), ("probe", "models.execute"),
                                             ("test", "models.execute"), ("publish", "models.publish")])
def test_dynamic_model_name_cannot_supply_update_permission(client, administrator, db, name, permission):
    headers = member(client, administrator, [permission])
    payload = profile_payload(name)
    payload.pop("profile_name")
    response = client.put("/api/models/profiles/" + name, headers=headers, json=payload)
    assert response.status_code == 403, response.text
    assert db.scalar(select(func.count()).select_from(ModelProfile)) == 0


@pytest.mark.parametrize("name", ["export", "probe", "test", "publish"])
def test_editor_can_update_profiles_with_action_words(client, administrator, name):
    headers = member(client, administrator, ["models.edit"])
    payload = profile_payload(name)
    payload.pop("profile_name")
    response = client.put("/api/models/profiles/" + name, headers=headers, json=payload)
    assert response.status_code == 200, response.text


def test_true_probe_and_export_routes_keep_their_distinct_permissions(client, administrator, monkeypatch):
    from app.modules.model import service
    calls = []
    monkeypatch.setattr(service, "probe_effective_profile", lambda *args, **kwargs: calls.append(kwargs["profile_name"]) or {"status": "passed"})
    executor = member(client, administrator, ["models.execute"], username="model-executor")
    exporter = member(client, administrator, ["models.export"], username="model-exporter")
    assert client.post("/api/models/probe/export", headers=executor).status_code == 200
    assert client.post("/api/models/probe/export", headers=exporter).status_code == 403
    assert client.get("/api/models/usage/export", headers=exporter).status_code == 200
    assert client.get("/api/models/usage/export", headers=executor).status_code == 403
    assert calls == ["export"]


@pytest.mark.parametrize("assign_roles", [False, True])
def test_legacy_user_create_cannot_grant_admin_from_non_admin_role(client, administrator, db, assign_roles):
    permissions = ["system.admin", "system.users.manage"]
    if assign_roles:
        permissions.append("system.users.assign_roles")
    headers = member(client, administrator, permissions, username="legacy-operator")
    before = tuple(db.scalar(select(func.count()).select_from(model)) for model in (User, AuthIdentity, WorkspaceMember))
    response = client.post("/api/workspace/users", headers=headers, json={
        "username": "unauthorized-admin", "password": "SyntheticPassword123", "role": "admin",
    })
    assert response.status_code == 403, response.text
    db.expire_all()
    assert tuple(db.scalar(select(func.count()).select_from(model)) for model in (User, AuthIdentity, WorkspaceMember)) == before


@pytest.mark.parametrize("role,code", [("admin", "workspace_admin"), ("member", "member")])
def test_legacy_administrator_creation_initializes_real_role_bindings(client, administrator, db, role, code):
    response = client.post("/api/workspace/users", headers=administrator, json={
        "username": "valid-legacy-user", "password": "SyntheticPassword123", "role": role,
    })
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["role"] == role and result["username"] == "valid-legacy-user"
    assert db.scalar(select(MemberProfile).where(MemberProfile.user_id == result["id"])).roles_initialized
    assert db.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == result["id"])).all() == [code]


@pytest.mark.parametrize("capability,provider_function", [("embedding", "embed_texts"), ("vision", "analyze_image")])
def test_probe_uses_requested_custom_profile(client, administrator, monkeypatch, capability, provider_function):
    from app.providers import model
    name = "custom-" + capability
    assert client.post("/api/models/profiles", headers=administrator,
                       json=profile_payload(name, capabilities=[capability], api_key="synthetic-probe-key")).status_code == 201
    observed = []
    def provider(*args, **kwargs):
        observed.append(kwargs.get("profile_name"))
        return [[0.1, 0.2]] if capability == "embedding" else {"content": "测试图像"}
    monkeypatch.setattr(model, provider_function, provider)
    response = client.post("/api/models/probe/" + name, headers=administrator)
    assert response.status_code == 200 and response.json()["status"] == "passed", response.text
    assert observed == [name]


def test_probe_failure_never_persists_or_returns_upstream_secret(client, administrator, db, monkeypatch, caplog):
    from app.providers import model
    marker = "synthetic-secret-marker-never-publish"
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "base_url": "https://model-fixture.invalid", "api_key": marker, "model": "fixture",
    })
    def fail(*args, **kwargs):
        raise RuntimeError("upstream echoed Authorization Bearer " + marker)
    # 保留真实 provider 异常与日志路径，只替换最末端 SDK 请求，不发送网络请求。
    monkeypatch.setattr(model, "_client", lambda *args, **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(model, "_create_chat_completion", fail)
    assert client.post("/api/models/profiles", headers=administrator, json=profile_payload()).status_code == 201
    response = client.post("/api/models/probe/protected", headers=administrator)
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert marker not in response.text
    row = db.scalar(select(ModelProbeResult))
    assert marker not in json.dumps(row.details_json)
    assert marker not in client.get("/api/models/profiles", headers=administrator).text
    assert marker not in caplog.text


@pytest.mark.parametrize("business_transport", [False, True])
def test_custom_vision_profile_reaches_dispatch_and_usage(monkeypatch, business_transport):
    from app.business_worker import transport
    from app.config import settings
    from app.providers import model
    monkeypatch.setattr(settings, "VISION_ENABLED", True)
    seen = {"config": [], "dispatch": [], "usage": []}
    def configuration(name, **kwargs):
        seen["config"].append(name)
        return {"base_url": "https://vision-fixture.invalid/v1", "api_key": "synthetic-key",
                "model": "custom-vision-model", "provider": "fixture"}
    monkeypatch.setattr(model, "_profile_config", configuration)
    monkeypatch.setattr(model, "_governed_call", lambda *args: None)
    monkeypatch.setattr(model, "_persist_usage", lambda **kwargs: seen["usage"].append(kwargs["profile_name"]))
    monkeypatch.setattr(transport, "active", lambda: business_transport)
    def native_generate(profile, *args, **kwargs):
        seen["dispatch"].append(profile)
        return {"content": "图像事实", "usage": {}}
    monkeypatch.setattr(transport, "generate", native_generate)
    def sdk_generate(**kwargs):
        seen["dispatch"].append(kwargs["model"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="图像事实"))], usage=None)
    monkeypatch.setattr(model, "_client", lambda *args: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_generate))))
    result = model.analyze_image(b"synthetic-png", "image/png", profile_name="custom-vision", workspace_id="fixture-workspace")
    assert result["content"] == "图像事实"
    assert seen["config"] == ["custom-vision"]
    assert seen["dispatch"] == ["custom-vision" if business_transport else "custom-vision-model"]
    assert seen["usage"] == ([] if business_transport else ["custom-vision"])


@pytest.mark.parametrize("status,message", [(401, "认证"), (429, "限流"), (503, "暂不可用")])
def test_probe_error_categories_do_not_echo_original_message(status, message):
    from app.modules.model.service import _safe_probe_error
    error = RuntimeError("synthetic-confidential-message")
    error.status_code = status
    result = _safe_probe_error(error)
    assert message in result and "synthetic-confidential-message" not in result


@pytest.mark.parametrize("name", ["protected", "reasoning"])
def test_named_probe_cannot_execute_outside_model_scope(client, administrator, monkeypatch, name):
    from app.providers import model
    saved = client.post("/api/models/profiles", headers=administrator,
                        json=profile_payload(name, api_key="synthetic-key"))
    assert saved.status_code == 201
    headers = member(client, administrator, ["models.execute"], scope="self")
    calls = []
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: calls.append(args[0]) or {"content": "ok"})
    response = client.post("/api/models/probe/" + name, headers=headers)
    assert response.status_code == 404, response.text
    assert client.post("/api/models/profiles/" + saved.json()["id"] + "/probe", headers=headers).status_code == 404
    assert calls == []


@pytest.mark.parametrize("environment", [False, True])
def test_legacy_probe_error_is_not_republished_or_implicitly_deleted(client, administrator, db, environment):
    from app.ids import new_id
    from app.modules.model.models import EnvironmentModelProbe
    from app.modules.model.service import _environment_fingerprint
    marker = "synthetic-legacy-private-error"
    if environment:
        name = "embedding"
        row = EnvironmentModelProbe(workspace_id=administrator["X-Workspace-Id"], profile_name=name,
                                    config_fingerprint=_environment_fingerprint(name), status="failed",
                                    error_code="probe_error", details_json={"error": marker})
    else:
        name = "protected"
        saved = client.post("/api/models/profiles", headers=administrator, json=profile_payload(name)).json()
        row = ModelProbeResult(id=new_id(), workspace_id=administrator["X-Workspace-Id"],
                               profile_id=saved["id"], status="failed", error_code="probe_error",
                               details_json={"error": marker}, created_at=datetime.utcnow())
    db.add(row)
    db.commit()
    response = client.get("/api/models/profiles", headers=administrator)
    assert response.status_code == 200 and marker not in response.text
    profile = next(item for item in response.json()["items"] if item["profile_name"] == name)
    assert profile["last_probe"]["status"] == "failed"
    assert "模型探测失败" in profile["last_probe"]["details"]["error"]
    db.refresh(row)
    assert row.details_json["error"] == marker
