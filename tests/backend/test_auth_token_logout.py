"""隔离验证退出仅撤销当前令牌，不影响兄弟会话、服务令牌或客户端。"""
from __future__ import annotations

import hashlib
import os

from sqlalchemy import select

from app.models import SessionToken
from app.modules.auth.service import logout
from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient


def create_service_client(client, administrator):
    response = client.post("/api/v1/system-tools/integration-clients", headers=administrator,
                           json={"name": "退出令牌隔离验收", "redirectUris": [], "scopes": ["files.view"]})
    assert response.status_code == 201, response.text
    configured = response.json()
    return configured, {"clientId": configured["clientId"], "clientSecret": configured["clientSecret"],
                        "workspaceId": administrator["X-Workspace-Id"]}


def issue_service_token(client, credentials):
    response = client.post("/api/auth/client-token", json=credentials)
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"],
            "X-Workspace-Id": credentials["workspaceId"]}


def token_row(db, model, headers):
    raw = headers["Authorization"].removeprefix("Bearer ")
    return db.scalar(select(model).where(model.token_digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()))


def test_service_logout_revokes_only_current_token_and_client_can_issue_again(client, administrator, db):
    configured, credentials = create_service_client(client, administrator)
    current = issue_service_token(client, credentials)
    sibling = issue_service_token(client, credentials)
    assert client.get("/api/files", headers=current).status_code == 200
    assert client.get("/api/files", headers=sibling).status_code == 200

    response = client.post("/api/auth/logout", headers=current)
    assert response.status_code == 200 and response.json() == {"ok": True}
    denied = client.get("/api/files", headers=current)
    assert denied.status_code == 401
    assert client.post("/api/auth/logout", headers=current).status_code == 401
    assert client.get("/api/files", headers=sibling).status_code == 200
    assert client.get("/api/auth/me", headers=administrator).status_code == 200
    refreshed = issue_service_token(client, credentials)
    assert client.get("/api/files", headers=refreshed).status_code == 200

    db.expire_all()
    assert token_row(db, IntegrationAccessToken, current).revoked_at is not None
    assert token_row(db, IntegrationAccessToken, sibling).revoked_at is None
    assert token_row(db, IntegrationAccessToken, refreshed).revoked_at is None
    assert db.get(IntegrationClient, configured["id"]).enabled


def test_user_logout_keeps_other_user_and_service_tokens_valid(client, administrator, db):
    _, credentials = create_service_client(client, administrator)
    service_headers = issue_service_token(client, credentials)
    login = client.post("/api/auth/login", json={"username": "workspace-admin",
                                              "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"]})
    assert login.status_code == 200
    sibling = {"Authorization": "Bearer " + login.json()["token"],
               "X-Workspace-Id": administrator["X-Workspace-Id"]}

    response = client.post("/api/auth/logout", headers=administrator)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me", headers=administrator).status_code == 401
    assert client.get("/api/auth/me", headers=sibling).status_code == 200
    assert client.get("/api/files", headers=service_headers).status_code == 200
    db.expire_all()
    assert token_row(db, SessionToken, administrator).revoked_at is not None
    assert token_row(db, SessionToken, sibling).revoked_at is None
    assert token_row(db, IntegrationAccessToken, service_headers).revoked_at is None


def test_service_logout_helper_is_idempotent_without_rotating_client(client, administrator, db):
    configured, credentials = create_service_client(client, administrator)
    headers = issue_service_token(client, credentials)
    raw = headers["Authorization"].removeprefix("Bearer ")
    before = db.get(IntegrationClient, configured["id"])
    original = (before.version, before.secret_digest, before.enabled)
    logout(db, raw)
    db.expire_all()
    revoked_at = token_row(db, IntegrationAccessToken, headers).revoked_at
    assert revoked_at is not None
    logout(db, raw)
    logout(db, "synthetic-unknown-token")
    db.expire_all()
    assert token_row(db, IntegrationAccessToken, headers).revoked_at == revoked_at
    unchanged = db.get(IntegrationClient, configured["id"])
    assert (unchanged.version, unchanged.secret_digest, unchanged.enabled) == original
