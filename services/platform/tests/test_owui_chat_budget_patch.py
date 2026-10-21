"""受管原生预算的纯源码、签名及全新临时SQLite账本回归，不调用原生服务。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import ModuleType, SimpleNamespace

from fastapi import HTTPException
import jwt
import pytest
from starlette.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/native"))
import owui_chat_budget_patch as patch
import owui_chat_budget_runtime as runtime

SOURCE = ROOT / ".local/native_windows/sources/openwebui/backend/open_webui"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """密钥是本例固定测试值，账本只允许落本次新建临时目录。"""
    values = {"DATA_DIR": str(tmp_path), "AIC_NATIVE_TURN_BUDGET_ALIAS_ID": "enterprise-assistant",
              "AIC_NATIVE_TURN_BUDGET_SERVER_ID": "business-tools", "AIC_NATIVE_TURN_BUDGET_SECRET": "budget-test-" * 5,
              "WEBUI_SECRET_KEY": "session-test-" * 5, "FORWARD_USER_INFO_HEADER_JWT_SECRET": "forward-test-" * 5}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return SimpleNamespace(directory=tmp_path, secret=values["AIC_NATIVE_TURN_BUDGET_SECRET"], user=SimpleNamespace(id="native-user"))


def payload():
    return {"model": "enterprise-assistant", "chat_id": "native-chat", "id": "assistant-message",
            "user_message": {"id": "user-message", "content": "生产情况"},
            "tool_ids": ["server:business-tools"], "params": {"function_calling": "native", "max_tokens": 64},
            "features": {"memory": False}, "background_tasks": {}, "session_id": "native-session"}


def request(setup, body=None, *, nonce=None, updates=None):
    body = deepcopy(body if body is not None else payload())
    issued = int(time.time())
    kind = "resolution" if nonce else "completion"
    path = "/api/v1/chats/native-chat/messages/assistant-message/resolve" if nonce else "/api/chat/completions"
    claims = {"iss": "ai-agent-company", "aud": "openwebui-native-turn-budget-v1", "v": 1, "jti": "platform-turn",
              "sub": setup.user.id, "workspace_id": "workspace", "integration_id": "integration", "chat_id": "native-chat",
              "user_message_id": "user-message", "message_id": "assistant-message", "model_id": "enterprise-assistant",
              "server_id": "business-tools", "policy_revision": 1, "max_tool_calls": 4, "max_tool_iterations": 2,
              "max_model_requests": 3, "max_tokens": 64, "max_seconds": 90, "deadline": issued + 30,
              "iat": issued, "exp": issued + 30, "dispatch_kind": kind, "dispatch_nonce": nonce or "platform-turn",
              "request_path": path, "body_sha256": runtime.digest(body)}
    claims.update(updates or {})
    token = jwt.encode(claims, setup.secret, algorithm="HS256", headers={"typ": runtime.TOKEN_TYPE})
    return SimpleNamespace(state=SimpleNamespace(), headers={runtime.HEADER: token}, url=SimpleNamespace(path=path)), body, claims


def admitted(setup):
    req, body, claims = request(setup)
    asyncio.run(runtime.enter_completion(req, body, setup.user))
    return req, claims


def counts(setup):
    """只读本例已创建的专属账本，不读取任何运行中SQLite文件。"""
    with sqlite3.connect(setup.directory / "aic-native-turn-budget.sqlite3") as connection:
        row = connection.execute("SELECT models,iterations,calls FROM turns WHERE id='platform-turn'").fetchone()
        nonces = connection.execute("SELECT COUNT(*) FROM nonces").fetchone()[0]
    return row, nonces


def tool(call_id, value="A"):
    return {"id": call_id, "function": {"name": "list_saved_queries", "arguments": json.dumps({"q": value})}}


def test_original_models_do_not_need_budget_or_create_ledger(tmp_path, monkeypatch):
    for key in ("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", "AIC_NATIVE_TURN_BUDGET_SERVER_ID", "AIC_NATIVE_TURN_BUDGET_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    req = SimpleNamespace(state=SimpleNamespace(), headers={}, url=SimpleNamespace(path="/api/chat/completions"))
    body = {"model": "deepseek-v4.1-flash", "max_tokens": 128}
    asyncio.run(runtime.enter_completion(req, body, SimpleNamespace(id="ordinary-user")))
    runtime.provider_context(req, body, SimpleNamespace(id="ordinary-user"))
    runtime.provider_payload(req, body, SimpleNamespace(id="ordinary-user"))
    runtime.consume_tool_iteration(req, [tool("call1")])
    assert body == {"model": "deepseek-v4.1-flash", "max_tokens": 128}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("updates", [{"sub": "another-user"}, {"server_id": "another-server"},
                                     {"max_model_requests": True}, {"max_tokens": 4097}, {"dispatch_nonce": "wrong"},
                                     {"aud": ["openwebui-native-turn-budget-v1"]}, {"extra": "untrusted"}])
def test_invalid_signed_claims_fail_before_creating_ledger(setup, updates):
    req, body, _ = request(setup, updates=updates)
    with pytest.raises(HTTPException) as error:
        asyncio.run(runtime.enter_completion(req, body, setup.user))
    assert error.value.status_code == 403
    assert not (setup.directory / "aic-native-turn-budget.sqlite3").exists()


@pytest.mark.parametrize("mutation", ["body", "unsigned", "wrong_signature", "path"])
def test_body_and_endpoint_binding_fail_closed(setup, mutation):
    req, body, _ = request(setup)
    if mutation == "body":
        body["session_id"] = "different-socket"
    elif mutation == "unsigned":
        req.headers = {}
    elif mutation == "wrong_signature":
        token = req.headers[runtime.HEADER]
        claims = jwt.decode(token, options={"verify_signature": False})
        req.headers[runtime.HEADER] = jwt.encode(claims, "another-secret-" * 4, algorithm="HS256", headers={"typ": runtime.TOKEN_TYPE})
    else:
        req.url.path = "/api/v1/chat/completions"
    with pytest.raises(HTTPException):
        asyncio.run(runtime.enter_completion(req, body, setup.user))
    assert not (setup.directory / "aic-native-turn-budget.sqlite3").exists()


def test_first_dispatch_is_not_replayed_and_does_not_reset_ledger(setup):
    admitted(setup)
    second, body, _ = request(setup)
    with pytest.raises(HTTPException) as error:
        asyncio.run(runtime.enter_completion(second, body, setup.user))
    assert (error.value.status_code, error.value.detail) == (409, "aic_native_budget_replayed")
    assert counts(setup) == ((0, 0, 0), 1)


def test_model_counter_includes_context_compaction_and_caps_actual_payload(setup):
    req, _ = admitted(setup)
    for index in range(3):
        body = {"model": "real-base-model", "max_tokens": 1000, "max_completion_tokens": 900,
                "n": 4, "metadata": {"task": "context_compaction" if index == 0 else "chat"}}
        runtime.provider_payload(req, body, setup.user)
        assert body["max_tokens"] == body["max_completion_tokens"] == 64
        assert body["n"] == 1
    with pytest.raises(HTTPException) as error:
        runtime.provider_payload(req, {"model": "real-base-model"}, setup.user)
    assert error.value.detail == "aic_native_budget_model_limit"
    assert counts(setup) == ((3, 0, 0), 1)


def test_raw_provider_api_cannot_use_unsigned_alias(setup):
    req = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException):
        runtime.provider_context(req, {"model": "enterprise-assistant"}, setup.user)
    runtime.provider_context(req, {"model": "deepseek-v4.1-flash"}, setup.user)
    assert not (setup.directory / "aic-native-turn-budget.sqlite3").exists()


def test_iterations_are_consumed_before_tools_and_approval_subset_is_not_charged_again(setup):
    req, _ = admitted(setup)
    calls = [tool("first"), tool("second")]
    runtime.consume_tool_iteration(req, calls)
    assert counts(setup) == ((0, 1, 2), 1)
    runtime.consume_tool_iteration(req, [deepcopy(calls[1])])
    assert counts(setup) == ((0, 1, 2), 1)
    runtime.consume_tool_iteration(req, [tool("third")])
    assert counts(setup) == ((0, 2, 3), 1)
    with pytest.raises(HTTPException) as error:
        runtime.consume_tool_iteration(req, [tool("fourth")])
    assert error.value.detail == "aic_native_budget_tool_limit"
    assert counts(setup) == ((0, 2, 3), 1)
    with pytest.raises(HTTPException):
        runtime.consume_tool_iteration(req, [tool("second", "changed")])


def mock_native_chat(monkeypatch, setup):
    module = ModuleType("open_webui.models.chats")
    class Chats:
        @staticmethod
        async def get_accessible_chat_by_id(*args, **kwargs):
            return SimpleNamespace(user_id=setup.user.id)

        @staticmethod
        async def get_message_by_id_and_message_id(*args, **kwargs):
            return {"model": "enterprise-assistant", "parentId": "user-message"}
    module.Chats = Chats
    monkeypatch.setitem(sys.modules, "open_webui.models.chats", module)


def test_approval_nonce_and_caps_survive_resume_without_rehashing_rebuilt_payload(setup, monkeypatch):
    initial, claims = admitted(setup)
    runtime.provider_payload(initial, {"max_tokens": 64}, setup.user)
    runtime.consume_tool_iteration(initial, [tool("first")])
    mock_native_chat(monkeypatch, setup)
    body = {"call_id": "first", "action": "approve"}
    req, body, _ = request(setup, body, nonce="action-one", updates={"deadline": claims["deadline"], "exp": claims["exp"]})
    asyncio.run(runtime.enter_resolution(req, "native-chat", "assistant-message", body, setup.user))
    rebuilt = payload()
    rebuilt["params"]["max_tokens"] = 1000
    asyncio.run(runtime.enter_completion(req, rebuilt, setup.user))
    runtime.consume_tool_iteration(req, [tool("first")])
    runtime.provider_payload(req, {}, setup.user)
    assert counts(setup) == ((2, 1, 1), 2)
    replay, body, _ = request(setup, body, nonce="action-one", updates={"deadline": claims["deadline"], "exp": claims["exp"]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(runtime.enter_resolution(replay, "native-chat", "assistant-message", body, setup.user))
    assert error.value.status_code == 409
    changed, body, _ = request(setup, body, nonce="action-two", updates={"deadline": claims["deadline"], "exp": claims["exp"], "max_model_requests": 4})
    with pytest.raises(HTTPException):
        asyncio.run(runtime.enter_resolution(changed, "native-chat", "assistant-message", body, setup.user))
    assert counts(setup) == ((2, 1, 1), 2)


def test_unapproved_unsigned_resolution_has_no_nonce_or_native_side_effect(setup, monkeypatch):
    admitted(setup)
    mock_native_chat(monkeypatch, setup)
    req = SimpleNamespace(state=SimpleNamespace(), headers={}, url=SimpleNamespace(path="/api/v1/chats/native-chat/messages/assistant-message/resolve"))
    with pytest.raises(HTTPException):
        asyncio.run(runtime.enter_resolution(req, "native-chat", "assistant-message", {"call_id": "first", "action": "approve"}, setup.user))
    assert counts(setup) == ((0, 0, 0), 1)


def test_expired_budget_cannot_dispatch_or_consume_more(setup):
    req, _ = admitted(setup)
    req.state.aic_native_turn_budget.monotonic_deadline = time.monotonic() - 1
    for operation in (lambda: runtime.provider_payload(req, {}, setup.user),
                      lambda: runtime.consume_tool_iteration(req, [tool("first")])):
        with pytest.raises(HTTPException) as error:
            operation()
        assert error.value.detail == "aic_native_budget_deadline"
    assert counts(setup) == ((0, 0, 0), 1)


def test_deadline_cancels_waiting_model_and_stream_without_retries(setup):
    req, _ = admitted(setup)
    calls = []
    async def blocked(request, body, user, **kwargs):
        calls.append("model")
        await asyncio.Event().wait()
    req.state.aic_native_turn_budget.monotonic_deadline = time.monotonic() + 0.01
    with pytest.raises(HTTPException) as error:
        asyncio.run(runtime.model_dispatch(req, {}, setup.user, blocked))
    assert error.value.detail == "aic_native_budget_deadline"
    assert calls == ["model"]
    closed = []
    async def chunks():
        try:
            yield "one"
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    async def response(*args, **kwargs):
        return StreamingResponse(chunks())
    async def consume():
        req.state.aic_native_turn_budget.monotonic_deadline = time.monotonic() + 0.01
        result = await runtime.model_dispatch(req, {}, setup.user, response)
        assert await anext(result.body_iterator) == "one"
        await anext(result.body_iterator)
    with pytest.raises(HTTPException):
        asyncio.run(consume())
    assert closed == [True]


def test_capability_has_only_frozen_public_fields_and_unknown_database_is_not_adopted(setup):
    assert runtime.capability() == {"version": 1, "patch_id": patch.PATCH_ID,
                                    "alias_model_id": "enterprise-assistant", "server_id": "business-tools", "ledger_ready": True}
    path = setup.directory / "aic-native-turn-budget.sqlite3"
    path.unlink()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    original = path.read_bytes()
    with pytest.raises(HTTPException) as error:
        runtime.capability()
    assert error.value.status_code == 503
    assert path.read_bytes() == original


@pytest.mark.parametrize("name", list(patch.ORIGINAL_SHA256))
def test_locked_sources_compile_and_are_idempotent_without_mutating_runtime(name):
    original = (SOURCE / name).read_bytes()
    assert patch.digest(original) == patch.ORIGINAL_SHA256[name]
    changed, receipt = patch.prepare_patch(name, original)
    assert receipt["applied"]
    again, receipt = patch.prepare_patch(name, changed)
    assert again == changed and not receipt["applied"]
    with pytest.raises(RuntimeError):
        patch.prepare_patch(name, original + b"\n")
    assert (SOURCE / name).read_bytes() == original


def test_hooks_precede_approval_tool_and_model_side_effects():
    main = patch.transform("main.py", (SOURCE / "main.py").read_bytes()).decode()
    assert main.index("await enter_completion(request, form_data, user)") < main.index("model_item = form_data.pop")
    assert main.index("await enter_resolution(request, id, message_id") < main.index("resolution = await resolve_tool_call_output")
    assert "user=Depends(get_verified_user)):\n    # 只返回" in main
    middleware = patch.transform("utils/middleware.py", (SOURCE / "utils/middleware.py").read_bytes()).decode()
    assert middleware.index("consume_tool_iteration(request, tool_calls[0])") < middleware.index("error = resolve_tool_call(tool_call", middleware.index("tool_call_iterations += 1"))
    provider = patch.transform("routers/openai.py", (SOURCE / "routers/openai.py").read_bytes()).decode()
    index = provider.index("provider_payload(request, payload, user)")
    assert index < provider.index("payload = JSONCodec.dumps(payload)", index) < provider.index("r = await session.request", index)


def test_environment_is_absent_by_default(tmp_path):
    paths = {"data": tmp_path / "data/openwebui"}
    assert patch.budget_environment(None, paths, {}) == {}
    assert not (tmp_path / "data/openwebui/aic-native-turn-budget.sqlite3").exists()


def managed_copy(tmp_path):
    """只复制四个已核纯源码文件到临时安装副本，不接触运行根的文件。"""
    root = tmp_path / "runtime"
    source = root / "sources/openwebui"
    backend = source / "backend/open_webui"
    for name in patch.ORIGINAL_SHA256:
        target = backend / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SOURCE / name).read_bytes())
    (source / "package.json").write_text(json.dumps({"version": "0.12.3"}), encoding="utf-8")
    (root / "runtime.json").write_text(json.dumps({"kind": "manufacturing-native-windows", "root": str(root),
        "id": "runtime-test", "sources": {"openwebui": str(source)}}), encoding="utf-8")
    return root, backend


def test_installer_is_atomic_per_known_source_and_idempotent_in_new_copy(tmp_path):
    root, backend = managed_copy(tmp_path)
    first = patch.apply_patch(root)
    assert all(item["applied"] for item in first["files"])
    assert first["global_iteration_limit_changed"] is False
    assert patch.digest((backend / patch.HELPER_TARGET).read_bytes()) == patch.HELPER_SHA256
    again = patch.apply_patch(root)
    assert all(not item["applied"] for item in again["files"])
    assert not list(backend.rglob("*.pending"))


def test_unknown_source_or_helper_is_not_overwritten(tmp_path):
    root, backend = managed_copy(tmp_path)
    target = backend / "utils/chat.py"
    target.write_bytes(target.read_bytes() + b"\n")
    before = {name: (backend / name).read_bytes() for name in patch.ORIGINAL_SHA256}
    with pytest.raises(RuntimeError, match="source_unknown"):
        patch.apply_patch(root)
    assert not (backend / patch.HELPER_TARGET).exists()
    assert before == {name: (backend / name).read_bytes() for name in patch.ORIGINAL_SHA256}
    target.write_bytes((SOURCE / "utils/chat.py").read_bytes())
    helper = backend / patch.HELPER_TARGET
    helper.write_text("# 未确认的现有守卫\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="helper_unknown"):
        patch.apply_patch(root)
    assert helper.read_text("utf-8") == "# 未确认的现有守卫\n"


def test_enabled_environment_verifies_all_patches_and_same_tool_server(tmp_path, monkeypatch):
    root, backend = managed_copy(tmp_path)
    patch.apply_patch(root)
    config = {"kind": patch.CONFIG_KIND, "runtime_id": "runtime-test", "enabled": True,
              "alias_id": "enterprise-assistant", "server_id": "business-tools"}
    (root / patch.CONFIG_FILE).write_text(json.dumps(config), encoding="utf-8")
    paths = {"data": root / "data/openwebui"}
    secrets = {patch.SECRET_KEY: "budget-test-" * 5, "webui_secret": "session-test-" * 5,
               "openwebui_tool_forward_jwt_secret": "forward-test-" * 5}
    import owui_tool_forward_patch as forwarding
    monkeypatch.setattr(forwarding, "forwarding_environment", lambda *args: {"AIC_PLATFORM_TOOL_SERVER_ID": "business-tools"})
    env = patch.budget_environment(None, paths, secrets)
    assert env == {"AIC_NATIVE_TURN_BUDGET_SECRET": secrets[patch.SECRET_KEY],
                   "AIC_NATIVE_TURN_BUDGET_ALIAS_ID": "enterprise-assistant", "AIC_NATIVE_TURN_BUDGET_SERVER_ID": "business-tools"}
    import windows_services
    paths.update({"sqlite": paths["data"] / "webui.db", "static": root / "static", "frontend": root / "frontend",
                  "backend": backend.parent, "model_cache": root / "cache"})
    owner = SimpleNamespace(base_environment=lambda value: value, openwebui_provider_environment=lambda: {})
    process_env = windows_services.openwebui_environment(owner, paths, secrets)
    assert {key: process_env[key] for key in env} == env
    assert "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS" not in process_env
    monkeypatch.setattr(forwarding, "forwarding_environment", lambda *args: {"AIC_PLATFORM_TOOL_SERVER_ID": "different-server"})
    with pytest.raises(RuntimeError, match="server_mismatch"):
        patch.budget_environment(None, paths, secrets)
    secrets[patch.SECRET_KEY] = secrets["webui_secret"]
    with pytest.raises(RuntimeError, match="not_ready"):
        patch.budget_environment(None, paths, secrets)


def test_tool_call_limit_refuses_entire_new_batch_without_partial_charge(setup):
    req, _ = admitted(setup)
    with pytest.raises(HTTPException) as error:
        runtime.consume_tool_iteration(req, [tool("call" + str(index)) for index in range(5)])
    assert error.value.detail == "aic_native_budget_tool_limit"
    assert counts(setup) == ((0, 0, 0), 1)


def test_background_process_uses_original_cancellation_path(setup):
    req, _ = admitted(setup)
    cleanup = []
    @runtime.bounded_process
    async def original_process(request):
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.append("original-finally")
    req.state.aic_native_turn_budget.monotonic_deadline = time.monotonic() + 0.01
    with pytest.raises(HTTPException) as error:
        asyncio.run(original_process(req))
    assert error.value.detail == "aic_native_budget_deadline"
    assert cleanup == ["original-finally"]


def test_config_and_budget_repr_do_not_disclose_secret(setup):
    req, _ = admitted(setup)
    assert setup.secret not in repr(runtime.configuration())
    assert setup.secret not in repr(req.state.aic_native_turn_budget)
