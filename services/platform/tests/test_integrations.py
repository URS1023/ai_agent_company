"""原生协议与运行可靠性回归；只使用MockTransport和本轮新建的本地SQLite。"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.adapters.native import DifyAdapter, NativeConnection, OpenWebUIAdapter, RetrievalInput, WorkflowInput, native_link, segment, validate_base_url
from agent_platform.core.context import Actor
from agent_platform.core.database import Database
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Organization, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.integrations.models import CallbackReceipt, NativeRun
from agent_platform.modules.integrations.schemas import InvocationInput, SearchInput
from agent_platform.modules.integrations.service import AUTOMATION, BINDING, INTEGRATION, PROFILE, SPACE, IntegrationService


def test_dify_published_version_and_user_are_preserved():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"workflow_run_id": "run-1", "task_id": "task-1", "data": {"status": "succeeded", "outputs": {"ok": True}}})
    adapter = DifyAdapter(NativeConnection("http://127.0.0.1:12080/v1", "app-secret"), transport=httpx.MockTransport(transport))
    result = adapter.run_workflow(WorkflowInput(inputs={"device": "设备一"}), "mapped-user", release_id="fixed-release")
    assert calls[0].url.path == "/v1/workflows/fixed-release/run"
    assert json.loads(calls[0].content) == {"inputs": {"device": "设备一"}, "response_mode": "blocking", "user": "mapped-user"}
    assert calls[0].headers["authorization"] == "Bearer app-secret"
    assert result["workflow_run_id"] == "run-1"


def test_dify_knowledge_paths_do_not_use_console_or_app_api():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"records": [], "document": {"id": "document-1"}})
    adapter = DifyAdapter(NativeConnection("http://127.0.0.1:12080/v1", "dataset-secret"), transport=httpx.MockTransport(transport))
    adapter.import_text("dataset-1", "作业规程", "正文")
    adapter.retrieve("dataset-1", RetrievalInput(query="检查什么"))
    adapter.reindex("dataset-1", "document-1", {"mode": "automatic"})
    assert [x.url.path for x in calls] == ["/v1/datasets/dataset-1/document/create-by-text", "/v1/datasets/dataset-1/retrieve", "/v1/datasets/dataset-1/documents/document-1/update-by-text"]
    assert all(x.headers["authorization"] == "Bearer dataset-secret" for x in calls)


@pytest.mark.parametrize("url", ["http://192.168.1.9:5432", "http://localhost:12080", "http://127.0.0.1.evil.invalid", "http://user:pass@127.0.0.1", "file:///tmp/a", "http://127.0.0.1/?token=x"])
def test_unapproved_network_or_embedded_credentials_rejected(url):
    with pytest.raises(DomainError):
        validate_base_url(url)


@pytest.mark.parametrize("id", ["..", "a/b", "a?b", "a\\b", "x\r\nHost:bad"])
def test_native_id_cannot_escape_path(id):
    with pytest.raises(DomainError):
        segment(id)


def test_native_errors_never_forward_private_response_or_retry():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(401, json={"message": "private-provider-key-should-not-leak"})
    adapter = DifyAdapter(NativeConnection("http://127.0.0.1/v1", "private-token"), transport=httpx.MockTransport(transport))
    with pytest.raises(DomainError) as error:
        adapter.parameters()
    assert error.value.code == "native_permission_denied"
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_redirect_is_not_followed_and_invalid_response_is_rejected():
    for response, code in [(httpx.Response(302, headers={"location": "http://192.168.1.2"}), "native_request_failed"), (httpx.Response(200, text="<html>login</html>"), "native_invalid_response")]:
        calls = []
        def transport(request):
            calls.append(request)
            return response
        adapter = DifyAdapter(NativeConnection("http://127.0.0.1/v1", "token"), transport=httpx.MockTransport(transport))
        with pytest.raises(DomainError) as error:
            adapter.parameters()
        assert error.value.code == code
        assert len(calls) == 1


def test_owui_api_and_console_link_are_separate():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=[])
    adapter = OpenWebUIAdapter(NativeConnection("http://127.0.0.1:13000", "personal-token"), transport=httpx.MockTransport(transport))
    adapter.models()
    adapter.chats(page=2)
    assert [x.url.path for x in calls] == ["/api/models", "/api/v1/chats/"]
    link = native_link("openwebui", "http://127.0.0.1:13000", "chat", "chat-1")
    assert link == "http://127.0.0.1:13000/c/chat-1"
    assert "token" not in link


class FakeVault:
    def get(self, ref, workspace_id, session=None):
        if workspace_id != "ws-one" or ref not in {"app-ref", "dataset-ref", "callback-ref"}:
            raise DomainError(404, "secret_not_found", "凭据不存在")
        return {"app-ref": "app-value", "dataset-ref": "dataset-value", "callback-ref": "callback-value"}[ref]


@pytest.fixture
def environment(tmp_path):
    # 只创建本测试目录中的SQLite；文件归当前测试Python进程，不读取任何原项目连接。
    settings = Settings(data_dir=tmp_path, database_url="sqlite:///" + (tmp_path / "native-test.sqlite3").as_posix())
    database = Database(settings)
    database.initialize()
    session = database.sessions()
    session.add(Organization(id="org-one", name="测试企业"))
    session.flush()
    session.add(Workspace(id="ws-one", organization_id="org-one", name="测试工作区"))
    session.add(User(id="user-one", organization_id="org-one", username="tester", display_name="测试用户", password_hash="unused"))
    session.commit()
    actor = Actor("user-one", "ws-one", frozenset({"*"}), is_admin=True)
    state = SimpleNamespace(settings=settings, secret_vault=FakeVault(), native_http_transport=None)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": SimpleNamespace(state=state)})
    service = IntegrationService(session, actor, request)
    yield service, state
    session.close()
    database.dispose()


def connection_and_binding(service, kind=AUTOMATION):
    integration = service.create(INTEGRATION, {"name": "Dify本机", "engine": "dify", "base_url": "http://127.0.0.1:12080/v1", "console_url": "http://127.0.0.1:12080", "credential_ref": "app-ref", "dataset_credential_ref": "dataset-ref", "callback_secret_ref": "callback-ref", "enabled": True})
    binding = service.create(kind, {"name": "设备质检流程", "integration_id": integration["id"], "native_id": "app-one", "app_mode": "workflow", "release_id": "release-one", "enabled": True})
    service.session.commit()
    return integration, binding


def test_real_run_projection_idempotency_and_revision_snapshot(environment):
    service, state = environment
    integration, binding = connection_and_binding(service)
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"task_id": "native-task", "workflow_run_id": "native-run", "data": {"status": "succeeded", "outputs": {"inspection": "pass"}, "total_tokens": 7}})
    state.native_http_transport = httpx.MockTransport(transport)
    data = InvocationInput(inputs={"device_id": "device-one"}, idempotency_key="request-one")
    run = service.invoke(AUTOMATION, binding["id"], data)
    assert run["status"] == "succeeded"
    assert TaskService(service.session, service.actor).get(run["task_id"])["outputs"] == {"inspection": "pass"}
    service.update(AUTOMATION, binding["id"], {"release_id": "release-two"}, binding["revision"])
    repeated = service.invoke(AUTOMATION, binding["id"], data)
    assert repeated["id"] == run["id"] and len(calls) == 1
    assert run["release_id"] == "release-one"
    with pytest.raises(DomainError) as error:
        service.invoke(AUTOMATION, binding["id"], InvocationInput(inputs={"device_id": "other"}, idempotency_key="request-one"))
    assert error.value.code == "idempotency_conflict"


def test_timeout_keeps_unknown_reservation_and_never_resends(environment):
    service, state = environment
    _, binding = connection_and_binding(service)
    calls = []
    def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout("sample timeout", request=request)
    state.native_http_transport = httpx.MockTransport(transport)
    data = InvocationInput(inputs={}, idempotency_key="timeout-one")
    with pytest.raises(DomainError) as error:
        service.invoke(AUTOMATION, binding["id"], data)
    assert error.value.status == 504
    service.session.rollback()
    run = service.invoke(AUTOMATION, binding["id"], data)
    assert run["status"] == "unknown" and len(calls) == 1
    assert TaskService(service.session, service.actor).get(run["task_id"])["status"] == "unknown"


def signed_event(service, integration, run, *, sequence, status, event_id):
    raw = json.dumps({"run_id": run["id"], "native_run_id": "native-run", "event_id": event_id, "sequence": sequence, "status": status}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"callback-value", timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return service.callback(integration["id"], raw, timestamp, signature)


def test_signed_callbacks_deduplicate_ignore_old_and_preserve_terminal(environment):
    service, state = environment
    integration, binding = connection_and_binding(service)
    state.native_http_transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"workflow_run_id": "native-run", "task_id": "native-task", "data": {"status": "running"}}))
    run = service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="callback-one"))
    assert signed_event(service, integration, run, sequence=4, status="succeeded", event_id="event-4")["disposition"] == "applied"
    assert signed_event(service, integration, run, sequence=4, status="succeeded", event_id="event-4")["duplicate"]
    assert signed_event(service, integration, run, sequence=2, status="running", event_id="event-2")["disposition"] == "stale"
    assert signed_event(service, integration, run, sequence=6, status="failed", event_id="event-6")["disposition"] == "terminal_ignored"
    assert signed_event(service, integration, run, sequence=7, status="succeeded", event_id="event-7")["disposition"] == "terminal_ignored"
    assert service.get_run(run["id"]).status == "succeeded"
    assert service.get_run(run["id"]).sequence == 4
    with pytest.raises(DomainError) as error:
        service.callback(integration["id"], b"{}", str(int(time.time())), "bad")
    assert error.value.code == "callback_signature_invalid"


def test_cancel_requires_native_task_and_only_marks_request(environment):
    service, state = environment
    _, binding = connection_and_binding(service)
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"result": "success"} if request.url.path.endswith("/stop") else {"workflow_run_id": "native-run", "task_id": "native-task", "data": {"status": "running"}})
    state.native_http_transport = httpx.MockTransport(transport)
    run = service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="cancel-one"))
    cancelled = service.cancel_task(run["task_id"])
    assert cancelled["status"] == "cancel_requested"
    assert calls[-1].url.path == "/v1/workflows/tasks/native-task/stop"
    assert json.loads(calls[-1].content)["user"].startswith("platform-")


def test_permissions_workspace_isolation_and_configuration_revision(environment):
    service, state = environment
    integration, binding = connection_and_binding(service)
    with pytest.raises(DomainError) as error:
        service.update(INTEGRATION, integration["id"], {"name": "changed"}, 999)
    assert error.value.status == 409
    other = IntegrationService(service.session, Actor("user-one", "other-workspace", frozenset({"*"}), is_admin=True), service.request)
    with pytest.raises(DomainError) as error:
        other.integration(integration["id"])
    assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        service.delete(INTEGRATION, integration["id"], integration["revision"])
    assert error.value.code == "integration_in_use"


def test_retrieval_uses_dataset_key_and_persists_real_citations(environment):
    service, state = environment
    integration, _ = connection_and_binding(service)
    space = service.create(SPACE, {"name": "设备手册", "integration_id": integration["id"], "native_id": "dataset-one", "enabled": True})
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"records": [{"score": 0.91, "segment": {"id": "segment-one", "document_id": "doc-one", "position": 3, "content": "先停机检查", "document": {"id": "doc-one", "name": "操作手册"}}}]})
    state.native_http_transport = httpx.MockTransport(transport)
    result = service.search(SearchInput(space_ids=[space["id"]], query="如何检查"))
    assert calls[0].headers["authorization"] == "Bearer dataset-value"
    citation = service.store.get("knowledge_citation", result["records"][0]["citation_id"])
    assert citation["segment_id"] == "segment-one" and citation["position"] == 3
    assert citation["content"] == "先停机检查"


def test_agent_v2_is_not_treated_as_legacy_chat_api(environment):
    service, state = environment
    integration, _ = connection_and_binding(service)
    binding = service.create(BINDING, {"name": "Agent V2", "integration_id": integration["id"], "native_id": "agent-one", "app_mode": "agent_v2", "enabled": True})
    with pytest.raises(DomainError) as error:
        service.invoke(BINDING, binding["id"], InvocationInput(query="你好", idempotency_key="agent-v2-one"))
    assert error.value.code == "native_console_required"
    assert "/agents/agent-one" in error.value.fields["launch_url"]
    assert service.session.scalar(select(NativeRun).where(NativeRun.binding_id == binding["id"])) is None


def test_callback_expiration_payload_tampering_and_event_reuse_are_rejected(environment):
    service, state = environment
    integration, binding = connection_and_binding(service)
    state.native_http_transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"workflow_run_id": "native-run", "data": {"status": "running"}}))
    run = service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="callback-two"))
    signed_event(service, integration, run, sequence=1, status="running", event_id="immutable-event")
    with pytest.raises(DomainError) as conflict:
        signed_event(service, integration, run, sequence=2, status="succeeded", event_id="immutable-event")
    assert conflict.value.code == "callback_event_conflict"
    old_timestamp = str(int(time.time()) - 1000)
    signature = hmac.new(b"callback-value", old_timestamp.encode() + b".{}", hashlib.sha256).hexdigest()
    with pytest.raises(DomainError) as expired:
        service.callback(integration["id"], b"{}", old_timestamp, signature)
    assert expired.value.code == "callback_expired"
    timestamp = str(int(time.time()))
    signature = hmac.new(b"callback-value", timestamp.encode() + b".{}", hashlib.sha256).hexdigest()
    with pytest.raises(DomainError) as tampered:
        service.callback(integration["id"], b'{"extra":1}', timestamp, signature)
    assert tampered.value.code == "callback_signature_invalid"


def test_no_success_record_for_unavailable_configuration_or_invalid_workflow_response(environment):
    service, state = environment
    integration, binding = connection_and_binding(service)
    service.update(INTEGRATION, integration["id"], {"enabled": False}, integration["revision"])
    with pytest.raises(DomainError) as disabled:
        service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="disabled-one"))
    assert disabled.value.status == 503
    assert service.session.scalar(select(NativeRun)) is None
    integration = service.store.get(INTEGRATION, integration["id"])
    service.update(INTEGRATION, integration["id"], {"enabled": True}, integration["revision"])
    state.native_http_transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with pytest.raises(DomainError) as invalid:
        service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="invalid-response-one"))
    assert invalid.value.code == "native_invalid_response"
    run = service.session.scalar(select(NativeRun))
    assert run.status == "unknown"


def test_paused_native_workflow_becomes_waiting_review(environment):
    service, state = environment
    _, binding = connection_and_binding(service)
    state.native_http_transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"workflow_run_id": "native-run", "data": {"status": "paused"}}))
    run = service.invoke(AUTOMATION, binding["id"], InvocationInput(idempotency_key="paused-native-one"))
    assert run["status"] == "waiting_review"
    assert TaskService(service.session, service.actor).get(run["task_id"])["status"] == "waiting_review"


def test_rest_dto_revision_and_module_permission_are_enforced(environment):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from agent_platform.core.context import get_actor
    from agent_platform.core.database import get_session
    from agent_platform.modules.integrations.router import router

    service, state = environment
    app = FastAPI()
    app.state.settings, app.state.secret_vault = state.settings, state.secret_vault
    app.include_router(router)
    @app.exception_handler(DomainError)
    def handler(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})
    def session_dependency():
        try:
            yield service.session
            service.session.commit()
        except Exception:
            service.session.rollback()
            raise
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_actor] = lambda: service.actor
    with TestClient(app) as client:
        data = {"name": "连接", "engine": "dify", "base_url": "http://127.0.0.1:12080/v1", "console_url": "http://127.0.0.1:12080", "credential_ref": "app-ref"}
        created = client.post("/api/v1/integration-instances", json=data)
        assert created.status_code == 201
        item = created.json()
        assert client.patch(f"/api/v1/integration-instances/{item['id']}", json={"revision": item["revision"], "name": "新名称"}).status_code == 200
        assert client.patch(f"/api/v1/integration-instances/{item['id']}", json={"revision": item["revision"], "name": "过期编辑"}).status_code == 409
        assert client.post("/api/v1/integration-instances", json={**data, "api_key": "forbidden-cleartext"}).status_code == 422
        app.dependency_overrides[get_actor] = lambda: Actor("user-one", "ws-one", frozenset({"m15.read"}))
        assert client.get("/api/v1/integration-instances").status_code == 200
        assert client.post("/api/v1/integration-instances", json=data).status_code == 403


@pytest.mark.parametrize("response_mode", ["json", "sse"])
def test_mcp_initialization_notifies_and_closes_session_without_calling_tools(response_mode):
    from agent_platform.adapters.mcp import check_mcp
    from agent_platform.adapters.native import NativeHTTP
    calls = []
    def transport(request):
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "new-session"
            return httpx.Response(202)
        result = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}}
        if response_mode == "sse":
            return httpx.Response(200, text="event: message\ndata: " + json.dumps(result) + "\n\n", headers={"content-type": "text/event-stream", "mcp-session-id": "new-session"})
        return httpx.Response(200, json=result, headers={"mcp-session-id": "new-session"})
    client = NativeHTTP(NativeConnection("http://127.0.0.1:19000/mcp", "", auth_required=False), transport=httpx.MockTransport(transport))
    result = check_mcp(client)
    assert result["status"] == "initialized" and result["session_closed"]
    assert not result["executed"]
    assert len(calls) == 3
    assert all(request.url.path == "/mcp" for request in calls)
    assert [json.loads(x.content)["method"] for x in calls if x.method == "POST"] == ["initialize", "notifications/initialized"]


def test_model_gateway_uses_authorized_profile_once_and_records_usage(environment):
    service, state = environment
    integration = service.create(INTEGRATION, {"name": "OWUI", "engine": "openwebui", "base_url": "http://127.0.0.1:13000", "console_url": "http://127.0.0.1:13000", "credential_ref": "app-ref", "enabled": True})
    profile = service.create(PROFILE, {"name": "SQL生成", "integration_id": integration["id"], "role": "reasoning", "native_model_id": "provider/model-one", "enabled": True})
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "SELECT device_id FROM devices"}}], "usage": {"total_tokens": 12}})
    state.native_http_transport = httpx.MockTransport(transport)
    messages = [{"role": "system", "content": "仅生成SQL候选"}, {"role": "user", "content": "查设备"}]
    result = service.model_completion(profile["id"], messages, max_tokens=900)
    assert result["choices"][0]["message"]["content"].startswith("SELECT")
    assert len(calls) == 1 and calls[0].url.path == "/api/chat/completions"
    body = json.loads(calls[0].content)
    assert body["model"] == "provider/model-one" and body["stream"] is False
    assert body["max_tokens"] == 900 and body["tool_ids"] == []
    usage = service.store.list("model_usage_event")["items"]
    assert len(usage) == 1 and usage[0]["usage"]["total_tokens"] == 12
    assert "messages" not in usage[0]
    with pytest.raises(DomainError) as unavailable:
        service.model_completion("", messages)
    assert unavailable.value.status == 503


def test_owui_model_deep_link_handles_provider_slashes(environment):
    service, _ = environment
    integration = service.create(INTEGRATION, {"name": "OWUI", "engine": "openwebui", "base_url": "http://127.0.0.1:13000", "console_url": "http://127.0.0.1:13000", "credential_ref": "app-ref", "enabled": True})
    binding = service.create(BINDING, {"name": "工厂助手", "integration_id": integration["id"], "native_id": "provider/model-one", "app_mode": "openwebui_model", "enabled": True})
    with pytest.raises(DomainError) as native:
        service.invoke(BINDING, binding["id"], InvocationInput(query="问题", idempotency_key="model-chat-one"))
    assert native.value.fields["launch_url"] == "http://127.0.0.1:13000/?model=provider%2Fmodel-one"


def test_native_entries_use_application_permission_without_exposing_config(environment):
    """普通员工可使用获授权的应用，但不能读取连接配置或跨原生能力入口。"""
    service, _ = environment
    visible = service.create(INTEGRATION, {"name": "聊天", "engine": "openwebui", "base_url": "http://127.0.0.1:13000", "console_url": "http://127.0.0.1:13000", "credential_ref": "app-ref", "enabled": True})
    service.create(INTEGRATION, {"name": "已停用", "engine": "openwebui", "base_url": "http://127.0.0.1:13002", "console_url": "http://127.0.0.1:13002", "credential_ref": "app-ref", "enabled": False})
    connection_and_binding(service)
    employee = Actor("employee-one", "ws-one", frozenset({"m02.read"}))
    employee_service = IntegrationService(service.session, employee, service.request)
    entries = employee_service.native_entries("chat")
    assert entries == {"items": [{"id": visible["id"], "name": "聊天", "engine": "openwebui", "enabled": True}], "total": 1}
    assert employee_service.launch(visible["id"], "chat", None)["url"] == "http://127.0.0.1:13000/"
    with pytest.raises(DomainError) as denied:
        employee_service.launch(visible["id"], "admin", None)
    assert denied.value.status == 403
    with pytest.raises(DomainError) as invalid:
        employee_service.native_entries("unknown")
    assert invalid.value.status == 422


def test_native_entries_obey_private_resource_and_workspace_scope(environment):
    """入口目录复用资源授权，隐藏私人连接和其他工厂的配置。"""
    service, _ = environment
    integration = service.create(INTEGRATION, {"name": "私人聊天", "engine": "openwebui", "base_url": "http://127.0.0.1:13000", "console_url": "http://127.0.0.1:13000", "credential_ref": "app-ref", "enabled": True})
    row = service.store._get(INTEGRATION, integration["id"])
    row.visibility = "private"
    service.session.flush()
    for actor in (
        Actor("employee-one", "ws-one", frozenset({"m02.read"})),
        Actor("user-one", "ws-other", frozenset({"m02.read"})),
    ):
        restricted = IntegrationService(service.session, actor, service.request)
        assert restricted.native_entries("chat") == {"items": [], "total": 0}
        with pytest.raises(DomainError) as missing:
            restricted.launch(integration["id"], "chat", None)
        assert missing.value.status == 404


def test_dify_model_configuration_uses_native_provider_settings_and_permission(environment):
    service, _ = environment
    row = service.create(INTEGRATION, {"name": "Dify", "engine": "dify",
        "base_url": "http://127.0.0.1:13001/v1", "console_url": "http://127.0.0.1:13001",
        "credential_ref": "app-ref", "enabled": True})
    reader = IntegrationService(service.session, Actor("employee", "ws-one", frozenset({"m15.read"})), service.request)
    assert reader.native_entries("models")["items"][0]["id"] == row["id"]
    assert reader.launch(row["id"], "models", None)["url"] == "http://127.0.0.1:13001/apps?settings=provider"
    denied = IntegrationService(service.session, Actor("employee", "ws-one", frozenset({"m02.read"})), service.request)
    with pytest.raises(DomainError) as unavailable:
        denied.launch(row["id"], "models", None)
    assert unavailable.value.status == 403
