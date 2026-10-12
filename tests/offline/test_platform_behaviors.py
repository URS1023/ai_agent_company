"""Platform behavior checks with memory doubles only; use run_python_checks.py.

中文说明：平台行为的离线检查（时间/权限/状态分支），全部用内存替身。
运行方式：经 tests/offline/run_python_checks.py 执行（该入口会禁止数据库与网络访问）。
边界：不覆盖真实数据库与供应商行为，那部分属于 tests/backend 与其它 gate。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest


def test_invalid_cron_returns_editable_validation_errors():
    from app.errors import ValidationError
    from app.modules.system_tools.schedules import _field_values
    for expression in ("abc", "*/abc", "1/", "1-foo", "1-2-3", "60", "*//2"):
        with pytest.raises(ValidationError):
            _field_values(expression, 0, 59)
    assert _field_values("*/15", 0, 59) == {0, 15, 30, 45}


def test_expired_notice_creates_no_recipient_messages(monkeypatch):
    from app.errors import ConflictError
    from app.modules.system_tools import notifications
    row = SimpleNamespace(id="notice", status="draft", expires_at=datetime.utcnow() - timedelta(seconds=1))
    monkeypatch.setattr(notifications, "require_permission", lambda *args: None)
    monkeypatch.setattr(notifications, "scoped_record", lambda *args: row)
    monkeypatch.setattr(notifications, "check_version", lambda *args: None)
    recipients, writes = [], []
    monkeypatch.setattr(notifications, "_recipient_ids", lambda *args: recipients.append(True) or ["user"])
    ctx = SimpleNamespace(workspace_id="ws", db=SimpleNamespace(scalar=lambda _: 0, add=writes.append))
    with pytest.raises(ConflictError, match="过期"):
        notifications.publish_notice(ctx, "notice", 1)
    assert not recipients and not writes
    assert notifications._utc_naive(datetime(2026, 9, 16, 8, tzinfo=timezone(timedelta(hours=8)))) == datetime(2026, 9, 16)


def test_whole_database_backup_requires_all_organization_and_workspace_admin(monkeypatch):
    from app.errors import PermissionErrorEx
    from app.modules.system_tools import monitoring
    monkeypatch.setattr(monitoring, "require_permission", lambda *args: None)
    for missing in (("other-org", None), (None, "other-workspace")):
        results = iter(missing)
        ctx = SimpleNamespace(client_id=None, role="admin", user_id="user", db=SimpleNamespace(scalar=lambda _: next(results)))
        with pytest.raises(PermissionErrorEx, match="所有组织和工作区"):
            monitoring._require_platform_backup_access(ctx)
    ctx = SimpleNamespace(client_id="service-token", role="admin")
    with pytest.raises(PermissionErrorEx, match="交互会话"):
        monitoring._require_platform_backup_access(ctx)
    ctx = SimpleNamespace(client_id=None, role="admin", user_id="user", db=SimpleNamespace(scalar=lambda _: None))
    monitoring._require_platform_backup_access(ctx)


def test_generated_sdk_decodes_downloads_and_uploads_multipart(monkeypatch):
    from app.modules.system_tools.codegen import _python_client
    def operation(name, method="get", media=None):
        return {"path": "/" + name, "method": method, "operation": {
            "operationId": name, "requestBody": {"content": {media: {}}} if media else {},
        }}
    code = _python_client([operation("csv"), operation("binary"), operation("upload", "post", "multipart/form-data")], False)
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path == "/csv":
            return httpx.Response(200, text="name,count\n设备,0\n", headers={"Content-Type": "text/csv; charset=utf-8"})
        if request.url.path == "/binary":
            return httpx.Response(200, content=b"\x00\x01", headers={"Content-Type": "application/octet-stream"})
        return httpx.Response(200, json={"ok": True})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs))
    namespace = {}
    exec(compile(code, "generated_sdk", "exec"), namespace)
    client = namespace["CloudBrainClient"]("http://offline.invalid", "offline", "ws")
    try:
        assert client.csv() == "name,count\n设备,0\n"
        assert client.binary() == b"\x00\x01"
        assert client.upload(body={"name": "manual"}, files={"file": ("manual.txt", b"content")}) == {"ok": True}
        assert requests[-1].headers["content-type"].startswith("multipart/form-data; boundary=")
        assert b'filename="manual.txt"' in requests[-1].content
        assert len(requests) == 3
    finally:
        client.close()
