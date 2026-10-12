"""Advanced migrated workflow nodes and durable execution controls."""
from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select

from app.models import ModelCall, Run, RunStep, ToolCall
from test_capabilities_center import http_payload
from test_collection import create_source, upstream
from test_workflows import API, create_flow, edge, execute, graph, node, result


def test_iteration_aggregation_loop_while_classifier_and_safe_text(client, administrator):
    iter_flow = create_flow(client, administrator, graph(
        node("iterate", "iterator", {"updates": [{"name": "double", "formula": "amount * 2"}]}),
        node("aggregate", "aggregator", {
            "groupBy": ["team"],
            "metrics": [
                {"name": "count", "operation": "count", "field": ""},
                {"name": "total", "operation": "sum", "field": "double"},
            ],
        }),
    ))
    output = result(execute(client, administrator, iter_flow, {
        "data": [
            {"team": "A", "amount": 2}, {"team": "A", "amount": 3},
            {"team": "B", "amount": 5},
        ],
    }))["result"]["outputs"]["end"]
    assert output["data"] == [
        {"team": "A", "count": 2, "total": "10"},
        {"team": "B", "count": 1, "total": "10"},
    ]

    loop_flow = create_flow(client, administrator, graph(
        node("repeat", "loop", {
            "iterations": 3, "updates": [{"name": "x", "formula": "x + 1"}],
            "keepHistory": True,
        }),
    ))
    output = result(execute(client, administrator, loop_flow, {"x": 1}))["result"]["outputs"]["end"]
    assert output["data"]["x"] == "4" and output["iterations"] == 3 and len(output["history"]) == 3

    while_flow = create_flow(client, administrator, graph(
        node("until", "while", {
            "expression": {"op": "lt", "args": [{"field": "x"}, {"value": 4}]},
            "updates": [{"name": "x", "formula": "x + 1"}], "maxIterations": 10,
        }),
        node("classify", "classifier", {
            "cases": [{
                "id": "ready", "label": "已达标",
                "expression": {"op": "eq", "args": [{"field": "iterations"}, {"value": 3}]},
            }],
            "fallback": "review",
        }),
    ))
    output = result(execute(client, administrator, while_flow, {"x": 1}))["result"]["outputs"]["end"]
    assert output["category"] == "ready" and output["matched"] is True

    text_flow = create_flow(client, administrator, graph(
        node("vars", "setvar", {
            "keepInput": False,
            "variables": [{"name": "product", "path": "$.input.name"}],
        }),
        node("render", "template", {"template": "{{ $.product }}系统"}),
        node("upper", "text", {"operation": "upper"}),
    ))
    assert result(execute(client, administrator, text_flow, {"name": "erp"}))["result"]["outputs"]["end"] == "ERP系统"


def test_question_wait_resume_is_idempotent_and_does_not_repeat_completed_nodes(client, administrator, db):
    flow = create_flow(client, administrator, graph(
        node("ask", "question", {"prompt": "请填写复核结论", "answerKey": "decision", "required": True}),
    ))
    waiting_response = execute(client, administrator, flow, {"sample": "S-001"})
    assert waiting_response.status_code == 202
    waiting = result(waiting_response, 202)
    assert waiting["status"] == "waiting"
    assert waiting["result"]["waiting"]["nodeId"] == "ask"
    assert waiting["result"]["nodes"]["start"]["status"] == "completed"

    control_key = "resume-" + uuid4().hex
    resumed = result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": control_key, "input": "通过"},
    ))
    assert resumed["status"] == "completed"
    assert resumed["result"]["outputs"]["end"] == {
        "input": {"sample": "S-001"}, "decision": "通过",
    }
    duplicate = result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": control_key, "input": "通过"},
    ))
    assert duplicate == resumed
    steps = db.scalars(select(RunStep).where(RunStep.run_id == resumed["runId"])).all()
    assert [step.name for step in steps].count("start") == 1
    assert [step.name for step in steps].count("ask") == 2
    assert all(step.status == "completed" for step in steps)


