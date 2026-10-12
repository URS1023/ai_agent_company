"""Behavioral coverage for M23 operations and M24 developer integrations."""
from __future__ import annotations

from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.common.storage import LocalStorage
from app.modules.system_tools import migrations, monitoring


API = "/api/v1/system-tools"
SYSTEM = "/api/v1/system"


def _role(client, headers, permissions, code="system-tools-reader"):
    response = client.post(SYSTEM + "/roles", headers=headers, json={
        "code": code, "name": "系统工具查看者", "permissions": permissions, "data_scope": "workspace",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _user(client, headers, role_id, username="system-tools-reader"):
    response = client.post(SYSTEM + "/users", headers=headers, json={
        "username": username, "display_name": "系统工具查看者",
        "password": "Temporary12345", "role_ids": [role_id],
    })
    assert response.status_code == 201, response.text
    login = client.post("/api/auth/login", json={"username": username, "password": "Temporary12345"}).json()
    temporary = {"Authorization": "Bearer " + login["token"]}
    changed = client.post("/api/auth/password", headers=temporary, json={
        "current_password": "Temporary12345", "new_password": "ChangedPassword12345",
    })
    assert changed.status_code == 200, changed.text
    login = client.post("/api/auth/login", json={"username": username, "password": "ChangedPassword12345"}).json()
    return {"Authorization": "Bearer " + login["token"]}


def test_openapi_contract_is_complete_and_collection_has_real_examples(client, administrator):
    schema = client.get("/api/v1/platform/openapi.json", headers=administrator).json()
    quality = schema["x-contract-quality"]
    assert quality["describedOperations"] == quality["operations"]
    assert quality["requestBodiesWithExamples"] == quality["requestBodies"]
    assert quality["uniqueOperationIds"] is True
    operation = schema["paths"][API + "/jobs"]["post"]
    assert operation["description"] and operation["requestBody"]["content"]["application/json"]["example"]
    collection = client.get("/api/v1/platform/api-collection", headers=administrator).json()
    requests = [request for group in collection["item"] for request in group["item"]]
    job = next(item for item in requests if item["request"]["url"] == "{{base_url}}" + API + "/jobs")
    assert '"taskType"' in job["request"]["body"]["raw"]
    assert job["request"]["body"]["raw"] != "{}"


def test_notice_delivery_is_snapshotted_and_recipient_scoped(client, administrator):
    created = client.post(API + "/notices", headers=administrator, json={
        "title": "设备平台维护", "body": "今晚 22:00 进行例行维护。",
        "noticeType": "maintenance", "severity": "warning", "audienceType": "all",
    })
    assert created.status_code == 201, created.text
    notice = created.json()
    published = client.post(API + f"/notices/{notice['id']}/publish", headers=administrator,
                            json={"version": notice["version"]})
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published" and published.json()["deliveredCount"] >= 1
    inbox = client.get(API + "/inbox?status=unread", headers=administrator).json()
    message = next(item for item in inbox["items"] if item["noticeId"] == notice["id"])
    assert message["subject"] == notice["title"] and inbox["unread"] >= 1
    read = client.post(API + f"/inbox/{message['id']}/read", headers=administrator,
                       json={"version": message["version"]})
    assert read.status_code == 200 and read.json()["status"] == "read"
    assert client.patch(API + f"/notices/{notice['id']}", headers=administrator, json={
        "version": published.json()["version"], "title": "非法覆盖", "body": "已发布后不能改",
        "audienceType": "all",
    }).status_code == 409


def test_schedule_manual_run_is_idempotent_and_validates_cron(client, administrator):
    invalid = client.post(API + "/jobs", headers=administrator, json={
        "code": "health.invalid", "name": "非法调度", "taskType": "health",
        "scheduleKind": "cron", "scheduleExpression": "90 * * * *", "timezone": "Asia/Shanghai",
    })
    assert invalid.status_code == 422
    created = client.post(API + "/jobs", headers=administrator, json={
        "code": "health.hourly", "name": "服务健康巡检", "taskType": "health",
        "scheduleKind": "cron", "scheduleExpression": "0 * * * 1-7", "timezone": "Asia/Shanghai",
        "status": "paused",
    })
    assert created.status_code == 201, created.text
    job = created.json()
    body = {"version": job["version"], "idempotencyKey": "manual-health-20260901-01"}
    first = client.post(API + f"/jobs/{job['id']}/run", headers=administrator, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    duplicate = client.post(API + f"/jobs/{job['id']}/run", headers=administrator,
                            json={**body, "version": job["version"] + 1})
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]
    runs = client.get(API + f"/jobs/{job['id']}/runs", headers=administrator).json()["items"]
    assert [item["id"] for item in runs].count(first.json()["id"]) == 1


def test_verified_sqlite_backup_and_offline_restore_plan(client, administrator, monkeypatch, tmp_path):
    storage = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    response = client.post(API + "/backups", headers=administrator, json={
        "name": "专项测试备份", "confirmation": "CREATE_BACKUP",
    })
    assert response.status_code == 201, response.text
    backup = response.json()
    assert backup["status"] == "verified" and backup["sha256"] and backup["size"] > 0
    downloaded = client.get(API + f"/backups/{backup['id']}/download", headers=administrator)
    assert downloaded.status_code == 200 and len(downloaded.content) == backup["size"]
    plan = client.get(API + f"/backups/{backup['id']}/restore-plan", headers=administrator).json()
    assert plan["automaticApply"] is False and plan["status"] == "requires_offline_restart"


def test_client_secret_is_shown_once_and_generated_code_contains_no_credentials(client, administrator):
    response = client.post(API + "/integration-clients", headers=administrator, json={
        "name": "设备门户", "redirectUris": ["https://portal.example.invalid/callback"],
        "scopes": ["alerts.view", "quality.view"],
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["clientSecret"].startswith("t5c_") and created["secretShownOnce"] is True
    listed = client.get(API + "/integration-clients", headers=administrator).json()["items"][0]
    assert "clientSecret" not in listed and listed["secretPrefix"] == created["secretPrefix"]
    directory = client.get(API + "/api-directory?keyword=查询调度任务", headers=administrator).json()
    assert directory["total"] >= 1
    operation_id = directory["items"][0]["operationId"]
    code = client.post(API + "/client-code", headers=administrator, json={
        "operationIds": [operation_id], "language": "python", "includeExamples": True,
    })
    assert code.status_code == 200 and "class CloudBrainClient" in code.text
    assert created["clientSecret"] not in code.text and administrator["Authorization"] not in code.text


def test_client_token_is_workspace_and_scope_bounded_then_revoked_on_rotation(client, administrator):
    created = client.post(API + "/integration-clients", headers=administrator, json={
        "name": "只读运维服务", "redirectUris": [], "scopes": ["system.operations.view"],
    }).json()
    exchange = client.post("/api/auth/client-token", json={
        "clientId": created["clientId"], "clientSecret": created["clientSecret"],
        "workspaceId": administrator["X-Workspace-Id"],
    })
    assert exchange.status_code == 200, exchange.text
    token = exchange.json()["accessToken"]
    service = {"Authorization": "Bearer " + token, "X-Workspace-Id": administrator["X-Workspace-Id"]}
    assert client.get(API + "/health", headers=service).status_code == 200
    assert client.post(API + "/notices", headers=service, json={
        "title": "越权", "body": "服务令牌不能创建", "audienceType": "all",
    }).status_code == 403
    navigation = client.get("/api/v1/platform/navigation", headers=service)
    assert navigation.status_code == 200
    assert {item["id"] for item in navigation.json()["items"]} == {"system-operations"}
    rotated = client.post(API + f"/integration-clients/{created['id']}/rotate", headers=administrator,
                          json={"version": created["version"]})
    assert rotated.status_code == 200, rotated.text
    assert client.get(API + "/health", headers=service).status_code == 401
    assert client.post("/api/auth/client-token", json={
        "clientId": created["clientId"], "clientSecret": created["clientSecret"],
        "workspaceId": administrator["X-Workspace-Id"],
    }).status_code == 401


def _public_certificate() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Cloud Brain local integration")])
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(minutes=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_certificate_registry_accepts_public_cert_and_rejects_private_key(client, administrator):
    pem = _public_certificate()
    response = client.post(API + "/certificates", headers=administrator, json={
        "name": "本机集成证书", "kind": "client_signing", "pem": pem,
    })
    assert response.status_code == 201, response.text
    assert len(response.json()["fingerprintSha256"]) == 64
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    rejected = client.post(API + "/certificates", headers=administrator, json={
        "name": "禁止私钥", "kind": "client_signing", "pem": private,
    })
    assert rejected.status_code == 422 and "PRIVATE KEY" not in rejected.text


def test_source_scan_is_read_only_and_redacts_secrets(client, administrator, monkeypatch):
    source = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with source.begin() as connection:
        connection.execute(text("CREATE TABLE sample_users (id INTEGER PRIMARY KEY, username TEXT, password TEXT, api_token TEXT)"))
        connection.execute(text("INSERT INTO sample_users VALUES (1, 'operator', 'plain-password', 'plain-token')"))
    monkeypatch.setattr(migrations, "_source_engine", lambda schema: source)
    response = client.post(API + "/migration/batches", headers=administrator, json={
        "sourceSchema": "qknow_dev", "sourceName": "隔离测试来源",
        "includeTables": ["sample_users"], "archiveRows": True,
    })
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["status"] == "completed" and batch["rowCount"] == 1
    records = client.get(API + f"/migration/batches/{batch['id']}/records", headers=administrator).json()
    payload = records["items"][0]["payload"]
    assert payload["username"] == "operator"
    assert payload["password"]["redacted"] is True and payload["api_token"]["redacted"] is True
    assert "plain-password" not in str(records) and "plain-token" not in str(records)


def test_system_tool_permissions_separate_view_manage_and_development(client, administrator):
    role = _role(client, administrator, ["home.view", "system.operations.view"])
    headers = _user(client, administrator, role["id"])
    assert client.get(API + "/notices", headers=headers).status_code == 200
    assert client.post(API + "/notices", headers=headers, json={
        "title": "越权", "body": "不能创建", "audienceType": "all",
    }).status_code == 403
    assert client.get(API + "/api-directory", headers=headers).status_code == 403
    navigation = client.get("/api/v1/platform/navigation", headers=headers).json()["items"]
    assert {item["id"] for item in navigation} == {"home", "system-operations"}
