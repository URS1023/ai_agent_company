"""Executable contract matrix for every node exposed by the workflow palette.

The focused business suites still prove real HTTP, SQL, alert, quality and
writeback behavior.  This file adds the cross-cutting guarantee that every
catalog entry has a valid contract, rejects undeclared properties, reaches an
executor branch, and fails early for configurations that would silently lose
customer data.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError as ContractError

from app.errors import ValidationError
from app.modules.analytics import queries
from app.modules.collection import execution as collection
from app.modules.workflows import nodes as workflow_nodes
from app.modules.workflows.catalog import CONFIGS, NODES, editable_config, hydrate_editable_config
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import check_config
from app.modules.workflows.nodes import NO_RESUME, WorkflowPause, execute_node
from app.modules.workflows.schemas import FlowNode
from test_analytics import source
from test_workflows import API, create_flow, execute, graph, node, result


RESOURCE_ID = "A" * 26


VALID_CONFIGS = {
    "read-values": {"datasourceId": "source", "tableName": "measurements", "fields": ["value"]},
    "calculate-values": {"calculations": [{"name": "copied", "operation": "copy", "left": {"source": "constant", "value": 1}}]},
    "output-values": {"mode": "alert", "fields": ["value"], "conclusion": "normal"},
    "business-report": {"kind": "alert", "title": "真实检查结果", "aiAnalysis": False},
    "business-notify": {"audience": "executor"},
    "document-extractor": {},
    "list-operator": {},
    "start": {},
    "end": {},
    "reply": {},
    "merge": {},
    "http-request": {"datasourceId": RESOURCE_ID, "datasourceVersion": 1},
    "database-query": {
        "datasourceId": RESOURCE_ID, "datasourceVersion": 1,
        "query": "SELECT 1 AS value", "parameters": {}, "maxRows": 100,
    },
    "report-analysis": {
        "datasourceId": RESOURCE_ID, "datasourceVersion": 1,
        "query": "统计销售额", "parameters": {}, "maxRows": 100,
    },
    "jsonpath-extract": {
        "recordPath": "$.data", "fields": [
            {"name": "value", "path": "$.value", "type": "decimal", "required": True},
        ],
    },
    "arithmetic": {"fields": [{"name": "double", "formula": "value * 2"}]},
    "condition": {"expression": {"op": "gte", "args": [{"field": "value"}, {"value": 10}]}},
    "filter": {"expression": {"op": "gte", "args": [{"field": "value"}, {"value": 10}]}},
    "set-operation": {"operation": "intersection", "keys": ["id"]},
    "rule-evaluate": {
        "mode": "alert", "rules": [{
            "id": "high", "name": "数值超限", "severity": "warning", "message": "请复核",
            "expression": {"op": "gte", "args": [{"field": "value"}, {"value": 10}]},
        }],
    },
    "alert-evaluate": {"ruleId": RESOURCE_ID, "ruleRevision": 1},
    "alert-notify": {"audience": "executor", "outcomes": ["issues"], "minimumSeverity": "warning"},
    "qc-appearance": {
        "metricId": RESOURCE_ID, "metricVersion": 1, "deviceId": RESOURCE_ID,
        "datasourceId": RESOURCE_ID, "datasourceVersion": 1, "requestMode": "saved",
        "sampleField": "sampleId", "imageField": "imageFileId",
    },
    "qc-component": {
        "metricId": RESOURCE_ID, "metricVersion": 1, "deviceId": RESOURCE_ID,
        "datasourceId": RESOURCE_ID, "datasourceVersion": 1, "requestMode": "saved",
        "sampleField": "sampleId", "imageField": "imageFileId",
    },
    "qc-evaluate": {
        "templateId": RESOURCE_ID, "templateRevision": 1, "deviceId": RESOURCE_ID,
        "sampleId": "LOT-001", "sampleMode": "fixed",
    },
    "output-table": {
        "targetId": RESOURCE_ID, "targetVersion": 1, "mode": "insert",
        "recordPath": "$.data", "fields": [
            {"column": "value", "origin": "row", "path": "$.value"},
        ], "keys": [],
    },
    "llm": {
        "modelProfile": "reasoning", "systemPrompt": "只依据输入",
        "promptTemplate": "分析 {{ $.value }}", "temperature": 0,
    },
    "agent": {
        "botId": RESOURCE_ID, "mode": "retrieval", "usePublished": False,
        "queryTemplate": "查询 {{ $.value }}",
    },
    "knowledge": {
        "knowledgeBaseIds": [RESOURCE_ID], "queryTemplate": "检索 {{ $.value }}",
        "topK": 5, "minScore": 0.2, "mode": "hybrid",
    },
    "question": {"prompt": "请确认", "answerKey": "decision", "required": True},
    "classifier": {
        "cases": [{
            "id": "ready", "label": "已达标",
            "expression": {"op": "gte", "args": [{"field": "value"}, {"value": 10}]},
        }], "fallback": "review",
    },
    "extractor": {
        "modelProfile": "reasoning", "systemPrompt": "只抽取输入",
        "promptTemplate": "{{ input }}", "temperature": 0,
        "fields": [{"name": "value", "type": "number", "description": "数值", "required": True}],
    },
    "loop": {"iterations": 2, "updates": [{"name": "value", "formula": "value + 1"}], "keepHistory": True},
    "iterator": {"updates": [{"name": "double", "formula": "value * 2"}]},
    "while": {
        "expression": {"op": "lt", "args": [{"field": "value"}, {"value": 3}]},
        "updates": [{"name": "value", "formula": "value + 1"}], "maxIterations": 5,
    },
    "parallel": {"branchNames": ["设备", "财务"]},
    "aggregator": {
        "groupBy": ["team"], "metrics": [
            {"name": "count", "operation": "count", "field": ""},
            {"name": "total", "operation": "sum", "field": "value"},
        ],
    },
    "delay": {"seconds": 1},
    "subflow": {
        "workflowId": RESOURCE_ID, "workflowVersion": 1,
        "timeoutSeconds": 60, "confirmWrites": False,
    },
    "setvar": {
        "keepInput": True, "variables": [
            {"name": "copied", "path": "$.input.value"},
            {"name": "fixed", "value": {"approved": True}},
        ],
    },
    "text": {"operation": "replace", "find": "ERP", "replacement": "MES", "separator": ","},
    "template": {"template": "数值：{{ $.value }}"},
    "code": {"code": "result = {'ok': True}", "timeoutSeconds": 30},
    "tool": {"toolName": "current_datetime", "arguments": {}, "includeInput": False},
}


def make_node(kind: str, config: dict | None = None) -> FlowNode:
    return FlowNode(id="tested", title=NODES[kind][0], kind=kind, config=config if config is not None else VALID_CONFIGS[kind])


def budget() -> RuleBudget:
    return RuleBudget(time.monotonic() + 5)


@pytest.mark.parametrize("kind", sorted(VALID_CONFIGS))
def test_every_catalog_node_has_a_complete_strict_contract(kind):
    assert set(VALID_CONFIGS) == set(NODES) == set(CONFIGS)
    check_config(make_node(kind))
    with pytest.raises(ContractError):
        check_config(make_node(kind, {**VALID_CONFIGS[kind], "undeclaredProperty": True}))


@pytest.mark.parametrize("kind", sorted(VALID_CONFIGS))
def test_editable_defaults_are_isolated_and_hydration_cannot_keep_model_hallucinations(kind):
    first = editable_config(kind)
    second = editable_config(kind)
    assert first == second and first is not second
    hydrated = hydrate_editable_config(kind, {"undeclaredProperty": "discard", **VALID_CONFIGS[kind]})
    assert "undeclaredProperty" not in hydrated
    assert set(hydrated) <= set(CONFIGS[kind].model_json_schema(by_alias=True)["properties"])


@pytest.mark.parametrize("kind", ["condition", "filter", "rule-evaluate", "classifier", "while"])
def test_hydration_preserves_heterogeneous_expression_arguments(kind):
    hydrated = hydrate_editable_config(kind, VALID_CONFIGS[kind])
    check_config(make_node(kind, hydrated))

    def assert_expression(expression):
        if "args" not in expression:
            return
        for argument in expression["args"]:
            assert not ({"field", "value"} <= set(argument))
            assert_expression(argument)

    if kind in {"condition", "filter", "while"}:
        assert_expression(hydrated["expression"])
    elif kind == "rule-evaluate":
        for rule in hydrated["rules"]:
            assert_expression(rule["expression"])
    else:
        for case in hydrated["cases"]:
            assert_expression(case["expression"])


@pytest.mark.parametrize("kind,config", [
    ("set-operation", {"operation": "union", "keys": ["id", "id"]}),
    ("aggregator", {"groupBy": [], "metrics": [
        {"name": "total", "operation": "sum", "field": "value"},
        {"name": "total", "operation": "max", "field": "value"},
    ]}),
    ("aggregator", {"groupBy": [], "metrics": [{"name": "total", "operation": "sum", "field": ""}]}),
    ("loop", {"iterations": 2, "updates": [
        {"name": "value", "formula": "value + 1"},
        {"name": "value", "formula": "value + 1"},
    ]}),
    ("iterator", {"updates": [
        {"name": "value", "formula": "value + 1"},
        {"name": "value", "formula": "value + 2"},
    ]}),
    ("while", {
        "expression": {"value": True}, "maxIterations": 2,
        "updates": [
            {"name": "value", "formula": "value + 1"},
            {"name": "value", "formula": "value + 2"},
        ],
    }),
    ("parallel", {"branchNames": ["设备", "设备"]}),
    ("parallel", {"branchNames": [""]}),
    ("text", {"operation": "replace", "find": "", "replacement": "x", "separator": ","}),
    ("text", {"operation": "split", "find": "", "replacement": "", "separator": ""}),
    ("template", {"template": "{{ customer.name }}"}),
    ("knowledge", {**VALID_CONFIGS["knowledge"], "queryTemplate": "{{ items }}"}),
    ("agent", {**VALID_CONFIGS["agent"], "queryTemplate": "{{ missing }}"}),
    ("llm", {**VALID_CONFIGS["llm"], "promptTemplate": "{{ $.missing[ }}"}),
])
def test_configuration_blocks_silent_overwrite_and_runtime_only_failures(kind, config):
    with pytest.raises((ContractError, ValidationError, ValueError)):
        check_config(make_node(kind, config))


@pytest.mark.parametrize("operation,value,expected", [
    ("stringify", {"b": 2, "a": 1}, '{"b":2,"a":1}'),
    ("trim", "  ERP  ", "ERP"),
    ("upper", "erp", "ERP"),
    ("lower", "ERP", "erp"),
    ("replace", "ERP 系统", "MES 系统"),
    ("split", "A,B,C", ["A", "B", "C"]),
    ("join", ["A", "B"], "A,B"),
])
def test_all_text_operations_execute(operation, value, expected):
    config = {"operation": operation, "find": "ERP", "replacement": "MES", "separator": ","}
    assert execute_node(None, make_node("text", config), {"input": value}, budget()) == expected


def test_pure_node_paths_execute_with_empty_and_nonempty_business_data():
    cases = [
        ("start", {"value": 2}, {"value": 2}),
        ("end", {"input": {"value": 2}}, {"value": 2}),
        ("reply", {"answer": "ok", "evidence": [1]}, {"answer": "ok", "evidence": [1]}),
        ("jsonpath-extract", {"input": {"data": [{"value": "12.50"}]}}, {"data": [{"value": "12.50"}]}),
        ("arithmetic", {"input": {"data": [{"value": "2.5"}]}}, {"data": [{"value": "2.5", "double": "5.0"}]}),
        ("filter", {"input": {"data": [{"value": 9}, {"value": 10}]}}, {"data": [{"value": 10}]}),
        ("condition", {"input": {"value": 10}}, {"matched": True, "data": {"value": 10}}),
        ("set-operation", {"left": [{"id": "A"}, {"id": "B"}], "right": [{"id": "B"}]}, {"data": [{"id": "B"}]}),
        ("rule-evaluate", {"input": {"data": [{"value": 11}]}}, {"passed": False}),
        ("merge", {"left": {"data": [{"id": "A"}]}, "right": {"data": []}}, {"data": [{"id": "A"}]}),
        ("classifier", {"input": {"value": 10}}, {"category": "ready", "matched": True}),
        ("loop", {"input": {"value": 1}}, {"data": {"value": "3"}, "iterations": 2}),
        ("iterator", {"input": {"data": [{"value": 2}, {"value": 3}]}}, {"data": [{"value": 2, "_index": 0, "double": "4"}, {"value": 3, "_index": 1, "double": "6"}]}),
        ("while", {"input": {"value": 1}}, {"data": {"value": "3"}, "iterations": 2, "terminated": True}),
        ("parallel", {"input": {"value": 2}}, {"value": 2}),
        ("aggregator", {"input": {"data": [{"team": "A", "value": 2}, {"team": "A", "value": 3}]}}, {"data": [{"team": "A", "count": 2, "total": "5"}]}),
        ("setvar", {"input": {"value": 2}}, {"value": 2, "copied": 2, "fixed": {"approved": True}}),
        ("template", {"input": {"value": 2}}, "数值：2"),
    ]
    for kind, inputs, expected in cases:
        output = execute_node(None, make_node(kind), inputs, budget())
        if isinstance(expected, dict):
            for key, value in expected.items():
                assert output[key] == value, kind
        else:
            assert output == expected, kind

    empty_aggregate = execute_node(None, make_node("aggregator"), {"input": {"data": []}}, budget())
    assert empty_aggregate["data"] == []
    empty_rules = execute_node(None, make_node("rule-evaluate"), {"input": {"data": []}}, budget())
    assert empty_rules["passed"] is None and empty_rules["summary"]["status"] == "no_data"


def test_question_and_delay_have_explicit_wait_resume_and_invalid_resume_cases():
    question = make_node("question")
    with pytest.raises(WorkflowPause) as paused:
        execute_node(None, question, {"input": {"sample": "S-1"}}, budget())
    assert paused.value.kind == "question" and paused.value.payload["answerKey"] == "decision"
    with pytest.raises(ValidationError):
        execute_node(None, question, {"input": {}}, budget(), resume_value="  ")
    assert execute_node(None, question, {"input": {"sample": "S-1"}}, budget(), resume_value="通过") == {
        "input": {"sample": "S-1"}, "decision": "通过",
    }

    delay = make_node("delay")
    with pytest.raises(WorkflowPause) as delayed:
        execute_node(None, delay, {"input": {"sample": "S-1"}}, budget())
    assert delayed.value.kind == "delay"
    resumed = execute_node(
        None, delay, {"input": {"sample": "S-1"}}, budget(),
        wait_context={"resumeAt": (datetime.utcnow() - timedelta(seconds=1)).isoformat()},
    )
    assert resumed["input"] == {"sample": "S-1"} and resumed["delayedSeconds"] == 1


def test_every_external_node_dispatches_only_to_its_real_service(monkeypatch):
    calls: list[tuple[str, object]] = []
    ctx = SimpleNamespace(workspace_id=RESOURCE_ID, user_id=RESOURCE_ID, db=object())

    class Result:
        success = True
        truncated = False
        queryId = "query-1"
        runId = "run-1"

        def __init__(self, marker):
            self.marker = marker

        def model_dump(self, **_kwargs):
            return {"service": self.marker, "data": [], "truncated": False}

    monkeypatch.setattr(collection, "collect", lambda *_args, **_kwargs: Result("http-request"))
    # HTTP authorization/override behavior has dedicated real-source tests;
    # this dispatch test uses a context without a database.
    monkeypatch.setattr(workflow_nodes, "_execute_http", lambda *_args, **_kwargs: Result("http-request").model_dump())
    monkeypatch.setattr(
        queries, "execute_query",
        lambda _ctx, payload, **_kwargs: Result("database-query" if payload.mode == "sql" else "report-analysis"),
    )
    monkeypatch.setattr(
        workflow_nodes, "_model_output",
        lambda _ctx, _config, _value, _budget, **kwargs: {"service": "extractor" if kwargs.get("extraction") else "llm"},
    )
    monkeypatch.setattr(
        workflow_nodes, "_execute_tool",
        lambda _ctx, _config, _inputs, **kwargs: {"service": "code" if kwargs.get("force_name") else "tool"},
    )

    from app.modules.alerts import notifications, reports
    from app.modules.knowledge import service as knowledge_service
    from app.modules.knowledge_graph import bots
    from app.modules.quality import detection, inspections
    from app.modules.workflows import execution as workflow_execution, service as workflow_service
    from app.modules.writeback import execution as write_execution

    monkeypatch.setattr(reports, "evaluate_alert", lambda *_args, **_kwargs: {"service": "alert-evaluate"})
    monkeypatch.setattr(notifications, "notify_alert", lambda *_args, **_kwargs: {"service": "alert-notify"})
    monkeypatch.setattr(
        detection, "detect",
        lambda _ctx, kind, *_args, **_kwargs: {"service": kind},
    )
    monkeypatch.setattr(inspections, "evaluate_node", lambda *_args, **_kwargs: {"service": "qc-evaluate"})
    monkeypatch.setattr(write_execution, "execute_write", lambda *_args, **_kwargs: {"service": "output-table"})
    monkeypatch.setattr(
        knowledge_service, "search",
        lambda *_args, **_kwargs: [{"content": "真实知识证据"}],
    )
    monkeypatch.setattr(bots, "debug_bot", lambda *_args, **_kwargs: {"service": "agent"})
    monkeypatch.setattr(workflow_service, "get_workflow", lambda *_args, **_kwargs: SimpleNamespace(id=RESOURCE_ID))
    monkeypatch.setattr(
        workflow_execution, "start",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="completed", id="child-execution", runId="child-run", revision=1,
            result={"outputs": {"end": {"ok": True}}},
        ),
    )

    external = [
        "http-request", "database-query", "report-analysis", "llm", "extractor", "agent", "knowledge",
        "tool", "code", "subflow", "output-table", "alert-evaluate", "alert-notify",
        "qc-appearance", "qc-component", "qc-evaluate",
    ]
    for kind in external:
        output = execute_node(
            ctx, make_node(kind), {"input": {"value": 12, "data": []}}, budget(),
            execution_id="execution-1", run_id="run-1", step_id="step-1",
        )
        marker = (
            output.get("service")
            or ("knowledge" if output.get("results") else None)
            or ("subflow" if output.get("childExecutionId") else None)
        )
        assert marker == kind, (kind, output)
        calls.append((kind, output))
    assert [kind for kind, _output in calls] == external


def test_report_analysis_runs_natural_language_against_the_selected_real_database(
    client, administrator, source, monkeypatch,
):
    from app.config import settings
    from app.providers import model

    # 显式设置夹具预算，校验节点传递配置；不依赖部署环境或默认时限。
    monkeypatch.setattr(settings, "ANALYTICS_NL_REQUEST_TIMEOUT_SECONDS", 37)
    observed = []

    def natural_language_sql(_profile, _messages, **kwargs):
        observed.append(kwargs)
        return {
            "content": '{"sql":"SELECT SUM(amount) AS total FROM sales","explanation":"销售额合计"}',
            "model": "workflow-matrix", "usage": {"total_tokens": 8},
        }

    monkeypatch.setattr(model, "chat_for_run", natural_language_sql)
    flow = create_flow(client, administrator, graph(node("analysis", "report-analysis", {
        "datasourceId": source["id"], "datasourceVersion": source["version"],
        "query": "统计销售总额", "parameters": {}, "maxRows": 100,
    })))
    output = result(execute(client, administrator, flow))["result"]["outputs"]["end"]
    assert output["data"] == [{"total": 1100}]
    assert output["queryType"] == "natural_language" and output["generatedSql"].startswith("SELECT")
    assert observed == [{"temperature": 0, "workspace_id": administrator["X-Workspace-Id"],
                         "request_timeout": 37, "max_retries": 0}]


def test_knowledge_and_agent_nodes_use_selected_authorized_resources(
    client, administrator, monkeypatch,
):
    from test_knowledge_governance import _kb, _offline_knowledge

    _offline_knowledge(monkeypatch)
    knowledge_base = _kb(client, administrator, name="工作流设备知识")
    segment = client.post(
        f"/api/knowledge/{knowledge_base['id']}/segments", headers=administrator,
        json={"segment_type": "text", "text": "振动超限时应立即停机并复核传感器。", "enabled": True},
    )
    assert segment.status_code == 201, segment.text

    knowledge_flow = create_flow(client, administrator, graph(node("search", "knowledge", {
        "knowledgeBaseIds": [knowledge_base["id"]], "queryTemplate": "{{ $.question }}",
        "topK": 5, "minScore": 0, "mode": "keyword",
    })))
    output = result(execute(client, administrator, knowledge_flow, {"question": "振动超限"}))["result"]["outputs"]["end"]
    assert output["query"] == "振动超限" and output["totalCount"] == 1
    assert "立即停机" in output["results"][0]["content"]

    bot_response = client.post("/api/v1/bots", headers=administrator, json={
        "name": "工作流设备助手", "botType": "knowledge", "instructions": "仅依据知识证据回答",
        "knowledgeBaseIds": [knowledge_base["id"]], "modelProfile": "chat",
    })
    assert bot_response.status_code == 201, bot_response.text
    bot = bot_response.json()["data"]
    agent_flow = create_flow(client, administrator, graph(node("agent", "agent", {
        "botId": bot["id"], "mode": "retrieval", "usePublished": False,
        "queryTemplate": "{{ $.question }}",
    })))
    output = result(execute(client, administrator, agent_flow, {"question": "振动超限怎么办"}))["result"]["outputs"]["end"]
    assert output["status"] == "completed" and output["references"]
    assert "立即停机" in output["answer"]


def test_delay_node_persists_wait_rejects_early_resume_then_completes_without_restarting_upstream(
    client, administrator, db,
):
    from sqlalchemy import select
    from app.models import RunStep

    flow = create_flow(client, administrator, graph(node("delay", "delay", {"seconds": 1})))
    waiting = result(execute(client, administrator, flow, {"ticket": "T-1"}), 202)
    assert waiting["status"] == "waiting" and waiting["result"]["waiting"]["kind"] == "delay"
    early = client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": uuid4().hex, "input": None},
    )
    assert early.status_code == 409 and "延迟尚未到期" in early.text
    time.sleep(1.05)
    completed = result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": uuid4().hex, "input": None},
    ))
    assert completed["status"] == "completed"
    assert completed["result"]["outputs"]["end"]["input"] == {"ticket": "T-1"}
    steps = db.scalars(select(RunStep).where(RunStep.run_id == completed["runId"])).all()
    assert [step.name for step in steps].count("start") == 1
    assert [step.name for step in steps].count("delay") == 2