def test_waiting_execution_can_be_cancelled_once(client, administrator):
    flow = create_flow(client, administrator, graph(
        node("ask", "question", {"prompt": "等待审批", "answerKey": "approval"}),
    ))
    waiting = result(execute(client, administrator, flow, {"order": "O-1"}), 202)
    key = "cancel-" + uuid4().hex
    cancelled = result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/cancel", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": key},
    ))
    assert cancelled["status"] == "cancelled"
    assert cancelled["result"]["nodes"]["ask"]["status"] == "cancelled"
    assert result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/cancel", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": key},
    )) == cancelled


def test_llm_and_extractor_are_real_traced_calls_with_strict_json(client, administrator, db, monkeypatch):
    calls = []

    def fake_chat(profile, messages, tools=None, temperature=0.2, workspace_id=None):
        calls.append({"profile": profile, "messages": messages, "workspace": workspace_id})
        content = '{"invoice":"INV-1","amount":12.5}' if "结构化信息抽取器" in messages[0]["content"] else "已完成分析"
        return {
            "content": content, "tool_calls": [], "usage": {"total_tokens": 12},
            "model": "test-model", "provider": "test-provider",
        }

    monkeypatch.setattr("app.providers.model.chat_for_run", fake_chat)
    flow = create_flow(client, administrator, graph(
        node("model", "llm", {
            "modelProfile": "reasoning", "systemPrompt": "只分析输入",
            "promptTemplate": "分析 {{ $.name }}", "temperature": 0,
        }),
        node("extract", "extractor", {
            "modelProfile": "reasoning", "promptTemplate": "{{ $.content }}",
            "fields": [
                {"name": "invoice", "type": "string", "required": True},
                {"name": "amount", "type": "number", "required": True},
            ],
        }),
    ))
    execution = result(execute(client, administrator, flow, {"name": "ERP"}))
    assert execution["result"]["outputs"]["end"]["data"] == {"invoice": "INV-1", "amount": 12.5}
    assert len(calls) == 2
    recorded = db.scalars(select(ModelCall).where(ModelCall.run_id == execution["runId"])).all()
    assert len(recorded) == 2 and all(row.status == "completed" for row in recorded)


def test_ai_generation_hydrates_node_contracts_and_repairs_redundant_terminal_chain(
    client, administrator, monkeypatch,
):
    captured = {}

    def fake_chat(*args, **kwargs):
        captured["messages"] = args[1]
        return {
            "content": json.dumps({
                "name": "AI 财务分析",
                "description": "读取数据并计算指标",
                "domains": ["analysis"],
                "graph": {
                    "nodes": [
                        {"id": "start1", "kind": "start", "title": "开始"},
                        {"id": "query1", "kind": "database-query", "title": "查询", "config": {
                            "query": "SELECT amount FROM sales", "hallucinated": "discard-me",
                        }},
                        {"id": "calc1", "kind": "arithmetic", "title": "计算"},
                        {"id": "reply1", "kind": "reply", "title": "输出"},
                        {"id": "end1", "kind": "end", "title": "冗余结束"},
                    ],
                    "edges": [
                        {"id": "e1", "source": "start1", "target": "query1", "port": "out"},
                        {"id": "e2", "source": "query1", "target": "calc1", "port": "out"},
                        {"id": "e3", "source": "calc1", "target": "reply1", "port": "out"},
                        {"id": "e4", "source": "reply1", "target": "end1", "port": "out"},
                    ],
                },
            }, ensure_ascii=False),
            "usage": {"total_tokens": 10}, "model": "fixture-model", "provider": "fixture",
        }

    monkeypatch.setattr("app.providers.model.chat_for_run", fake_chat)
    generated = result(client.post(API + "/generate", headers=administrator, json={
        "name": "AI 财务分析", "domains": ["analysis"],
        "prompt": "读取销售数据，计算销售额并输出分析结果",
    }))

    nodes = {item["id"]: item for item in generated["workflow"]["graph"]["nodes"]}
    assert set(nodes) == {"start1", "query1", "calc1", "reply1"}
    assert nodes["query1"]["config"] == {
        "datasourceId": "", "datasourceVersion": None,
        "query": "SELECT amount FROM sales", "parameters": {}, "maxRows": 1000,
    }
    assert nodes["calc1"]["config"] == {"fields": [{"name": "", "formula": ""}]}
    assert all(edge["source"] != "reply1" for edge in generated["workflow"]["graph"]["edges"])
    assert generated["workflow"]["validation"]["nodeCount"] == 4
    assert {item["nodeId"] for item in generated["workflow"]["validation"]["errors"]} == {
        "query1", "calc1",
    }
    assert "defaultConfig" in captured["messages"][0]["content"]
    assert "禁止 reply→end" in captured["messages"][0]["content"]
    assert "不能用来分析上游节点结果" in captured["messages"][0]["content"]


