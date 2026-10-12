"""Real local HTTP requests, extraction, permissions and auditable partial failure.

中文说明：HTTP 数据采集走真实本机请求：分页保持筛选与历史、JSON 模式拒绝 HTML、
批量部分失败不能伪装成空成功、空/非法/重定向可区分、私网地址需已保存授权、
元数据与特殊地址（含混合 DNS、伪 IP）始终拒绝、慢响应受总时限约束、请求头拒绝不安全值、
记录路径保留空标量与小数、缺失/多匹配不静默强转、密钥轮换与错误不回显凭据。
隔离方式：client 夹具（内存 SQLite）+ 本地 HTTP 桩服务，不连生产库与外部网络。
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.models import DataSource, Run, Secret
from app.modules.analytics.models import QueryHistory
from app.modules.collection import transport
from app.modules.collection.extraction import compile_path, extract_records
from app.modules.collection.schemas import ExtractField
from test_system_foundation import activate_user, create_role, create_user

API = "/api/v1/collection"


@pytest.fixture
def upstream():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
            requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
            if self.path == "/slow-headers":
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    for _ in range(30):
                        self.wfile.write(b"X-Progress: pending\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/records")
                self.end_headers()
                return
            if body and body.get("fail"):
                self.send_response(503)
                self.end_headers()
                return
            if self.path.startswith("/html"):
                raw = ("<!doctype html><h1>电影采集验收</h1><p>" + self.path + "</p>" + self.headers.get("Authorization", "")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            raw = b'{"items":[{"device":"A","amount":123.450,"enabled":false,"echo":"TOKEN"}]}'
            raw = raw.replace(b"TOKEN", self.headers.get("Authorization", "public").encode())
            if self.path == "/empty":
                raw = b'{"items":[]}'
            if self.path == "/invalid":
                raw = b"not JSON"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def source_payload(upstream, **kwargs):
    return {"name": "设备采集", "url": upstream[0] + "/records", "allowPrivate": True,
            "recordPath": "$.items[*]", **kwargs}


def create_source(client, administrator, upstream, **kwargs):
    payload = source_payload(upstream, **kwargs)
    response = client.post(API + "/sources", headers=administrator, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"], payload


def collect(client, headers, source, **kwargs):
    return client.post(API + "/sources/" + source["id"] + "/collect", headers=headers,
                       json={"version": source["version"], **kwargs})


def test_text_pagination_preserves_filters_and_history(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, url=upstream[0] + "/html?filter=movie&start=999",
                             recordPath="$", responseFormat="text",
                             pagination={"parameter": "start", "start": 0, "step": 25, "pages": 4},
                             headers={"Authorization": "Bearer ${secret.access}"}, credentials={"access": "secret-html-123"})
    response = collect(client, administrator, source)
    assert response.status_code == 200, response.text
    value = response.json()["data"]
    assert len(value["data"]) == 4 and value["rowOrigins"] == [0, 1, 2, 3]
    assert [item["path"] for item in upstream[1]] == [f"/html?filter=movie&start={offset}" for offset in [0, 25, 50, 75]]
    assert "电影采集验收" in value["data"][0]["body"] and "[REDACTED]" in value["data"][0]["body"]
    assert "secret-html-123" not in response.text
    assert client.get(API + "/history/" + value["queryId"], headers=administrator).json()["data"] == value


def test_json_mode_rejects_html_with_actionable_error(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, url=upstream[0] + "/html", recordPath="$")
    response = collect(client, administrator, source)
    assert response.status_code == 502
    assert "响应格式" in response.json()["data"]["outcomes"][0]["message"]


def test_pagination_limit_stops_remaining_requests(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, url=upstream[0] + "/html", recordPath="$",
                             responseFormat="text", maxRows=2, pagination={"pages": 4})
    value = collect(client, administrator, source).json()["data"]
    assert len(upstream[1]) == 2 and value["status"] == "partial"
    assert [item["status"] for item in value["outcomes"]] == ["success", "success", "skipped", "skipped"]
    invalid = client.post(API + "/sources", headers=administrator,
                          json=source_payload(upstream, method="POST", pagination={"pages": 4}))
    assert invalid.status_code == 422


def test_real_request_credentials_decimal_false_and_persisted_history(client, administrator, upstream, db):
    secret = "test-secret-901#"
    source, payload = create_source(client, administrator, upstream,
        headers={"Authorization": "Bearer ${secret.access}"}, credentials={"access": secret}, fields=[
            {"name": "设备", "path": "$.device", "required": True},
            {"name": "金额", "path": "$.amount", "type": "decimal", "comment": "含税金额"},
            {"name": "启用", "path": "$.enabled", "required": True, "type": "boolean"},
            {"name": "回显", "path": "$.echo"},
        ])
    assert "credentials" not in source and source["credentialNames"] == ["access"]
    row = db.get(DataSource, source["id"])
    assert secret not in json.dumps(row.connection_config)
    assert secret not in db.get(Secret, row.secret_id).ciphertext
    response = collect(client, administrator, source)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["data"] == [{"设备": "A", "金额": "123.450", "启用": False, "回显": "Bearer [REDACTED]"}]
    assert result["statistics"]["columns"]["金额"]["sum"] == "123.450"
    assert upstream[1][0]["headers"]["Authorization"] == "Bearer " + secret
    assert result["rowOrigins"] == [0] and result["status"] == "success"
    assert secret not in response.text
    assert db.get(Run, result["runId"]).status == "completed"
    assert client.get(API + "/history/" + result["queryId"], headers=administrator).json()["data"] == result
    assert len(client.get(API + "/sources/" + source["id"] + "/history", headers=administrator).json()["data"]) == 1
    assert client.get("/api/v1/analytics/history/recent", headers=administrator).json()["data"] == []
    assert client.get("/api/v1/analytics/datasources", headers=administrator).json()["data"] == []


@pytest.mark.parametrize("continue_on_error,expected", [(False, ["success", "failed", "skipped"]), (True, ["success", "failed", "success"])])
def test_batch_partial_failure_is_not_empty_success(client, administrator, upstream, db, continue_on_error, expected):
    source, _ = create_source(client, administrator, upstream, method="POST", bodies=[{}, {"fail": True}, {}],
                              continueOnError=continue_on_error)
    assert collect(client, administrator, source).status_code == 422
    assert len(upstream[1]) == 0
    response = collect(client, administrator, source, confirmed=True)
    assert response.status_code == 207, response.text
    result = response.json()["data"]
    assert not response.json()["success"] and result["status"] == "partial"
    assert [item["status"] for item in result["outcomes"]] == expected
    assert len(upstream[1]) == (3 if continue_on_error else 2)
    assert result["outcomes"][1]["httpStatus"] == 503
    assert result["rowOrigins"] == ([0, 2] if continue_on_error else [0])
    assert result["statistics"]["partial"]
    assert db.get(QueryHistory, result["queryId"]).status == "partial"


@pytest.mark.parametrize("path,status", [("empty", 200), ("invalid", 502), ("redirect", 502)])
def test_empty_invalid_and_redirect_are_distinct(client, administrator, upstream, path, status):
    source, _ = create_source(client, administrator, upstream, url=upstream[0] + "/" + path)
    response = collect(client, administrator, source)
    assert response.status_code == status, response.text
    result = response.json()["data"]
    assert result["totalCount"] == 0
    assert result["status"] == ("success" if path == "empty" else "failed")
    assert len(upstream[1]) == 1


def test_batch_row_limit_does_not_send_remaining_bodies(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, method="POST", bodies=[{}, {}, {}], maxRows=1)
    response = collect(client, administrator, source, confirmed=True)
    assert response.status_code == 207, response.text
    result = response.json()["data"]
    assert len(upstream[1]) == 1 and result["status"] == "partial"
    assert [row["status"] for row in result["outcomes"]] == ["success", "skipped", "skipped"]


def test_versions_provider_boundaries_and_soft_removal(client, administrator, upstream):
    source, payload = create_source(client, administrator, upstream)
    update = {**payload, "name": "新版", "version": 1}
    response = client.put(API + "/sources/" + source["id"], headers=administrator, json=update)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["version"] == 2
    assert client.put(API + "/sources/" + source["id"], headers=administrator, json=update).status_code == 409
    assert collect(client, administrator, source).status_code == 409 and not upstream[1]
    assert client.put("/api/v1/analytics/datasources/" + source["id"], headers=administrator,
                      json={"name": "绕过", "type": "sqlite", "databaseName": "x.sqlite", "version": 2}).status_code == 422
    source["version"] = 2
    result = collect(client, administrator, source).json()["data"]
    assert client.delete(API + "/sources/" + source["id"] + "?version=1", headers=administrator).status_code == 409
    assert client.delete(API + "/sources/" + source["id"] + "?version=2", headers=administrator).status_code == 200
    assert collect(client, administrator, source).status_code == 404
    assert client.get(API + "/history/" + result["queryId"], headers=administrator).status_code == 200


def test_permissions_cannot_approve_private_target_or_post_automation(client, administrator, upstream):
    role = create_role(client, administrator, "collector", ["home.view", "datasources.view", "datasources.create", "datasources.edit", "datasources.execute"], "workspace")
    create_user(client, administrator, [role["id"]])
    user = activate_user(client)
    assert client.post(API + "/sources", headers=user, json=source_payload(upstream)).status_code == 403
    public = source_payload(upstream, url="https://example.com/records", allowPrivate=False, method="POST", allowAutomatic=True)
    assert client.post(API + "/sources", headers=user, json=public).status_code == 403
    source, payload = create_source(client, administrator, upstream, method="POST", allowAutomatic=True)
    assert client.put(API + "/sources/" + source["id"], headers=user,
                      json={**payload, "version": 1, "bodies": [{}]}).status_code == 403
    assert client.put(API + "/sources/" + source["id"], headers=user,
                      json={**payload, "version": 1, "url": upstream[0] + "/empty"}).status_code == 403
    assert not upstream[1]


def test_private_address_requires_saved_approval_before_network(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, allowPrivate=False)
    response = collect(client, administrator, source)
    assert response.status_code == 502 and not upstream[1]


@pytest.mark.parametrize("address", ["169.254.169.254", "100.100.100.200", "168.63.129.16", "0.0.0.0", "::", "ff02::1", "::ffff:169.254.169.254"])
def test_metadata_and_special_addresses_always_rejected(address):
    with pytest.raises(ValidationError):
        transport.validate_address(address, True)


def test_mixed_dns_records_are_rejected(monkeypatch):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: [(2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ValidationError):
        transport.resolve_destination("http://example.com/data", allow_private=False, deadline=time.monotonic() + 3)


@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize("address", ["198.18.0.227", "198.19.255.254", "::ffff:198.18.0.1"])
def test_fake_ip_dns_has_actionable_error_without_private_bypass(address, allow_private):
    with pytest.raises(ValidationError, match="Fake-IP DNS"):
        transport.validate_address(address, allow_private)


def test_slow_headers_respect_total_deadline(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream, url=upstream[0] + "/slow-headers", timeoutSeconds=1)
    start = time.monotonic()
    response = collect(client, administrator, source)
    assert response.status_code == 502, response.text
    assert time.monotonic() - start < 2.5


@pytest.mark.parametrize("headers", [{"Authorization": "plain-secret"}, {"Authorization": "plain ${secret.a}"}, {"Host": "internal"}, {"X-Key": "x\r\nHost:y"}])
def test_headers_reject_unsafe_values(headers):
    with pytest.raises(ValidationError):
        transport.validate_headers(headers)


def extract(payload, path="$", fields=None, limit=20):
    return extract_records(payload, SimpleNamespace(record_path=path, fields=[ExtractField(**field) for field in fields or []]),
                           limit=limit, deadline=time.monotonic() + 5)


@pytest.mark.parametrize("payload,path,expected", [({"items": []}, "$.items[*]", []), ({"items": [{"v": 0}]}, "$.items[?@.v > 1]", []),
    ({"items": [[], [], [], [{"v": 1}]]}, "$.items[*]", [{"v": 1}]), ("文字", "$", [{"value": "文字"}]),
    (False, "$", [{"value": False}]), ({"v": Decimal("1.230")}, "$", [{"v": "1.230"}])])
def test_record_paths_preserve_empty_scalar_and_decimal(payload, path, expected):
    assert extract(payload, path, limit=1)[0] == expected


def test_missing_required_and_multi_match_do_not_silently_coerce():
    with pytest.raises(ValidationError):
        extract({}, "$.missing[*]")
    with pytest.raises(ValidationError):
        extract({"values": [1, 2]}, fields=[{"name": "v", "path": "$.values[*]"}])
    assert extract({"values": [1, 2]}, fields=[{"name": "v", "path": "$.values[*]", "multiple": True}])[0] == [{"v": [1, 2]}]
    with pytest.raises(ValidationError):
        extract({"v": "1.2"}, fields=[{"name": "v", "path": "$.v", "type": "integer"}])
    with pytest.raises(ValueError):
        compile_path("$.items[?match(@.v, '(a+)+$')]")


def test_offline_preview_and_openapi_contract(client, administrator):
    response = client.post(API + "/extract-preview", headers=administrator, json={
        "sample": {"items": [{"v": 0}, {"v": 2}]}, "recordPath": "$.items[?@.v >= 1]", "fields": [{"name": "数量", "path": "$.v", "type": "integer"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["data"] == [{"数量": 2}]
    schema = client.get("/api/v1/platform/openapi.json", headers=administrator).json()
    assert schema["components"]["schemas"]["HttpSourceInput"]["properties"]["credentials"]["writeOnly"]
    assert len([path for path in schema["paths"] if path.startswith(API)]) == 6


def test_saved_http_widget_for_viewer_and_unapproved_source(client, administrator, upstream):
    source, payload = create_source(client, administrator, upstream, allowAutomatic=True)
    response = client.post('/api/v1/reports', headers=administrator, json={
        'reportName': 'HTTP 设备看板', 'templateMode': 'none', 'content': {'widgets': [
            {'id': 'equipment', 'component': 'JBar', 'x': 0, 'y': 0, 'w': 500, 'h': 300,
             'config': {'dataType': 2, 'datasourceId': source['id']}},
        ]},
    })
    assert response.status_code == 201, response.text
    report = response.json()['data']
    role = create_role(client, administrator, 'http_reader', ['home.view', 'dashboards.view', 'datasources.view'], 'workspace')
    create_user(client, administrator, [role['id']])
    viewer = activate_user(client)
    path = '/api/v1/reports/' + report['id'] + '/widgets/equipment/data'
    response = client.get(path, headers=viewer)
    assert response.status_code == 200, response.text
    assert response.json()['data']['queryType'] == 'http'
    assert response.json()['data']['data'][0]['device'] == 'A'
    assert collect(client, viewer, source).status_code == 403
    assert client.post('/api/v1/reports/http-query', headers=viewer, json={'datasourceId': source['id']}).status_code == 403
    assert client.put(API + '/sources/' + source['id'], headers=administrator,
                      json={**payload, 'version': 1, 'allowAutomatic': False}).status_code == 200
    assert client.get(path, headers=viewer).status_code == 422
    assert len(upstream[1]) == 1


def test_disabled_source_secret_rotation_and_errors_never_echo_credentials(client, administrator, upstream, db):
    secret = 'first-secret-901#'
    source, payload = create_source(client, administrator, upstream, headers={'Authorization': '${secret.access}'}, credentials={'access': secret})
    source_id = source['id']
    old_secret = db.get(DataSource, source_id).secret_id
    changed = {**payload, 'version': 1, 'credentials': {'access': 'second-secret-902#'}, 'enabled': False}
    response = client.put(API + '/sources/' + source_id, headers=administrator, json=changed)
    assert response.status_code == 200, response.text
    assert 'second-secret-902#' not in response.text
    db.expire_all()
    assert db.get(DataSource, source_id).secret_id != old_secret
    assert db.get(Secret, old_secret) is not None
    assert collect(client, administrator, response.json()['data']).status_code == 422
    invalid = client.post(API + '/sources', headers=administrator, json={**payload, 'credentials': {'invalid name': secret}})
    assert invalid.status_code == 422 and secret not in invalid.text
    assert not upstream[1]


def test_jsonpath_computation_and_huge_integer_are_bounded(monkeypatch):
    from app.modules.collection import extraction
    monkeypatch.setattr(extraction, 'MAX_EVALUATION_SECONDS', -1)
    with pytest.raises(ValidationError):
        extract({'a': [1, 2]}, '$..*')
    monkeypatch.setattr(extraction, 'MAX_EVALUATION_SECONDS', 5)
    with pytest.raises(ValidationError):
        extract({'n': '1e999999999'}, fields=[{'name': 'n', 'path': '$.n', 'type': 'integer'}])


def test_owner_scope_blocks_foreign_sources_and_history(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream)
    result = collect(client, administrator, source).json()['data']
    role = create_role(client, administrator, 'own_collector', ['home.view', 'datasources.view', 'datasources.execute'], 'self')
    create_user(client, administrator, [role['id']])
    viewer = activate_user(client)
    assert client.get(API + '/sources', headers=viewer).json()['data'] == []
    assert collect(client, viewer, source).status_code == 404
    assert client.get(API + '/history/' + result['queryId'], headers=viewer).status_code == 404
    assert len(upstream[1]) == 1


def test_secret_expansion_and_redaction_cannot_silently_change_columns():
    assert transport.resolve_references({'a': '${secret.x}'}, {'x': 'safe'}) == {'a': 'safe'}
    with pytest.raises(ValidationError):
        transport.resolve_references('${secret.x}' * 1000, {'x': 'a' * 4096}, max_bytes=16000)
    with pytest.raises(ValidationError):
        transport.redact_values({'secret-one': 1, 'secret-two': 2}, {'a': 'secret-one', 'b': 'secret-two'})
