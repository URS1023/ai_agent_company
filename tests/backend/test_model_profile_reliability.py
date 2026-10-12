"""The model administration status must match the actual provider boundary.

中文说明：模型管理页展示的状态必须与真实提供方边界一致（例如缺密钥不能显示为可用）。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不发起真实模型请求。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.errors import ModelError
from app.models import ModelProfile, Secret
from app.providers.model import _profile_config


def test_environment_probe_persists_success_and_failure_and_invalidates_changed_config(client, administrator, monkeypatch):
    from app.providers import model
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "base_url": "https://isolated-model.invalid", "api_key": "test", "model": "test-embedding",
    })
    monkeypatch.setattr(model, "embed_texts", lambda *args, **kwargs: [[0.1, 0.2]])
    response = client.post("/api/models/probe/embedding", headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "passed"
    def profile():
        return next(item for item in client.get("/api/models/profiles", headers=administrator).json()["items"]
                    if item["profile_name"] == "embedding")
    assert profile()["source"] == "environment"
    assert profile()["last_probe"]["details"]["dimensions"] == 2
    def fail(*args, **kwargs):
        raise ModelError("模型端点未配置")
    monkeypatch.setattr(model, "_profile_config", fail)
    failed = client.post("/api/models/probe/embedding", headers=administrator)
    assert failed.status_code == 200, failed.text
    assert profile()["last_probe"]["status"] == "failed"
    monkeypatch.setattr(settings, "SILICONFLOW_EMBED_MODEL", "different-model")
    assert profile()["last_probe"] is None


def _save(client, headers, **overrides):
    response = client.post("/api/models/profiles", headers=headers, json={
        "profile_name": "reasoning", "provider": "agnes", "model": "test-model",
        "endpoint": "https://isolated-model.invalid/v1", "api_key": "",
        "capabilities": ["chat"], **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_custom_endpoint_does_not_claim_environment_key_is_configured(client, administrator, monkeypatch):
    monkeypatch.setattr(settings, "AGNES_API_KEY", "test-environment-key")
    monkeypatch.setattr(settings, "AGNES_BASE_URL", "https://configured-provider.invalid/v1")
    _save(client, administrator)
    profiles = client.get("/api/models/profiles", headers=administrator).json()["items"]
    profile = next(item for item in profiles if item["profile_name"] == "reasoning")
    assert profile["api_key_configured"] is False
    assert profile["secret_source"] == "missing"
    with pytest.raises(ModelError, match="专用密钥"):
        _profile_config("reasoning", administrator["X-Workspace-Id"])


def test_disabled_profile_cannot_fall_back_to_environment(client, administrator, monkeypatch):
    monkeypatch.setattr(settings, "AGNES_API_KEY", "test-environment-key")
    _save(client, administrator, api_key="test-profile-key", enabled=False)
    with pytest.raises(ModelError, match="停用"):
        _profile_config("reasoning", administrator["X-Workspace-Id"])


def test_repeated_profile_key_updates_leave_exactly_one_active_key(client, administrator, db):
    first = _save(client, administrator, api_key="test-first-key")
    for index in range(3):
        _save(client, administrator, api_key=f"test-updated-key-{index}")
    profiles = db.scalars(select(ModelProfile).where(ModelProfile.status == "active")).all()
    keys = db.scalars(select(Secret).where(Secret.status == "active")).all()
    assert len(profiles) == 1 and profiles[0].id == first["id"]
    assert len(keys) == 1 and keys[0].id == profiles[0].capabilities_json["secret_id"]


def test_delete_button_disables_model_and_same_name_can_be_reconfigured(
    client, administrator, db, monkeypatch,
):
    monkeypatch.setattr(settings, "AGNES_API_KEY", "test-environment-key")
    first = _save(client, administrator, api_key="test-first-key")
    original = db.get(ModelProfile, first["id"])
    old_secret_id = original.capabilities_json["secret_id"]

    stopped = client.delete(f"/api/models/profiles/{first['id']}", headers=administrator)
    assert stopped.status_code == 200, stopped.text
    profile = next(item for item in client.get("/api/models/profiles", headers=administrator).json()["items"]
                   if item["profile_name"] == "reasoning")
    assert profile["id"] == first["id"]
    assert profile["source"] == "database" and profile["enabled"] is False
    assert profile["api_key_configured"] is False
    with pytest.raises(ModelError, match="停用"):
        _profile_config("reasoning", administrator["X-Workspace-Id"])
    db.expire_all()
    assert db.get(Secret, old_secret_id).status == "revoked"

    restarted = _save(client, administrator, api_key="test-reconfigured-key", enabled=True)
    assert restarted["id"] == first["id"]
    configured = _profile_config("reasoning", administrator["X-Workspace-Id"])
    assert configured["source"] == "database" and configured["api_key"] == "test-reconfigured-key"
    db.expire_all()
    assert len(db.scalars(select(ModelProfile)).all()) == 1
    assert len(db.scalars(select(Secret).where(Secret.status == "active")).all()) == 1
