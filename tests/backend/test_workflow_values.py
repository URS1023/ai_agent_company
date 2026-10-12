"""Visual value nodes: real SQLite reads, per-row branches and missing evidence."""
import time

import pytest

from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.value_nodes import calculate_one
from app.modules.workflows.value_schemas import ValueCalculation
from test_analytics import source
from test_workflows import API, create_flow, edge, execute, graph, node, result


def calculation(operation, left, right=None):
    return ValueCalculation(name="结果值", operation=operation, left={"source": "constant", "value": left},
                            right={"source": "constant", "value": right} if right is not None else None)


@pytest.mark.parametrize("operation,left,right,expected", [
    ("days_between", "2026-09-01 12:00:00", "2026-09-02 00:00:00", .5),
    ("hours_between", "2026-09-01", "2026-09-02", 24),
    ("days_between", "bad-date", "2026-09-02", None),
    ("days_between", "2026-09-01T00:00:00Z", "2026-09-02", None),
    ("divide", 5, 0, None), ("add", None, 3, None), ("multiply", True, 2, None),
    ("subtract", "5.25", "1.1", "4.15"),
    ("add", "12345678901234567890123456789", 1, "12345678901234567890123456790"),
    ("number", "21V以上", None, None), ("number", "25V", None, "25"),
    ("lower_limit", "21V以上", None, "21"), ("lower_limit", "在10°C以上", None, "10"),
    ("lower_limit", "油位50%以上、无渗漏", None, None), ("unit", "25V", None, "V"),
    ("contains", "夏季≤12%", "夏", True), ("contains", "普通标准", "夏", False),
    ("contains", 12, "夏", None), ("contains", None, "夏", None),
    ("add_months", "2026-01-31", 1, "2026-02-28T00:00:00"),
    ("add_months", "2024-01-31", 1, "2024-02-29T00:00:00"),
    ("add_months", "2026-01-31", 1.5, None),
    ("add_hours", "2026-09-11T08:00:00", 4, "2026-09-11T12:00:00"),
    ("coalesce", None, 0, 0), ("coalesce", 0, 3, 0),
])
def test_calculations_do_not_guess_missing_or_compound_values(operation, left, right, expected):
    assert calculate_one(calculation(operation, left, right), {}) == expected


def test_row_branches_only_forward_their_own_records(client, administrator):
    nodes = [node("start", "start"), node("calc", "calculate-values", {"calculations": [
        {"name": "已过天数", "operation": "days_between", "left": {"source": "field", "field": "上次时间"},
         "right": {"source": "field", "field": "参考时间"}}]}),
        node("missing", "condition", {"mode": "rows", "expression": {"op": "missing", "args": [{"field": "已过天数"}]}}),
        node("compare", "condition", {"mode": "rows", "expression": {"op": "gt", "args": [{"field": "已过天数"}, {"field": "周期"}]}}),
        *[node(k, "output-values", {"conclusion": k}) for k in ("pending", "warning", "normal")],
        node("merge", "merge"), node("end", "end")]
    edges = [edge("start", "calc"), edge("calc", "missing"), edge("missing", "pending", "true"),
             edge("missing", "compare", "false"), edge("compare", "warning", "true"), edge("compare", "normal", "false"),
             *[edge(k, "merge") for k in ("pending", "warning", "normal")], edge("merge", "end")]
    flow = create_flow(client, administrator, {"nodes": nodes, "edges": edges})
    inputs = {"data": [{"recordKey": k, "上次时间": date, "参考时间": "2026-09-10", "周期": cycle}
                       for k, date, cycle in [("late", "2026-09-01", 7), ("ontime", "2026-09-01", 10), ("unknown", None, 7)]]}
    run = result(execute(client, administrator, flow, inputs))
    assert run["status"] == "completed", run
    rows = run["result"]["outputs"]["end"]["data"]
    assert {r["recordKey"]: r["结果"] for r in rows} == {"late": "预警", "ontime": "正常", "unknown": "待确认"}
    empty = result(execute(client, administrator, flow, {"data": []}))
    assert empty["status"] == "completed", empty
    assert empty["result"]["outputs"]["end"]["data"] == []


