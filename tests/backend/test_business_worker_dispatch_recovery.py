"""派发崩溃窗口回归：临时 SQLite 与内存 provider，不发任何真实网络请求。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.business_worker.dispatch_state import summarize_dispatches
from app.models import Run, ToolCall
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.gateway import ToolGateway
from test_business_worker import prepare, worker_context  # noqa: F401


class SimulatedCrash(BaseException):
    """绕过普通业务异常处理，模拟进程在已提交调用记录后被中止。"""


@pytest.fixture
def dispatch_fixture(worker_context, monkeypatch):
    """保留真实网关鉴权、校验和 Worker 持久化，只把最终 provider 换成内存桩。"""
    from app.providers.tool import gateway
    spec = ToolSpec("fixture.send_order", "隔离的外部业务操作", "http", input_schema={
        "type": "object", "properties": {"order": {"type": "string"}}, "required": ["order"]})
    spec._tool_version_id = "fixture-dispatch-version"
    spec._connector_spec = {"method": "POST"}
    state = {"stage": "success", "calls": 0, "spec": spec}
    available, resolve = ToolGateway.list_available, ToolGateway.resolve
    monkeypatch.setattr(ToolGateway, "list_available", lambda self, *args, **kwargs: available(self, *args, **kwargs) + [spec])
    monkeypatch.setattr(ToolGateway, "resolve", lambda self, workspace, name, **kwargs:
        spec if name == spec.name else resolve(self, workspace, name, **kwargs))

    def invoke(_spec, _args, context):
        if state["stage"] == "before_dispatch":
            raise SimulatedCrash("登记后尚未派发")
        context["_before_tool_dispatch"](provider="http", external_side_effect=spec._connector_spec["method"] == "POST")
        state["calls"] += 1
        if state["stage"] == "after_dispatch":
            raise SimulatedCrash("远端可能已执行，回执尚未保存")
        return ToolResult(status="success", data={"accepted": True})

    monkeypatch.setitem(gateway._PROVIDERS, "http", SimpleNamespace(invoke=invoke))
    prepared = prepare(worker_context)
    chat.append_event(prepared["run_id"], "run.started", {})
    state["run_id"] = prepared["run_id"]
    return state


def invoke(state, call_id="original", sequence=1, arguments=None):
    """每次进入真实 Worker，让调用身份和事务边界参与回归。"""
    return tools.execute(state["run_id"], call_id, sequence, state["spec"].name, arguments or {"order": "1"})


def saved(db, state):
    db.expire_all()
    return db.scalars(select(ToolCall).where(ToolCall.run_id == state["run_id"]).order_by(ToolCall.started_at)).first()


def test_prepared_crash_is_proven_not_dispatched_and_does_not_freeze_tool(dispatch_fixture, db):
    state = dispatch_fixture
    state["stage"] = "before_dispatch"
    with pytest.raises(SimulatedCrash):
        invoke(state)
    assert saved(db, state).status == "prepared" and state["calls"] == 0
    assert summarize_dispatches(db, db.get(Run, state["run_id"]))["has_execution_trace"] is False
    assert invoke(state)["error_code"] == "tool_not_dispatched"
    recovered = next(run for run in chat.recover()["runs"] if run["run_id"] == state["run_id"])
    assert recovered["tool_dispatches"]["prepared"] == 1
    assert recovered["tool_dispatches"]["has_execution_trace"] is False
    state["stage"] = "success"
    assert invoke(state, "new-attempt", 2)["status"] == "success"
    assert state["calls"] == 1


@pytest.mark.parametrize("stage", ["after_dispatch", "after_return"])
def test_dispatched_crash_blocks_new_call_and_changed_arguments(dispatch_fixture, db, monkeypatch, stage):
    state = dispatch_fixture
    state["stage"] = "after_dispatch" if stage == "after_dispatch" else "success"
    original = tools.persist_artifacts
    if stage == "after_return":
        def crash_after_return(*args, **kwargs):
            raise SimulatedCrash("回执返回后、最终提交前中止")
        monkeypatch.setattr(tools, "persist_artifacts", crash_after_return)
    with pytest.raises(SimulatedCrash):
        invoke(state)
    monkeypatch.setattr(tools, "persist_artifacts", original)
    assert saved(db, state).status == "dispatch_started"
    recovered = next(run for run in chat.recover()["runs"] if run["run_id"] == state["run_id"])
    assert recovered["tool_dispatches"]["has_execution_trace"] is True
    state["stage"] = "success"
    blocked = invoke(state, "different-native-id", 20, {"order": "changed"})
    assert blocked["error_code"] == "tool_result_unconfirmed"
    assert blocked["metadata"]["replayBlocked"] is True
    assert state["calls"] == 1
    assert tools.execute(state["run_id"], "independent-read", 21, "current_datetime", {})["status"] == "success"


@pytest.mark.parametrize("stage,code", [("before_dispatch", "tool_not_dispatched"), ("after_dispatch", "tool_result_unconfirmed")])
def test_settle_preserves_dispatch_fact_and_never_claims_unfinished_completion(dispatch_fixture, db, stage, code):
    state = dispatch_fixture
    state["stage"] = stage
    with pytest.raises(SimulatedCrash):
        invoke(state)
    receipt = chat.settle(state["run_id"], "completed", "请求结束")
    assert receipt["status"] == "interrupted"
    assert receipt["native_status"] == "completed"
    assert receipt["error"]["code"] == code
    record = saved(db, state)
    assert record.status == "failed" and record.error_code == code
    assert record.result_json["metadata"]["dispatch"]["phase"] == ("prepared" if stage == "before_dispatch" else "dispatch_started")
    assert invoke(state, "stale-new-call", 99)["error_code"] == "run_already_settled"
    assert state["calls"] == (0 if stage == "before_dispatch" else 1)


def test_worker_unavailable_recovery_keeps_unknown_receipt(dispatch_fixture, db):
    from app.modules.runtime.chat_liveness import mark_interrupted
    state = dispatch_fixture
    state["stage"] = "after_dispatch"
    with pytest.raises(SimulatedCrash):
        invoke(state)
    run = db.get(Run, state["run_id"])
    assert mark_interrupted(db, run, error_code="dsh_worker_unavailable")
    record = saved(db, state)
    assert record.result_json["error_code"] == "tool_result_unconfirmed"
    assert record.result_json["metadata"]["receiptOrigin"] == "dispatch_journal"
    assert invoke(state, "stale-after-recovery", 99)["error_code"] == "run_already_settled"
    assert state["calls"] == 1


def test_invalid_arguments_never_cross_dispatch_boundary(dispatch_fixture, db):
    state = dispatch_fixture
    result = invoke(state, arguments={"order": 123})
    assert result["status"] == "failed"
    assert state["calls"] == 0
    assert saved(db, state).result_json["metadata"]["dispatch"]["phase"] == "prepared"
    assert invoke(state, "corrected-call", 2)["status"] == "success"
    assert state["calls"] == 1


def test_http_get_interruption_does_not_freeze_read_only_tool(dispatch_fixture, db):
    state = dispatch_fixture
    state["spec"]._connector_spec["method"] = "GET"
    state["stage"] = "after_dispatch"
    with pytest.raises(SimulatedCrash):
        invoke(state)
    assert saved(db, state).result_json["metadata"]["dispatch"]["externalSideEffect"] is False
    state["stage"] = "success"
    assert invoke(state, "retry-read", 2)["status"] == "success"
    assert state["calls"] == 2


def test_legacy_unstaged_external_call_is_honestly_unknown(dispatch_fixture, db):
    from app.modules.runtime.service import record_tool_call
    state = dispatch_fixture
    run = db.get(Run, state["run_id"])
    old = record_tool_call(db, workspace_id=run.workspace_id, run_id=run.id, step_id=None,
        tool_name=state["spec"].name, tool_version_id=None, arguments={"order": "1"})
    old.idempotency_key = "legacy-unfinished-call"
    db.commit()
    blocked = invoke(state, "after-legacy", 2)
    assert blocked["error_code"] == "tool_result_unconfirmed"
    assert "缺少派发阶段" in blocked["metadata"]["originalErrorSummary"]
    assert state["calls"] == 0


def test_read_interruption_is_closed_without_fabricating_external_side_effect(dispatch_fixture, db):
    state = dispatch_fixture
    state["spec"]._connector_spec["method"] = "GET"
    state["stage"] = "after_dispatch"
    with pytest.raises(SimulatedCrash):
        invoke(state)
    receipt = chat.settle(state["run_id"], "completed", "已结束")
    assert receipt["status"] == "interrupted"
    assert receipt["error"]["code"] == "tool_interrupted"
    assert saved(db, state).result_json["metadata"]["dispatch"]["externalSideEffect"] is False


def test_confirmed_result_survives_lost_rpc_without_redispatch(dispatch_fixture):
    state = dispatch_fixture
    first = invoke(state)
    replay = invoke(state)
    assert first == replay and state["calls"] == 1
    assert replay["status"] == "success"


def test_cold_recovery_authorization_failure_still_reconciles_dispatch(dispatch_fixture, db, monkeypatch):
    state = dispatch_fixture
    state["stage"] = "after_dispatch"
    with pytest.raises(SimulatedCrash):
        invoke(state)
    monkeypatch.setattr(chat, "prepared", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("身份已失效")))
    assert chat.recover()["runs"] == []
    assert saved(db, state).result_json["metadata"]["operationOutcome"] == "uncertain"
    assert db.get(Run, state["run_id"]).status == "failed"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("failure", ["target_validation", "journal_commit", "after_send"])
def test_http_marker_follows_target_preflight_and_precedes_send(monkeypatch, method, failure):
    """只用连接桩观察顺序；预检与登记失败时绝不能进入 request。"""
    from app.errors import ToolExecutionError, ValidationError
    from app.modules.collection import transport
    from app.providers.tool import http_provider
    events = []

    def destination(*args, **kwargs):
        events.append("target_checked")
        if failure == "target_validation":
            raise ValidationError("本地目标预检拒绝")
        return transport.Destination(host="fixture.invalid", port=443, ip="203.0.113.1", path="/", tls=True)

    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def abort(self):
            pass
        def close(self):
            pass
        def request(self, *args, **kwargs):
            events.append("request")
            raise SimulatedCrash("在发送边界后中断")

    def before_dispatch(**values):
        assert values == {"provider": "http", "external_side_effect": method == "POST"}
        events.append("journal")
        if failure == "journal_commit":
            raise RuntimeError("派发登记未提交")

    monkeypatch.setattr(transport, "resolve_destination", destination)
    monkeypatch.setattr(transport, "PinnedConnection", Connection)
    context = {"allow_network": True, "_before_tool_dispatch": before_dispatch, "connector_spec": {
        "base_url": "https://fixture.invalid", "path": "/", "method": method, "headers": {}}}
    expected = ValidationError if failure == "target_validation" else ToolExecutionError if failure == "journal_commit" else SimulatedCrash
    with pytest.raises(expected):
        http_provider.invoke(ToolSpec("fixture.http", "隔离调用", "http"), {}, context)
    assert events == (["target_checked"] if failure == "target_validation" else
                      ["target_checked", "journal"] if failure == "journal_commit" else
                      ["target_checked", "journal", "request"])


@pytest.mark.parametrize("failure", ["target_validation", "journal_commit", "after_send"])
def test_mcp_marker_follows_initialization_and_precedes_call(monkeypatch, failure):
    import asyncio
    from app.errors import ToolExecutionError, ValidationError
    from app.providers.tool import mcp_provider
    events = []

    class Session:
        async def call_tool(self, **kwargs):
            events.append("call_tool")
            raise SimulatedCrash("派发后中断")

    async def connect(*args, **kwargs):
        events.append("preflight_and_initialize")
        if failure == "target_validation":
            raise ValidationError("预检拒绝")
        return None, Session(), None, {}

    async def close(*args, **kwargs):
        pass

    def before_dispatch(**values):
        assert values == {"provider": "mcp", "external_side_effect": True}
        events.append("journal")
        if failure == "journal_commit":
            raise RuntimeError("登记未提交")

    monkeypatch.setattr(mcp_provider, "_connect", connect)
    monkeypatch.setattr(mcp_provider, "_close", close)
    expected = ValidationError if failure == "target_validation" else ToolExecutionError if failure == "journal_commit" else SimulatedCrash
    with pytest.raises(expected):
        asyncio.run(mcp_provider._call_tool({}, ToolSpec("fixture.mcp", "隔离调用", "mcp"), {},
            {"allow_network": True, "_before_tool_dispatch": before_dispatch}))
    assert events == (["preflight_and_initialize"] if failure == "target_validation" else
                      ["preflight_and_initialize", "journal"] if failure == "journal_commit" else
                      ["preflight_and_initialize", "journal", "call_tool"])