def test_ai_generation_retries_malformed_output_and_repairs_live_failure_shapes(
    client, administrator, monkeypatch,
):
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            return {"content": "这里是流程，但不是 JSON", "usage": {"total_tokens": 3}, "model": "fixture", "provider": "fixture"}
        return {
            "content": "结果如下：" + json.dumps({
                "name": "豆瓣电影分析",
                "graph": {
                    "nodes": [
                        {"id": "start1", "kind": "start", "config": {}, "position": {"x": 0, "y": 0}},
                        {"id": "http1", "kind": "http-request", "config": {
                            "datasourceId": "invented-source", "datasourceVersion": None,
                        }, "position": {"x": 220, "y": 0}, "validation": {"errors": ["不应出现在节点内"]}},
                        {"id": "analysis1", "kind": "report-analysis", "config": {
                            "query": "分析电影评分与类型",
                        }, "position": {"x": 440, "y": 0}},
                        {"id": "reply1", "kind": "reply", "position": {"x": 660, "y": 0}},
                        {"id": "end1", "kind": "end", "position": {"x": 880, "y": 0}},
                    ],
                    "edges": [
                        {"id": "e1", "source": "start1", "target": "http1"},
                        {"id": "e2", "source": "http1", "target": "analysis1"},
                        {"id": "e3", "source": "analysis1", "target": "reply1"},
                        {"id": "e4", "source": "reply1", "target": "end1"},
                    ],
                },
            }, ensure_ascii=False) + "\n请查收",
            "usage": {"total_tokens": 7}, "model": "fixture", "provider": "fixture",
        }

    monkeypatch.setattr("app.providers.model.chat_for_run", fake_chat)
    generated = result(client.post(API + "/generate", headers=administrator, json={
        "name": "豆瓣电影分析", "domains": ["analysis"],
        "prompt": "通过 HTTP 请求采集豆瓣电影数据并分析评分与类型",
    }))
    nodes = {item["id"]: item for item in generated["workflow"]["graph"]["nodes"]}
    assert generated["generation"]["mode"] == "repaired"
    assert generated["generation"]["attempts"] == 2
    assert generated["usage"]["total_tokens"] == 10
    assert len(calls) == 2 and "上次输出未通过工作流契约" in calls[1][-1]["content"]
    assert nodes["start1"]["title"] == "开始"
    assert nodes["http1"]["title"] == "HTTP 采集"
    assert nodes["http1"]["config"]["datasourceId"] == ""
    assert "validation" not in nodes["http1"]
    assert nodes["analysis1"]["kind"] == "llm"
    assert "分析电影评分与类型" in nodes["analysis1"]["config"]["promptTemplate"]
    assert "end1" not in nodes
    positions = [item["position"]["x"] for item in generated["workflow"]["graph"]["nodes"]]
    assert positions == [80, 400, 720, 1040]
    assert all(item["sourceSide"] == "right" and item["targetSide"] == "left"
               for item in generated["workflow"]["graph"]["edges"])
    assert {item["nodeId"] for item in generated["workflow"]["validation"]["errors"]} == {"http1"}


def test_ai_generation_returns_editable_fallback_when_provider_is_unavailable(
    client, administrator, db, monkeypatch,
):
    monkeypatch.setattr("app.providers.model.chat_for_run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    generated = result(client.post(API + "/generate", headers=administrator, json={
        "name": "豆瓣电影分析", "domains": ["analysis"],
        "prompt": "通过 HTTP 请求采集豆瓣电影数据并进行分析",
    }))
    assert generated["generation"] == {
        "mode": "fallback", "attempts": 2,
        "warnings": [
            "模型连续两次未返回可用流程；系统已保留业务目标并生成安全的可编辑草稿。",
            "草稿还有 1 项资源或节点属性需要确认，已定位到第一个待配置节点。",
        ],
    }
    assert [node["kind"] for node in generated["workflow"]["graph"]["nodes"]] == [
        "start", "http-request", "llm", "reply",
    ]
    assert generated["workflow"]["validation"]["errors"][0]["nodeId"] == "collect"
    run = db.get(Run, generated["runId"])
    calls = db.scalars(select(ModelCall).where(ModelCall.run_id == generated["runId"])).all()
    assert run.status == "completed" and len(calls) == 2
    assert all(call.status == "failed" and call.error_code == "RuntimeError" for call in calls)


