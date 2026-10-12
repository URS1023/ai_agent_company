"""Actual HTTP/SQLite workflow execution, versions, branches, permissions and trace."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Run, RunStep
from app.modules.workflows.models import WorkflowExecution
from test_analytics import source
from test_collection import create_source, upstream
from test_system_foundation import activate_user, create_role, create_user

API = "/api/v1/workflows"


def node(node_id, kind, config=None, inputs=None):
    return {"id": node_id, "title": node_id, "kind": kind, "config": config or {}, "inputs": inputs or {}}


def edge(source, target, port="out"):
    return {"id": source + "-" + target + "-" + port, "source": source, "target": target, "port": port}


def graph(*middle):
    nodes = [node("start", "start"), *middle, node("end", "end")]
    return {"nodes": nodes, "edges": [edge(a["id"], b["id"]) for a, b in zip(nodes, nodes[1:])]}


def create_flow(client, headers, flow_graph):
    response = client.post(API, headers=headers, json={"name": "预警质检共享流程", "domains": ["alert", "quality"], "graph": flow_graph})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def execute(client, headers, flow, inputs=None, **kwargs):
    return client.post(API + "/" + flow["id"] + "/runs", headers=headers,
                       json={"version": flow["version"], "mode": "draft", "idempotencyKey": uuid4().hex,
                             "inputs": inputs or {}, **kwargs})


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def test_workflow_timestamps_preserve_utc_across_list_and_detail(client, administrator):
    flow = create_flow(client, administrator, graph())
    listed = result(client.get(API, headers=administrator))
    record = next(item for item in listed if item["id"] == flow["id"])
    detail = result(client.get(API + "/" + flow["id"], headers=administrator))
    for key in ("createTime", "updateTime"):
        assert datetime.fromisoformat(flow[key]).utcoffset() == timedelta(0)
        assert record[key] == detail[key] == flow[key]
    revisions = result(client.get(API + "/" + flow["id"] + "/revisions", headers=administrator))
    assert all(datetime.fromisoformat(item["createTime"]).utcoffset() == timedelta(0) for item in revisions)
    execution = result(execute(client, administrator, flow))
    runs = result(client.get(API + "/" + flow["id"] + "/runs", headers=administrator))
    assert datetime.fromisoformat(execution["createTime"]).utcoffset() == timedelta(0)
    assert runs[0]["createTime"] == execution["createTime"]


def test_catalog_documents_sources_and_does_not_pretend_pending_nodes_work(client, administrator):
    entry = next(item for item in client.get('/api/v1/platform/navigation', headers=administrator).json()['items'] if item['id'] == 'workflows')
    assert entry['available'] and entry['path'] == '/workflows'
    workflow_list = client.get('/workflows')
    workflow_designer = client.get('/workflows/new')
    device_designer = client.get('/alerts/devices/device_1/workflow')
    access_point = client.get('/workflows/new/access-point')
    device_logs = client.get('/alerts/devices/device_1/workflow/logs')
    assert workflow_list.status_code == workflow_designer.status_code == device_designer.status_code == access_point.status_code == device_logs.status_code == 200
    assert 'id="platform-root"' in workflow_list.text and 'id="analytics-app"' not in workflow_list.text
    assert 'id="analytics-app"' in workflow_designer.text and 'id="platform-root"' not in workflow_designer.text
    assert 'id="analytics-app"' in device_designer.text and 'id="platform-root"' not in device_designer.text
    assert 'id="analytics-app"' in access_point.text and 'id="analytics-app"' in device_logs.text
    assert client.get('/workflows/not/a/route').status_code == 404
    assert client.get('/workflows/new/not-a-page').status_code == 404
    catalog = result(client.get(API + "/catalog", headers=administrator))
    entries = {item["kind"]: item for item in catalog}
    for item in entries.values():
        if not item["available"]:
            continue
        assert isinstance(item["defaultConfig"], dict)
        assert set(item["defaultConfig"]) <= set(item["configSchema"]["properties"])
    assert entries["http-request"]["available"] and entries["arithmetic"]["configSchema"]
    defaults = entries["http-request"]["defaultConfig"]
    assert defaults["datasourceId"] == "" and defaults["datasourceVersion"] is None
    assert defaults["confirmWrites"] is False and defaults["retry"] == 0
    assert defaults["auth"]["type"] == "none"
    assert entries["arithmetic"]["defaultConfig"]["fields"] == [{"name": "", "formula": ""}]
    assert entries["qc-appearance"]["available"] and entries["qc-evaluate"]["configSchema"]
    assert entries["loop"]["available"] and entries["question"]["available"] and entries["tool"]["configSchema"]
    flow = create_flow(client, administrator, graph(node("vision", "qc-appearance")))
    assert not flow["validation"]["valid"]
    assert execute(client, administrator, flow).status_code == 422
    spec = client.app.openapi()
    operations = [operation for path, methods in spec["paths"].items() if path.startswith(API)
                  for method, operation in methods.items() if method in {"get", "post", "put", "delete"}]
    # 当前工作流契约包含设备入口（device-entry）接口，路由总数为 25；新增接口必须同步更新门禁基线。
    assert len(operations) == 25
    assert all(operation.get("summary") and operation.get("description") and operation["operationId"].startswith("workflows_") for operation in operations)


def test_monitor_aggregates_only_persisted_workflow_runs(client, administrator):
    flow = create_flow(client, administrator, graph())
    result(execute(client, administrator, flow, {"sequence": 1}))
    result(execute(client, administrator, flow, {"sequence": 2}))

    monitor = result(client.get(API + f"/{flow['id']}/monitor?days=7", headers=administrator))

    assert monitor["days"] == 7 and len(monitor["series"]) == 7
    assert monitor["totals"]["runs"] == 2
    assert monitor["totals"]["completed"] == 2
    assert monitor["totals"]["activeUsers"] == 1
    assert monitor["totals"]["averageRunsPerUser"] == 2
    assert sum(item["runs"] for item in monitor["series"]) == 2


def test_edge_sides_are_persisted_without_changing_execution_semantics(client, administrator):
    flow_graph = graph(node("calc", "arithmetic", {"fields": [{"name": "y", "formula": "x + 1"}]}))
    flow_graph["edges"][0].update({"sourceSide": "top", "targetSide": "bottom"})
    flow_graph["edges"][1].update({"sourceSide": "left", "targetSide": "right"})

    flow = create_flow(client, administrator, flow_graph)

    assert [(item["sourceSide"], item["targetSide"]) for item in flow["graph"]["edges"]] == [
        ("top", "bottom"), ("left", "right"),
    ]
    run = result(execute(client, administrator, flow, {"data": [{"x": 2}]}))
    assert run["status"] == "completed"
    assert run["result"]["outputs"]["end"]["data"] == [{"x": 2, "y": "3"}]


def test_old_edges_receive_stable_default_sides(client, administrator):
    flow = create_flow(client, administrator, graph())

    assert flow["graph"]["edges"] == [{
        "id": "start-end-out", "source": "start", "target": "end", "port": "out",
        "sourceSide": "right", "targetSide": "left",
    }]


def test_true_http_arithmetic_rules_and_durable_node_provenance(client, administrator, upstream, db):
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    flow = create_flow(client, administrator, graph(
        node("http", "http-request", {"datasourceId": http["id"]}),
        node("calc", "arithmetic", {"fields": [{"name": "doubled", "formula": "amount * 2"}]}),
        node("rules", "rule-evaluate", {"rules": [{"id": "high", "name": "金额超限", "expression":
            {"op": "gte", "args": [{"field": "doubled"}, {"value": 200}]}}]}),
    ))
    execution = result(execute(client, administrator, flow))
    output = execution["result"]["outputs"]["end"]
    assert output["data"][0]["doubled"] == "246.900"
    assert output["issues"] == [{"rowIndex": 0, "ruleId": "high", "ruleName": "金额超限", "severity": "warning", "message": ""}]
    assert output["passed"] is False and execution["status"] == "completed"
    assert len(upstream[1]) == 1
    saved = result(client.get(API + f"/{flow['id']}/runs/{execution['id']}", headers=administrator))
    assert saved == execution
    assert db.get(Run, execution["runId"]).status == "completed"
    steps = db.scalars(select(RunStep).where(RunStep.run_id == execution["runId"])).all()
    assert len(steps) == 5 and all(step.status == "completed" for step in steps)
    assert all("amount" not in json.dumps(step.output_json) for step in steps)
    assert execution["result"]["nodes"]["http"]["summary"]["queryId"]


def test_published_snapshot_stays_pinned_and_restore_creates_new_revision(client, administrator):
    flow = create_flow(client, administrator, graph(node("calc", "arithmetic", {"fields": [{"name": "y", "formula": "x + 1"}]})))
    flow = result(client.post(API + f"/{flow['id']}/publish", headers=administrator, json={"version": 1}))
    assert flow["publishedRevision"] == 2
    updated = deepcopy(flow["graph"])
    updated["nodes"][1]["config"]["fields"][0]["formula"] = "x + 10"
    payload = {"name": "新版草稿", "graph": updated, "version": flow["version"]}
    flow = result(client.put(API + "/" + flow["id"], headers=administrator, json=payload))
    assert flow["version"] == 3 and flow["publishedRevision"] == 2
    assert client.put(API + "/" + flow["id"], headers=administrator, json=payload).status_code == 409
    published = result(execute(client, administrator, flow, {"data": [{"x": 1}]}, mode="published"))
    draft = result(execute(client, administrator, flow, {"data": [{"x": 1}]}))
    assert published["revision"] == 2 and published["result"]["outputs"]["end"]["data"][0]["y"] == "2"
    assert draft["revision"] == 3 and draft["result"]["outputs"]["end"]["data"][0]["y"] == "11"
    restored = result(client.post(API + f"/{flow['id']}/restore", headers=administrator, json={"version": 3, "revision": 1}))
    assert restored["version"] == 4 and restored["publishedRevision"] == 2
    assert len(result(client.get(API + f"/{flow['id']}/revisions", headers=administrator))) == 4


def test_duplicate_key_replays_result_even_after_draft_changes_without_http(client, administrator, upstream):
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": http["id"]})))
    first = result(execute(client, administrator, flow, idempotencyKey="same-run-0001"))
    updated = result(client.put(API + "/" + flow["id"], headers=administrator,
                               json={"name": "已修改", "graph": flow["graph"], "version": flow["version"]}))
    second = result(execute(client, administrator, flow, idempotencyKey="same-run-0001"))
    assert first == second and len(upstream[1]) == 1
    assert execute(client, administrator, updated, idempotencyKey="same-run-0001").status_code == 409
    assert len(upstream[1]) == 1


def test_database_bound_parameters_are_actual_and_writes_never_execute(client, administrator, source):
    config = {"datasourceId": source["id"], "query": "SELECT amount FROM sales WHERE department = :dept ORDER BY amount"}
    mapping = {"parameters": {"nodeId": "start", "path": "$.params"}}
    flow = create_flow(client, administrator, graph(node("query", "database-query", config, mapping)))
    run = result(execute(client, administrator, flow, {"params": {"dept": "设备"}}))
    assert run["result"]["outputs"]["end"]["data"] == [{"amount": 100}, {"amount": 300}, {"amount": 500}]
    config["query"] = "DELETE FROM sales"
    bad = create_flow(client, administrator, graph(node("query", "database-query", config)))
    assert result(execute(client, administrator, bad), 502)["status"] == "failed"
    assert result(execute(client, administrator, flow, {"params": {"dept": "设备"}}))["result"]["outputs"]["end"]["totalCount"] == 3


def test_condition_skips_other_branch_and_merge_uses_only_real_inputs(client, administrator):
    nodes = [node("start", "start"), node("choice", "condition", {"expression": {"field": "flag"}}),
             node("yes", "arithmetic", {"fields": [{"name": "y", "formula": "x + 1"}]}, {"input": {"nodeId": "choice", "path": "$.data"}}),
             node("no", "arithmetic", {"fields": [{"name": "y", "formula": "x / 0"}]}, {"input": {"nodeId": "choice", "path": "$.data"}}),
             node("join", "merge"), node("end", "end")]
    flow = create_flow(client, administrator, {"nodes": nodes, "edges": [edge("start", "choice"), edge("choice", "yes", "true"),
            edge("choice", "no", "false"), edge("yes", "join"), edge("no", "join"), edge("join", "end")]})
    run = result(execute(client, administrator, flow, {"flag": True, "data": [{"x": 1}]}))
    assert run["result"]["nodes"]["no"]["status"] == "skipped"
    assert run["result"]["outputs"]["end"]["data"] == [{"x": 1, "y": "2"}]
    failure = result(execute(client, administrator, flow, {"flag": False, "data": [{"x": 1}]}), 502)
    assert failure["result"]["nodes"]["join"]["status"] == "skipped" and not failure["result"]["outputs"]


def test_start_fields_render_a_real_runtime_contract_with_defaults(client, administrator):
    flow_graph = graph()
    flow_graph["nodes"][0]["config"] = {"variables": [
        {"variable": "query", "label": "查询内容", "type": "text-input", "required": True, "maxLength": 20},
        {"variable": "limit", "label": "数量", "type": "number", "default": 100, "unit": "部"},
        {"variable": "includeLowRated", "label": "包含低分电影", "type": "checkbox", "default": False},
        {"variable": "genre", "label": "类型", "type": "select", "options": ["全部", "剧情"], "default": "全部"},
    ]}
    flow = create_flow(client, administrator, flow_graph)
    assert flow["validation"]["valid"]

    run = result(execute(client, administrator, flow, {"query": "豆瓣电影"}))
    assert run["result"]["outputs"]["end"] == {
        "query": "豆瓣电影", "limit": 100, "includeLowRated": False, "genre": "全部",
    }
    missing = result(execute(client, administrator, flow), 502)
    assert missing["status"] == "failed" and "缺少必填输入" in missing["errorMessage"]
    unknown = result(execute(client, administrator, flow, {"query": "电影", "extra": True}), 502)
    assert "未定义字段" in unknown["errorMessage"]


def test_node_retry_records_attempts_and_then_completes(client, administrator, db, monkeypatch):
    from app.modules.workflows import engine

    original = engine.execute_node
    attempts = 0

    def flaky(ctx, selected, inputs, budget, **kwargs):
        nonlocal attempts
        if selected.id == "calc" and attempts < 2:
            attempts += 1
            raise RuntimeError("temporary provider failure")
        return original(ctx, selected, inputs, budget, **kwargs)

    monkeypatch.setattr(engine, "execute_node", flaky)
    calc = node("calc", "arithmetic", {"fields": [{"name": "y", "formula": "x + 1"}]})
    calc["retryConfig"] = {"retryEnabled": True, "maxRetries": 3, "retryInterval": 100}
    flow = create_flow(client, administrator, graph(calc))
    run = result(execute(client, administrator, flow, {"data": [{"x": 1}]}))

    assert run["status"] == "completed" and run["result"]["nodes"]["calc"]["retryCount"] == 2
    step = db.scalar(select(RunStep).where(RunStep.run_id == run["runId"], RunStep.name == "calc"))
    assert step.status == "completed" and step.retry_count == 2


def test_default_value_and_error_branch_continue_after_a_real_failure(client, administrator):
    fallback = node("calc", "arithmetic", {"fields": [{"name": "y", "formula": "x / 0"}]})
    fallback.update({"errorStrategy": "default-value", "defaultValue": {"data": [{"x": 1, "y": 0}]}})
    flow = create_flow(client, administrator, graph(fallback))
    run = result(execute(client, administrator, flow, {"data": [{"x": 1}]}))
    assert run["status"] == "completed"
    assert run["result"]["nodes"]["calc"]["status"] == "failed"
    assert run["result"]["nodes"]["calc"]["handled"] is True
    assert run["result"]["outputs"]["end"]["data"][0]["y"] == 0

    broken = node("broken", "arithmetic", {"fields": [{"name": "y", "formula": "x / 0"}]})
    broken["errorStrategy"] = "fail-branch"
    branch_graph = {
        "nodes": [node("start", "start"), broken, node("success", "end"), node("failure", "end")],
        "edges": [
            edge("start", "broken"), edge("broken", "success"), edge("broken", "failure", "error"),
        ],
    }
    flow = create_flow(client, administrator, branch_graph)
    assert flow["validation"]["valid"]
    run = result(execute(client, administrator, flow, {"data": [{"x": 1}]}))
    assert run["status"] == "completed"
    assert run["result"]["nodes"]["success"]["status"] == "skipped"
    assert run["result"]["outputs"]["failure"]["errorType"]
    assert "errorMessage" in run["result"]["outputs"]["failure"]


@pytest.mark.parametrize("operator,expected", [("union", ["A", "B", "C"]), ("intersection", ["B"]), ("difference", ["A"])])
def test_set_nodes_match_selected_record_fields(client, administrator, operator, expected):
    flow = create_flow(client, administrator, graph(node("sets", "set-operation", {"operation": operator, "keys": ["id"]},
        {"left": {"nodeId": "start", "path": "$.a"}, "right": {"nodeId": "start", "path": "$.b"}})))
    run = result(execute(client, administrator, flow, {"a": [{"id": "A"}, {"id": "B", "v": 1}], "b": [{"id": "B", "v": 2}, {"id": "C"}]}))
    rows = run["result"]["outputs"]["end"]["data"]
    assert [row["id"] for row in rows] == expected
    assert all(row.get("v") != 2 for row in rows)


def test_extraction_filter_and_quality_mode_preserve_actual_assessments(client, administrator):
    flow = create_flow(client, administrator, graph(
        node("extract", "jsonpath-extract", {"recordPath": "$.items", "fields": [{"name": "温度", "path": "$.temp", "type": "decimal"}]}),
        node("filter", "filter", {"expression": {"op": "gte", "args": [{"field": "温度"}, {"value": 10}]}}),
        node("rules", "rule-evaluate", {"mode": "quality", "rules": [{"id": "range", "name": "温度范围", "expression":
             {"op": "between", "args": [{"field": "温度"}, {"value": 10}, {"value": 30}]}}]})))
    run = result(execute(client, administrator, flow, {"items": [{"temp": 5}, {"temp": 10}, {"temp": 40}]}))
    output = run["result"]["outputs"]["end"]
    assert output["summary"]["evaluated"] == 2 and len(output["issues"]) == 1
    assert output["assessments"][0]["passed"] is True and output["assessments"][1]["passed"] is False


@pytest.mark.parametrize("variation", ["cycle", "missing_end", "bad_input", "disconnected", "invalid_formula"])
def test_invalid_drafts_cannot_run_and_send_no_http(client, administrator, upstream, variation):
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    draft = graph(node("http", "http-request", {"datasourceId": http["id"]}))
    if variation == "cycle":
        draft["edges"].append(edge("end", "start"))
    elif variation == "missing_end":
        draft["nodes"][2]["kind"] = "start"
    elif variation == "bad_input":
        draft["nodes"][1]["inputs"] = {"input": {"nodeId": "end", "path": "$"}}
    elif variation == "disconnected":
        draft["edges"] = []
    else:
        draft = graph(node("http", "http-request", {"datasourceId": http["id"]}),
                      node("calc", "arithmetic", {"fields": [{"name": "v", "formula": "__import__('os')"}]}))
    flow = create_flow(client, administrator, draft)
    assert not flow["validation"]["valid"]
    assert execute(client, administrator, flow).status_code == 422 and not upstream[1]


def test_partial_http_and_timeout_stop_workflow_and_keep_child_history(client, administrator, upstream):
    http, _ = create_source(client, administrator, upstream, method="POST", bodies=[{}, {"fail": True}], allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {
        "datasourceId": http["id"], "confirmWrites": True,
    })))
    failed = result(execute(client, administrator, flow), 502)
    assert failed["result"]["nodes"]["http"]["references"]["query_id"]
    assert failed["result"]["nodes"]["end"]["status"] == "skipped"
    slow, _ = create_source(client, administrator, upstream, url=upstream[0] + "/slow-headers", allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": slow["id"]})))
    started = time.monotonic()
    failed = result(execute(client, administrator, flow, timeoutSeconds=1), 502)
    assert time.monotonic() - started < 3 and failed["status"] == "failed"


def test_scope_source_revocation_and_no_runtime_privileges_required(client, administrator, upstream):
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": http["id"]})))
    perms = ["workflows.view", "workflows.execute", "workflows.edit", "datasources.view"]
    role = create_role(client, administrator, "workflow_user", perms, "workspace")
    create_user(client, administrator, [role["id"]])
    reader = activate_user(client)
    success = result(execute(client, reader, flow))
    assert success["status"] == "completed"
    bad = create_flow(client, administrator, graph(node("calc", "arithmetic", {"fields": [{"name": "x", "formula": "x / 0"}]})))
    assert result(execute(client, reader, bad, {"data": [{"x": 1}]}), 502)["status"] == "failed"
    assert client.delete(API + f"/{flow['id']}?version=1", headers=reader).status_code == 403
    assert client.patch("/api/v1/system/roles/" + role["id"], headers=administrator,
                        json={"version": role["version"], "permissions": perms[:-1]}).status_code == 200
    assert client.get(API + f"/{flow['id']}/runs/{success['id']}", headers=reader).status_code in {403, 404}
    assert result(client.get(API, headers=reader)) == [result(client.get(API + "/" + bad["id"], headers=reader))]


def test_self_scope_cannot_see_or_execute_someone_elses_graph(client, administrator):
    flow = create_flow(client, administrator, graph())
    role = create_role(client, administrator, "workflow_self", ["workflows.view", "workflows.execute", "workflows.edit"], "self")
    create_user(client, administrator, [role["id"]])
    reader = activate_user(client)
    assert result(client.get(API, headers=reader)) == []
    assert execute(client, reader, flow).status_code == 404


def test_disabled_and_deleted_flows_retain_history_without_new_execution(client, administrator, db):
    flow = create_flow(client, administrator, graph())
    flow = result(client.post(API + f"/{flow['id']}/publish", headers=administrator, json={"version": 1}))
    run = result(execute(client, administrator, flow, {"x": 1}, mode="published"))
    flow = result(client.post(API + f"/{flow['id']}/disable", headers=administrator, json={"version": 2}))
    assert execute(client, administrator, flow, mode="published").status_code == 422
    assert client.delete(API + f"/{flow['id']}?version=3", headers=administrator).status_code == 200
    assert result(client.get(API, headers=administrator)) == []
    assert result(client.get(API + f"/{flow['id']}/runs/{run['id']}", headers=administrator))["inputs"] == {"x": 1}
    assert db.get(WorkflowExecution, run["id"]).status == "completed"


def test_empty_quality_data_is_not_a_pass_and_malformed_operator_is_not_500(client, administrator):
    config = {"mode": "quality", "rules": [{"id": "ok", "name": "合格条件", "expression": {"value": True}}]}
    flow = create_flow(client, administrator, graph(node("rules", "rule-evaluate", config)))
    output = result(execute(client, administrator, flow, {"data": []}))["result"]["outputs"]["end"]
    assert output["passed"] is None and output["summary"]["status"] == "no_data"
    config["rules"][0]["expression"] = {"op": [], "args": []}
    invalid = create_flow(client, administrator, graph(node("rules", "rule-evaluate", config)))
    assert not invalid["validation"]["valid"]
    assert execute(client, administrator, invalid).status_code == 422


def test_unexpected_node_failure_is_redacted_and_jsonpath_never_selects_first(client, administrator, monkeypatch):
    from app.modules.workflows import engine
    flow = create_flow(client, administrator, graph(node("calc", "arithmetic", {"fields": [{"name": "x", "formula": "x + 1"}]},
             {"input": {"nodeId": "start", "path": "$.data[*]"}})))
    failure = result(execute(client, administrator, flow, {"data": [{"x": 1}, {"x": 2}]}), 502)
    assert "匹配到多个值" in failure["errorMessage"]
    assert "$.data[*]" in failure["errorMessage"]
    def broken(*args, **kwargs):
        raise RuntimeError("postgresql://private_user:private_password@server")
    monkeypatch.setattr(engine, "execute_node", broken)
    failure = execute(client, administrator, flow)
    assert failure.status_code == 502 and "private_password" not in failure.text
    assert result(failure, 502)["result"]["nodes"]["start"]["status"] == "failed"
