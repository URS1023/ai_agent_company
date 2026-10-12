"""模型高级配置经过正式 API 校验、隔离存储并映射到 Harness；不调用真实模型。"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.ids import new_id
from app.models import ModelProfile, Secret, Workspace
from app.modules.system.models import ResourceGrant
from app.modules.harness.context import model_configuration
from app.providers.model import _profile_config
from test_system_foundation import activate_user, create_role, create_user


def payload(**overrides):
    """使用无网络请求的合成模型配置。"""
    return {"profile_name": "runtime-fixture", "provider": "compatible", "model": "fixture-chat",
            "endpoint": "https://model.invalid/v1", "api_key": "fixture-secret", "capabilities": ["chat", "tools"],
            **overrides}


def configured(client, headers):
    """只读取管理端脱敏配置。"""
    return next(item for item in client.get("/api/models/profiles", headers=headers).json()["items"]
                if item["profile_name"] == "runtime-fixture")


def test_runtime_options_api_roundtrip_maps_actual_configuration(client, administrator, db):
    options = {"protocol": "openai-completions", "context_window": 65536, "max_output_tokens": 8192,
               "reasoning_effort": "low", "input_modalities": ["text", "image"],
               "compat": {"maxTokensField": "max_completion_tokens", "supportsStore": False}}
    created = client.post("/api/models/profiles", headers=administrator, json=payload(runtime_options=options))
    assert created.status_code == 201, created.text
    assert created.json()["runtime_options"] == options
    public = configured(client, administrator)
    assert public["runtime_options"] == options and public["capabilities"] == ["chat", "tools"]
    assert "fixture-secret" not in str(public) and "secret_id" not in str(public)
    config = _profile_config("runtime-fixture", administrator["X-Workspace-Id"])
    wire = model_configuration(config)
    assert wire["contextWindow"] == 65536 and wire["maxTokens"] == 8192
    assert wire["inputModalities"] == ["text", "image"]
    assert wire["reasoningEfforts"] == {"off": None, "low": "low"}
    assert wire["compat"] == options["compat"]
    row = db.scalar(select(ModelProfile).where(ModelProfile.profile_name == "runtime-fixture"))
    original_key = row.capabilities_json["secret_id"]
    update = payload(api_key="", model="updated-model")
    update.pop("profile_name")
    saved = client.put("/api/models/profiles/runtime-fixture", headers=administrator, json=update)
    assert saved.status_code == 200 and saved.json()["runtime_options"] == options
    db.expire_all()
    assert db.get(ModelProfile, row.id).capabilities_json["secret_id"] == original_key
    reset = client.put("/api/models/profiles/runtime-fixture", headers=administrator, json={**update, "runtime_options": {}})
    assert reset.status_code == 200 and configured(client, administrator)["runtime_options"] == {}


@pytest.mark.parametrize("options", [
    {"protocol": "unsupported"}, {"context_window": "65536"},
    {"context_window": 8192, "max_output_tokens": 8192},
    {"context_window": 1}, {"max_output_tokens": -1},
    {"secret_id": "external-secret"}, {"compat": {"headers": {"Authorization": "unsafe"}}},
    {"compat": {"supportsStore": "false"}}, {"input_modalities": ["text", "text"]},
    {"protocol": "deepseek-messages", "reasoning_effort": "medium"},
    {"protocol": "deepseek-messages", "compat": {"maxTokensField": "max_tokens"}},
])
def test_invalid_runtime_options_cannot_create_profile_or_rotate_secret(client, administrator, db, options):
    response = client.post("/api/models/profiles", headers=administrator, json=payload(runtime_options=options))
    assert response.status_code == 422, response.text
    assert db.scalar(select(ModelProfile).where(ModelProfile.profile_name == "runtime-fixture")) is None
    assert db.scalar(select(Secret).where(Secret.status == "active")) is None


@pytest.mark.parametrize("scope", ["self", "workspace"])
def test_runtime_options_preserve_model_administration_authorization(client, administrator, db, scope):
    saved = client.post("/api/models/profiles", headers=administrator, json=payload(runtime_options={"context_window": 65536}))
    assert saved.status_code == 201
    profile_id = saved.json()["id"]
    role = create_role(client, administrator, code="runtime_reader", permissions=["home.view", "models.view"], scope=scope)
    create_user(client, administrator, [role["id"]], username="runtime-reader")
    reader = activate_user(client, username="runtime-reader")
    listed = client.get("/api/models/profiles", headers=reader)
    assert listed.status_code == 200
    visible = {item["profile_name"] for item in listed.json()["items"]}
    assert ("runtime-fixture" in visible) is (scope == "workspace")
    assert client.post("/api/models/profiles", headers=reader, json=payload(runtime_options={"protocol": "deepseek-messages"})).status_code == 403
    assert client.put("/api/models/profiles/runtime-fixture", headers=reader, json=payload(runtime_options={})).status_code == 403
    assert client.post("/api/models/profiles", json=payload(runtime_options={})).status_code == 401
    if scope == "self":
        db.add(ResourceGrant(workspace_id=administrator["X-Workspace-Id"], resource_type="models",
                             resource_id=profile_id, subject_type="role", subject_id=role["id"], actions=["view"]))
        db.commit()
        assert configured(client, reader)["runtime_options"] == {"context_window": 65536}
    home_workspace = db.get(Workspace, administrator["X-Workspace-Id"])
    foreign = Workspace(id=new_id(), organization_id=home_workspace.organization_id, name="外部隔离工作区", created_at=datetime.utcnow())
    db.add(foreign)
    db.flush()
    db.add(ModelProfile(id=new_id(), workspace_id=foreign.id, profile_name="foreign-fixture", provider="fixture",
                        model="private-model", endpoint="https://foreign.invalid", capabilities_json={"capabilities": ["chat"]},
                        status="active", enabled=True, created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
    db.commit()
    listed = client.get("/api/models/profiles", headers=reader)
    assert "foreign-fixture" not in listed.text
    assert client.get("/api/models/profiles", headers={**reader, "X-Workspace-Id": foreign.id}).status_code in {401, 403}


def test_reasoning_off_and_ordinary_compatible_defaults_are_explicit(client, administrator):
    ordinary = model_configuration({"base_url": "https://model.invalid/v1", "model": "fixture", "capabilities": {}})
    assert "reasoningEffort" not in ordinary and "thinking" not in ordinary
    response = client.post("/api/models/profiles", headers=administrator, json=payload(
        runtime_options={"protocol": "openai-completions", "reasoning_effort": "off"}))
    assert response.status_code == 201
    wire = model_configuration(_profile_config("runtime-fixture", administrator["X-Workspace-Id"]))
    assert wire["reasoningEfforts"] is False and "reasoningEffort" not in wire
    response = client.post("/api/models/profiles", headers=administrator, json=payload(
        endpoint="https://api.deepseek.com/v1", runtime_options={"reasoning_effort": "off"}))
    assert response.status_code == 201
    wire = model_configuration(_profile_config("runtime-fixture", administrator["X-Workspace-Id"]))
    assert wire["baseURL"] == "https://api.deepseek.com/anthropic"
    assert wire["thinking"] == "disabled" and wire["reasoningEffort"] == "off"