def test_ai_generation_only_binds_explicit_selected_source(
    client, administrator, upstream, monkeypatch,
):
    source, _ = create_source(
        client, administrator, upstream, name="豆瓣电影接口",
        allowAutomatic=True, fields=[{"name": "标题", "path": "$.title", "comment": "电影名称"}],
    )
    captured = {}

    def fake_chat(*args, **kwargs):
        captured["system"] = args[1][0]["content"]
        return {
            "content": json.dumps({
                "graph": {
                    "nodes": [
                        {"id": "start", "kind": "start", "title": "开始"},
                        {"id": "collect", "kind": "http-request", "title": "采集豆瓣电影", "config": {
                            "datasourceId": "invented-source",
                        }},
                        {"id": "reply", "kind": "reply", "title": "输出"},
                    ],
                    "edges": [
                        {"id": "e1", "source": "start", "target": "collect"},
                        {"id": "e2", "source": "collect", "target": "reply"},
                    ],
                },
            }, ensure_ascii=False),
            "usage": {}, "model": "fixture", "provider": "fixture",
        }

    monkeypatch.setattr("app.providers.model.chat_for_run", fake_chat)
    generated = result(client.post(API + "/generate", headers=administrator, json={
        "name": "豆瓣电影分析", "prompt": "采集豆瓣电影接口并输出结果",
        "resourceIds": [source["id"]],
    }))
    node_config = next(node["config"] for node in generated["workflow"]["graph"]["nodes"] if node["kind"] == "http-request")
    assert node_config['datasourceId'] == source['id'] and node_config['datasourceVersion'] == source['version']
    assert node_config.get('url') is None and node_config.get('method') is None
    assert generated["workflow"]["validation"]["valid"]
    assert source["id"] in captured["system"] and "豆瓣电影接口" in captured["system"]
    assert "电影名称" in captured["system"]


def test_loop_tool_calls_keep_distinct_invocations_and_resume_once(client, administrator, db):
    """相同参数的循环工具按轮次独立调用；恢复时保留已完成轮次的回执。"""
    body = graph(node("clock", "tool", {
        "toolName": "current_datetime", "arguments": {}, "includeInput": False,
    }))
    flow = create_flow(client, administrator, graph(
        node("first", "loop", {"iterations": 2, "subgraph": body}),
        node("ask", "question", {"prompt": "继续核验", "answerKey": "decision", "required": True}),
        node("second", "loop", {"iterations": 2, "subgraph": body}),
    ))
    waiting = result(execute(client, administrator, flow), 202)
    assert waiting["status"] == "waiting"
    calls_before = db.scalars(select(ToolCall).where(ToolCall.run_id == waiting["runId"])).all()
    assert len(calls_before) == 2
    prior_ids = {call.id for call in calls_before}
    resumed = result(client.post(
        API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
        json={"version": waiting["version"], "idempotencyKey": uuid4().hex, "input": "通过"},
    ))
    assert resumed["status"] == "completed"
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == waiting["runId"])).all()
    assert len(calls) == 4
    assert prior_ids <= {call.id for call in calls}
    assert len({call.idempotency_key for call in calls}) == 4
    assert all(call.status == "completed" for call in calls)