def dataset(client, headers, source, sql="SELECT id AS recordKey, amount, department FROM sales WHERE department=:department ORDER BY id"):
    return result(client.post("/api/v1/analytics/query-templates", headers=headers, json={
        "name": "读取销售记录", "mode": "sql", "content": sql,
        "parameters": {"workflowValues": True, "datasourceId": source["id"], "identityFields": ["recordKey"],
                       "columns": [{"name": "amount"}, {"name": "department"}],
                       "inputs": [{"name": "department", "default": "设备", "label": "部门"}]}}), 201)


def test_read_selected_values_and_pin_template_version(client, administrator, source):
    template = dataset(client, administrator, source)
    choices = result(client.get(API + "/value-sources", headers=administrator, params={"datasourceId": source["id"]}))
    assert choices[0]["id"] == template["id"] and "content" not in choices[0]
    assert choices[0]["deviceScoped"] is False
    assert choices[0]["sourceTables"] == [{"name": "sales", "label": "sales"}]
    flow = create_flow(client, administrator, graph(node("read", "read-values", {
        "datasourceId": source["id"], "templateId": template["id"], "templateVersion": template["version"], "fields": ["amount"]})))
    run = result(execute(client, administrator, flow))
    assert run["status"] == "completed", run
    output = run["result"]["outputs"]["end"]
    assert [r["amount"] for r in output["data"]] == [100, 300, 500]
    assert set(output["data"][0]) == {"recordKey", "amount"}
    assert "generatedSql" not in output
    filtered = result(execute(client, administrator, flow, {"department": "财务"}))
    assert len(filtered["result"]["outputs"]["end"]["data"]) == 2
    fixed_graph = flow["graph"]
    fixed_graph["nodes"][1]["config"].update(parameters={"department": "设备"}, parameterSources={"department": "fixed"})
    fixed = create_flow(client, administrator, fixed_graph)
    fixed_run = result(execute(client, administrator, fixed, {"department": "财务"}))
    assert len(fixed_run["result"]["outputs"]["end"]["data"]) == 3
    result(client.put("/api/v1/analytics/query-templates/" + template["id"], headers=administrator,
                      json={"name": template["name"] + "新版", "mode": "sql", "content": template["content"],
                            "parameters": {**template["parameters"], "columns": [{"name": "department"}]}, "version": template["version"]}))
    assert execute(client, administrator, flow).status_code == 422
    assert client.get(API + f"/{flow['id']}/runs/{run['id']}", headers=administrator).status_code == 200


def test_dataset_cannot_read_disallowed_table_or_truncated_batch(client, administrator, source):
    template = dataset(client, administrator, source, "SELECT private_note FROM restricted_data")
    flow = create_flow(client, administrator, graph(node("read", "read-values", {
        "datasourceId": source["id"], "templateId": template["id"], "templateVersion": template["version"]})))
    assert result(execute(client, administrator, flow), 502)["status"] == "failed"
    template = dataset(client, administrator, source)
    flow = create_flow(client, administrator, graph(node("read", "read-values", {
        "datasourceId": source["id"], "templateId": template["id"], "templateVersion": template["version"], "maxRows": 1})))
    run = result(execute(client, administrator, flow), 502)
    assert run["status"] == "failed" and "部分记录" in run["errorMessage"]


def test_ambiguous_dataset_records_stop_before_judgment(client, administrator, source):
    template = dataset(client, administrator, source, 'SELECT 1 AS recordKey, amount FROM sales WHERE department=:department')
    flow = create_flow(client, administrator, graph(node("read", "read-values", {
        "datasourceId": source["id"], "templateId": template["id"], "templateVersion": template["version"]})))
    run = result(execute(client, administrator, flow), 502)
    assert "关联重复" in run["errorMessage"]
    assert run["result"]["nodes"]["end"]["status"] == "skipped"
