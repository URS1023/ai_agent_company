"""Behavioral checks for workspace lifecycle, login security, OIDC and model cost governance.

中文说明：工作区生命周期、登录安全、OIDC 与模型成本治理的行为检查。
隔离方式：monkeypatch + 内存 SQLite（部分用 create_engine/httpx 桩），不连业务库、不访问外部身份提供方。
"""
from __future__ import annotations

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ids import new_id
from app.models import AuthIdentity, Base, Organization, Workspace
from app.modules.model import service as model_service
from app.modules.system import lifecycle_service
from app.modules.system.models import OidcLoginState, TrustedDevice
from app.modules.system.models import OrganizationProfile, WorkspaceLifecycle

SYSTEM = "/api/v1/system"


def _member_role(client, headers):
    rows = client.get(SYSTEM + "/roles?page_size=100", headers=headers).json()["items"]
    return next(item for item in rows if item["code"] == "member")


def _create_user(client, headers, username):
    role = _member_role(client, headers)
    response = client.post(SYSTEM + "/users", headers=headers, json={
        "username": username, "display_name": "跨区成员", "password": "Temporary12345",
        "role_ids": [role["id"]],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_lifecycle_and_member_copy_move_are_atomic(client, administrator):
    current = client.get("/api/auth/me", headers=administrator).json()["workspace"]
    workspaces = client.get(SYSTEM + "/workspaces", headers=administrator)
    assert workspaces.status_code == 200, workspaces.text
    assert any(item["id"] == current["id"] and item["current"] for item in workspaces.json())
    stale_current = next(item for item in workspaces.json() if item["id"] == current["id"])
    blocked = client.patch(SYSTEM + "/workspaces/" + current["id"], headers=administrator,
                           json={"version": stale_current["version"], "status": "archived"})
    assert blocked.status_code == 409

    created = client.post(SYSTEM + "/workspaces", headers=administrator, json={
        "code": "factory-east", "name": "华东工厂", "description": "设备与质量协作空间",
    })
    assert created.status_code == 201, created.text
    target = created.json()
    assert target["current"] is False and target["actor_role"] == "admin"

    copied_user = _create_user(client, administrator, "workspace-copy-user")
    copied = client.post(SYSTEM + "/workspaces/member-transfer", headers=administrator, json={
        "user_id": copied_user["id"], "target_workspace_id": target["id"],
        "mode": "copy", "target_role_code": "member",
    })
    assert copied.status_code == 200, copied.text
    login = client.post("/api/auth/login", json={
        "username": "workspace-copy-user", "password": "Temporary12345",
    })
    assert login.status_code == 200, login.text
    assert {item["id"] for item in login.json()["workspaces"]} == {current["id"], target["id"]}

    moved_user = _create_user(client, administrator, "workspace-move-user")
    moved = client.post(SYSTEM + "/workspaces/member-transfer", headers=administrator, json={
        "user_id": moved_user["id"], "target_workspace_id": target["id"],
        "mode": "move", "target_role_code": "member",
    })
    assert moved.status_code == 200, moved.text
    moved_login = client.post("/api/auth/login", json={
        "username": "workspace-move-user", "password": "Temporary12345",
    })
    assert moved_login.status_code == 200
    assert [item["id"] for item in moved_login.json()["workspaces"]] == [target["id"]]

    archived = client.patch(SYSTEM + "/workspaces/" + target["id"], headers=administrator,
                            json={"version": target["version"], "status": "archived"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert client.post("/api/auth/login", json={
        "username": "workspace-move-user", "password": "Temporary12345",
    }).status_code == 401
    restored = client.patch(SYSTEM + "/workspaces/" + target["id"], headers=administrator,
                            json={"version": archived.json()["version"], "status": "active"})
    assert restored.status_code == 200 and restored.json()["status"] == "active"


def test_legacy_lifecycle_adoption_is_safe_for_parallel_page_requests(tmp_path):
    """Two page requests may adopt the same legacy rows at the same time."""
    database_path = (tmp_path / "lifecycle.db").as_posix()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        Organization.__table__,
        Workspace.__table__,
        OrganizationProfile.__table__,
        WorkspaceLifecycle.__table__,
    ])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    organization_id = new_id()
    workspace_id = new_id()
    created_at = datetime.utcnow()
    with session_factory() as session:
        session.add_all([
            Organization(id=organization_id, name="并发组织", created_at=created_at),
            Workspace(
                id=workspace_id,
                organization_id=organization_id,
                name="并发工作区",
                created_at=created_at,
            ),
        ])
        session.commit()

    def adopt_profiles(_index):
        with session_factory() as session:
            lifecycle_service.ensure_all_lifecycle_profiles(session)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(adopt_profiles, range(2)))
        with session_factory() as session:
            assert session.query(OrganizationProfile).count() == 1
            assert session.query(WorkspaceLifecycle).count() == 1
    finally:
        engine.dispose()


def test_trusted_device_approval_and_login_evidence(client, administrator):
    policy = client.get(SYSTEM + "/security/policy", headers=administrator)
    assert policy.status_code == 200, policy.text
    updated = client.patch(SYSTEM + "/security/policy", headers=administrator, json={
        "version": policy.json()["version"], "require_trusted_device": True,
        "max_failed_attempts": 3, "lock_minutes": 2, "max_active_sessions": 2,
    })
    assert updated.status_code == 200, updated.text
    raw_device = "browser-device-0123456789abcdef"
    blocked = client.post("/api/auth/login", json={
        "username": "workspace-admin", "password": "IsolatedBootstrap12345",
        "device_id": raw_device,
    })
    assert blocked.status_code == 401
    assert blocked.json()["device_approval_required"] is True
    devices = client.get(SYSTEM + "/security/devices?status=pending", headers=administrator).json()
    assert devices["total"] == 1
    assert raw_device not in json.dumps(devices)
    request = devices["items"][0]
    approved = client.patch(SYSTEM + "/security/devices/" + request["id"], headers=administrator,
                            json={"version": request["version"], "status": "trusted", "label": "验收浏览器"})
    assert approved.status_code == 200, approved.text
    accepted = client.post("/api/auth/login", json={
        "username": "workspace-admin", "password": "IsolatedBootstrap12345",
        "device_id": raw_device,
    })
    assert accepted.status_code == 200, accepted.text
    events = client.get(SYSTEM + "/security/login-events?page_size=100", headers=administrator).json()
    assert {item["outcome"] for item in events["items"]} >= {"success", "blocked"}
    assert raw_device not in json.dumps(events)


def test_model_usage_costs_never_invent_prices_and_budget_is_versioned(client, administrator):
    priced = client.post("/api/models/profiles", headers=administrator, json={
        "profile_name": "finance_reasoning", "provider": "local-compatible",
        "model": "finance-model", "endpoint": "http://127.0.0.1:9999/v1",
        "api_key": "local-test-key", "capabilities": ["chat", "reasoning"],
        "pricing": {"input_price_per_million": 10, "output_price_per_million": 20, "currency": "CNY"},
    })
    assert priced.status_code == 201, priced.text
    profile = next(item for item in client.get("/api/models/profiles", headers=administrator).json()["items"]
                   if item["profile_name"] == "finance_reasoning")
    assert profile["pricing"]["configured"] is True
    workspace_id = administrator["X-Workspace-Id"]
    model_service.record_usage(
        workspace_id=workspace_id, profile_name="finance_reasoning", provider="local-compatible",
        model="finance-model", capability="chat",
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        pricing={"input_price_per_million": 10, "output_price_per_million": 20, "currency": "CNY"},
        source="governance_test", status="completed",
    )
    model_service.record_usage(
        workspace_id=workspace_id, profile_name="embedding", provider="local-compatible",
        model="embedding-model", capability="embedding",
        usage={"prompt_tokens": 2000, "completion_tokens": 0, "total_tokens": 2000},
        pricing=None, source="governance_test", status="completed",
    )
    summary = client.get("/api/models/usage", headers=administrator)
    assert summary.status_code == 200, summary.text
    totals = summary.json()["totals"]
    assert totals["calls"] == 2 and totals["unpriced_calls"] == 1
    assert totals["unpriced_tokens"] == 2000
    assert abs(totals["costs"]["CNY"] - 0.02) < 1e-9

    policy = client.get("/api/models/cost-policy", headers=administrator).json()
    saved = client.put("/api/models/cost-policy", headers=administrator, json={
        "version": policy["version"], "monthly_budget": 100,
        "currency": "CNY", "warn_percent": 75, "hard_limit": True,
    })
    assert saved.status_code == 200, saved.text
    stale = client.put("/api/models/cost-policy", headers=administrator, json={
        "version": policy["version"], "monthly_budget": 200,
        "currency": "CNY", "warn_percent": 75, "hard_limit": True,
    })
    assert stale.status_code == 409
    exported = client.get("/api/models/usage/export", headers=administrator)
    assert exported.status_code == 200
    assert "filename*=UTF-8''" in exported.headers["content-disposition"]
    assert b"local-test-key" not in exported.content


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class _HttpResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


def test_oidc_pkce_signed_callback_auto_provisions_and_state_is_single_use(
    client, administrator, db, monkeypatch,
):
    base = "http://127.0.0.1:19091/oidc"
    created = client.post(SYSTEM + "/sso-providers", headers=administrator, json={
        "code": "factory-id", "display_name": "工厂统一身份",
        "issuer_url": base, "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token", "jwks_uri": base + "/jwks",
        "client_id": "cloud-brain-client", "token_auth_method": "none",
        "allow_private": True, "auto_provision": True,
        "allowed_domains": ["example.com"], "enabled": True,
    })
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    raw_device = "oidc-device-0123456789abcdef"
    started = client.post(f"/api/auth/sso/{provider_id}/authorize", json={"device_id": raw_device})
    assert started.status_code == 200, started.text
    parsed = urlparse(started.json()["authorization_url"])
    query = parse_qs(parsed.query)
    state_value = query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert raw_device not in started.json()["authorization_url"]
    state_row = db.scalar(select(OidcLoginState).where(
        OidcLoginState.state_digest == hashlib.sha256(state_value.encode()).hexdigest(),
    ))
    assert state_row and raw_device not in state_row.verifier_ciphertext

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-key", "alg": "RS256", "use": "sig",
           "n": _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
           "e": _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big"))}
    now = int(datetime.utcnow().timestamp())
    header = _b64url(json.dumps({"alg": "RS256", "kid": "test-key", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({
        "iss": base, "aud": "cloud-brain-client", "sub": "factory-user-1",
        "email": "operator@example.com", "email_verified": True,
        "name": "设备操作员", "preferred_username": "operator@example.com",
        "exp": now + 600, "iat": now, "nonce": state_row.oidc_nonce,
    }, ensure_ascii=False).encode())
    signed = (header + "." + claims).encode()
    signature = private_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    id_token = header + "." + claims + "." + _b64url(signature)

    from app.modules.system import oidc_service
    monkeypatch.setattr(oidc_service.httpx, "post", lambda *args, **kwargs: _HttpResponse({
        "id_token": id_token, "access_token": "access-token-value", "token_type": "Bearer",
    }))
    monkeypatch.setattr(oidc_service.httpx, "get", lambda *args, **kwargs: _HttpResponse({"keys": [jwk]}))
    callback = client.get("/api/auth/sso/callback", params={"code": "one-time-code", "state": state_value})
    assert callback.status_code == 200, callback.text
    assert callback.json()["user"]["username"] == "operator@example.com"
    assert callback.json()["workspaces"][0]["id"] == administrator["X-Workspace-Id"]
    assert db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "factory-user-1")) is not None
    replay = client.get("/api/auth/sso/callback", params={"code": "one-time-code", "state": state_value})
    assert replay.status_code == 401