def test_gateway_tool_code_parallel_and_published_subflow(client, administrator):
    tool_flow = create_flow(client, administrator, graph(
        node("clock", "tool", {"toolName": "current_datetime", "arguments": {}, "includeInput": False}),
    ))
    output = result(execute(client, administrator, tool_flow))["result"]["outputs"]["end"]
    assert output["status"] == "success" and output["toolName"] == "current_datetime"

    code_flow = create_flow(client, administrator, graph(
        node("code", "code", {"code": "result = {'total': 2 + 3}", "timeoutSeconds": 30}),
    ))
    output = result(execute(client, administrator, code_flow))["result"]["outputs"]["end"]
    assert output["data"]["result"] == {"total": 5}

    input_flow = create_flow(client, administrator, graph(
        node("code", "code", {"code": "def main(input):\n    return {'total': sum(input['values']), 'label': input['label']}", "timeoutSeconds": 30}),
    ))
    output = result(execute(client, administrator, input_flow, {"values": [3, 8], "label": "上游中文"}))["result"]["outputs"]["end"]
    assert output["data"]["result"] == {"total": 11, "label": "上游中文"}

    child = create_flow(client, administrator, graph())
    child = result(client.post(API + f"/{child['id']}/publish", headers=administrator, json={"version": child["version"]}))
    parent = create_flow(client, administrator, graph(
        node("fanout", "parallel", {"branchNames": ["设备", "财务"]}),
        node("child", "subflow", {
            "workflowId": child["id"], "workflowVersion": child["version"],
            "timeoutSeconds": 60, "confirmWrites": False,
        }),
    ))
    output = result(execute(client, administrator, parent, {"value": 7}))["result"]["outputs"]["end"]
    assert output["childExecutionId"] and output["outputs"]["end"]["value"] == 7


def test_subflow_history_survives_child_update_but_execution_and_permissions_stay_checked(client, administrator, monkeypatch):
    child = create_flow(client, administrator, graph())
    child = result(client.post(API + f"/{child['id']}/publish", headers=administrator, json={"version": child["version"]}))
    parent = create_flow(client, administrator, graph(node("child", "subflow", {
        "workflowId": child["id"], "workflowVersion": child["version"], "timeoutSeconds": 60,
    })))
    run = result(execute(client, administrator, parent, {"value": 7}))
    changed = result(client.put(API + f"/{child['id']}", headers=administrator, json={
        "version": child["version"], "name": "更新后的子流程", "domains": child["domains"], "graph": child["graph"],
    }))
    result(client.post(API + f"/{child['id']}/publish", headers=administrator, json={"version": changed["version"]}))
    detail_url = API + f"/{parent['id']}/runs/{run['id']}"
    history = result(client.get(detail_url, headers=administrator))
    assert history["result"]["outputs"]["end"]["outputs"]["end"]["value"] == 7
    assert any(item["id"] == run["id"] for item in result(client.get(API + f"/{parent['id']}/runs", headers=administrator)))
    assert client.get(API + f"/{parent['id']}/monitor", headers=administrator).status_code == 200
    stale_run = execute(client, administrator, parent)
    assert stale_run.status_code == 422
    assert "子流程版本已变化" in stale_run.text

    from app.errors import PermissionErrorEx
    from app.modules.workflows import graph as graph_module
    original = graph_module.owned_record

    def denied_child(ctx, model, record_id, permission, **kwargs):
        if record_id == child["id"]:
            raise PermissionErrorEx("已撤销子流程访问权限")
        return original(ctx, model, record_id, permission, **kwargs)

    monkeypatch.setattr(graph_module, "owned_record", denied_child)
    assert client.get(detail_url, headers=administrator).status_code == 403


def test_workflow_tool_node_runs_admin_approved_private_http_method(client, administrator, upstream):
    payload = http_payload(name="工作流局域网接口", tool_name="workflow.private_status")
    payload["config"].update({
        "baseUrl": upstream[0],
        "path": "/records",
        "method": "GET",
        "bodyMode": "query",
        "allowPrivate": True,
    })
    payload["tools"][0]["permissions"] = []
    connector = result(client.post(
        "/api/v1/capabilities/connectors", headers=administrator, json=payload,
    ), 201)
    result(client.post(
        f"/api/v1/capabilities/connectors/{connector['id']}/publish",
        headers=administrator,
        json={"version": 1},
    ))

    flow = create_flow(client, administrator, graph(
        node("private-http", "tool", {
            "toolName": "workflow.private_status",
            "arguments": {"deviceCode": "WF-LAN-001"},
            "includeInput": False,
        }),
    ))
    output = result(execute(client, administrator, flow))["result"]["outputs"]["end"]
    assert output["status"] == "success"
    assert output["data"]["items"][0]["device"] == "A"
    assert upstream[1][-1]["path"] == "/records?deviceCode=WF-LAN-001"
