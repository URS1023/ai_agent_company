"""验证异常数据源的任务级停止；取数用内存桩，预警读取走真实平台工具。"""
from types import SimpleNamespace

import pytest

from app.business_worker import chat, tools
from app.models import Run
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.gateway import ToolGateway
from app.providers.tool.builtin_provider import builtin_specs
from test_business_worker import prepare, worker_context  # noqa: F401


@pytest.fixture
def failing_source(monkeypatch):
    """模拟一次明确的连接异常，记录执行器是否被再次调用，不模拟数据库读取。"""
    state = SimpleNamespace(calls=[], fail=True)
    original_execute = ToolGateway.execute
    specs = [ToolSpec(name="database.query", description="隔离数据源", provider="database"),
             ToolSpec(name="query_inventory_data", description="隔离库存取数", provider="builtin"),
             ToolSpec(name="current_datetime", description="独立本地工具", provider="builtin")]
    specs.append(next(spec for spec in builtin_specs() if spec.name == "view_device_alerts"))
    monkeypatch.setattr(ToolGateway, "list_available", lambda *args, **kwargs: specs)

    def invoke(self, workspace, name, arguments, context):
        state.calls.append((name, context["run_id"]))
        if name == "view_device_alerts":
            return original_execute(self, workspace, name, arguments, context)
        if name == "database.query" and state.fail:
            from app.modules.analytics.connections import connection_failure
            return ToolGateway._error_result(name, connection_failure(
                SimpleNamespace(provider="mysql"), TimeoutError("隔离模拟，未建立连接")))
        return ToolResult(status="success", data={"fixture": True})

    monkeypatch.setattr(ToolGateway, "execute", invoke)
    return state


@pytest.mark.parametrize("content", ["继续", "重试刚才失败的任务", "继续，把剩下的做完"])
@pytest.mark.parametrize("queued_before_failure", [False, True])
def test_task_continuation_preserves_datasource_stop(worker_context, failing_source, content, queued_before_failure):
    first = prepare(worker_context, "查询设备状态")
    continued = prepare(worker_context, content) if queued_before_failure else None
    result = tools.execute(first["run_id"], "original", 1, "database.query", {})
    assert result["metadata"]["recovery"] == "stop_datasource"
    chat.settle(first["run_id"], "failed", "数据源异常，已停止")
    continued = continued or prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    failing_source.fail = False
    for index, name in enumerate(("database.query", "query_inventory_data"), 2):
        result = tools.execute(continued["run_id"], "blocked-" + name, index, name, {})
        assert result["error_code"] == "database_access_halted", result
    assert tools.execute(continued["run_id"], "independent", 5, "current_datetime", {})["status"] == "success"
    assert failing_source.calls == [("database.query", first["run_id"]), ("current_datetime", continued["run_id"])]


def test_explicit_independent_task_preserves_existing_dispatch_contract(worker_context, failing_source):
    first = prepare(worker_context, "查询设备状态")
    tools.execute(first["run_id"], "original", 1, "database.query", {})
    chat.settle(first["run_id"], "failed", "异常已停止")
    independent = prepare(worker_context, "另一个独立任务")
    assert independent["task"]["task_id"] != first["task"]["task_id"]
    failing_source.fail = False
    assert tools.execute(independent["run_id"], "new-task", 2, "database.query", {})["status"] == "success"
    assert len(failing_source.calls) == 2


def test_halted_task_reads_actual_saved_alert_report(worker_context, failing_source, client):
    """真实平台预警来自输入常量评估，halt 后只读原报告，不采集设备或重跑工作流。"""
    from test_alerts import business_node, equipment, policy
    from test_workflows import create_flow, execute, graph, result

    headers, _ = worker_context
    rule, _ = policy(client, headers, equipment(client, headers))
    flow = create_flow(client, headers, graph(business_node(rule)))
    evaluated = result(execute(client, headers, flow, {"data": [{"device": "隔离设备", "amount": 200}]}))
    report_id = evaluated["result"]["outputs"]["end"]["alertReportId"]
    first = prepare(worker_context, "查询设备状态并查看已保存的预警报告")
    failed = tools.execute(first["run_id"], "failure", 1, "database.query", {})
    assert failed["metadata"]["recovery"] == "stop_datasource"
    saved = tools.execute(first["run_id"], "saved-alerts", 2, "view_device_alerts", {"report_id": report_id})
    assert saved["status"] == "success", saved
    assert saved["data"]["reports"][0]["id"] == report_id
    assert saved["data"]["reports"][0]["issues"] == 1
    assert saved["data"]["provenance"]["source"] == "持久化预警报告与问题；未重新请求设备"
    assert tools.execute(first["run_id"], "blocked-query", 3, "database.query", {})["error_code"] == "database_access_halted"
    assert failing_source.calls == [("database.query", first["run_id"]), ("view_device_alerts", first["run_id"])]


@pytest.mark.parametrize("field", ["workspace_id", "started_by_user_id", "conversation_id"])
def test_foreign_run_with_same_task_id_does_not_stop_current_task(worker_context, failing_source, db, field):
    first = prepare(worker_context, "查询设备状态")
    tools.execute(first["run_id"], "original", 1, "database.query", {})
    chat.settle(first["run_id"], "failed", "异常已停止")
    continued = prepare(worker_context, "继续")
    db.expire_all()
    setattr(db.get(Run, first["run_id"]), field, "foreign-halt-fixture")
    db.commit()
    failing_source.fail = False
    assert tools.execute(continued["run_id"], "allowed", 2, "database.query", {})["status"] == "success"
    assert len(failing_source.calls) == 2
