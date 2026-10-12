"""Actual in-memory graph execution with only external side effects replaced.

中文说明：真实执行内存中的工作流图，只替换对外副作用（数据库/模型）。
隔离方式：SimpleNamespace + monkeypatch，图执行在内存完成。
边界：容器/循环类节点的边界（空集合、重复项）必须显式处理。
"""
import time
from types import SimpleNamespace

import pytest

from app.errors import SchemaValidationError, ToolExecutionError, ValidationError
from app.modules.workflows import nodes
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.schemas import FlowGraph, FlowNode, LoopConfig, WhileConfig


def graph_for(middle=None, *, extra_nodes=(), extra_edges=()):
    middle = middle or {"id": "operation", "kind": "tool", "title": "操作", "config": {"toolName": "offline_test"}}
    return FlowGraph.model_validate({"nodes": [
        {"id": "start", "kind": "start", "title": "开始"}, middle,
        {"id": "end", "kind": "end", "title": "结束"}, *extra_nodes,
    ], "edges": [
        {"id": "first", "source": "start", "target": middle["id"]},
        {"id": "last", "source": middle["id"], "target": "end"}, *extra_edges,
    ]})


def test_each_iteration_and_container_has_distinct_stable_side_effect_identity(monkeypatch):
    real_execute = nodes.execute_node
    calls = []
    def execute(ctx, node, inputs, budget, **kwargs):
        if node.kind == "tool":
            calls.append((kwargs.get("execution_id"), node.id, inputs["input"]["_index"]))
            return dict(inputs["input"])
        return real_execute(ctx, node, inputs, budget, **kwargs)
    monkeypatch.setattr(nodes, "execute_node", execute)
    graph = graph_for()
    loop = FlowNode(id="container", kind="loop", title="循环", config={"iterations": 2, "subgraph": graph.model_dump(by_alias=True)})
    result = real_execute(None, loop, {"input": {"x": 1}}, RuleBudget(time.monotonic() + 3), execution_id="real_execution")
    assert result["iterations"] == 2
    assert calls[0][0] == calls[1][0] == "real_execution"
    assert calls[0][1] != calls[1][1] and [item[2] for item in calls] == [0, 1]
    first_ids = [item[1] for item in calls]
    real_execute(None, loop, {"input": {"x": 1}}, RuleBudget(time.monotonic() + 3), execution_id="real_execution")
    assert [item[1] for item in calls[2:]] == first_ids
    loop.id = "other_container"
    real_execute(None, loop, {"input": {"x": 1}}, RuleBudget(time.monotonic() + 3), execution_id="real_execution")
    assert not set(first_ids) & {item[1] for item in calls[4:]}


def test_container_retry_and_default_value_policies_are_honored(monkeypatch):
    real_execute, calls = nodes.execute_node, []
    def execute(ctx, node, inputs, budget, **kwargs):
        if node.kind == "arithmetic":
            calls.append(node.id)
            raise ToolExecutionError("offline failure")
        return real_execute(ctx, node, inputs, budget, **kwargs)
    monkeypatch.setattr(nodes, "execute_node", execute)
    monkeypatch.setattr(nodes.time, "sleep", lambda _: None)
    graph = graph_for({"id": "operation", "kind": "arithmetic", "title": "操作",
                       "config": {"fields": [{"name": "result", "formula": "1"}]},
                       "retryConfig": {"retryEnabled": True, "maxRetries": 1, "retryInterval": 100},
                       "errorStrategy": "default-value", "defaultValue": {"fallback": 0}})
    output, trace = nodes._execute_container_subgraph(None, graph, {}, RuleBudget(time.monotonic() + 3),
                                                     execution_id="run", run_id=None, parent_node_id="container")
    assert len(calls) == 2 and calls[0] == calls[1]
    assert output == {"fallback": 0}
    assert trace["nodes"]["operation"]["handled"] is True
    assert trace["nodes"]["operation"]["retryCount"] == 1


def test_retry_policy_blocks_side_effects_and_validation_but_keeps_pure_nodes():
    from app.modules.workflows.nodes import should_retry_node

    safe = FlowNode(id="calc", kind="arithmetic", title="计算", config={})
    tool = FlowNode(id="tool", kind="tool", title="工具", config={})
    code = FlowNode(id="code", kind="code", title="代码", config={})
    agent = FlowNode(id="agent", kind="agent", title="智能体", config={})
    unknown_http = FlowNode(id="http", kind="http-request", title="HTTP", config={"datasourceId": "src"})
    write_http = FlowNode(id="httpw", kind="http-request", title="HTTP 写", config={"datasourceId": "src", "method": "POST"})
    read_http = FlowNode(id="httpr", kind="http-request", title="HTTP 读", config={"datasourceId": "src", "method": "GET"})
    nested = FlowNode(id="loop", kind="loop", title="循环", config={
        "iterations": 2,
        "subgraph": graph_for({"id": "inner", "kind": "tool", "title": "工具", "config": {"toolName": "side_effect"}}).model_dump(by_alias=True),
    })
    pure_nested = FlowNode(id="pure-loop", kind="loop", title="纯计算循环", config={
        "iterations": 2,
        "subgraph": graph_for({"id": "inner", "kind": "arithmetic", "title": "计算",
                                "config": {"fields": [{"name": "result", "formula": "1"}]}}).model_dump(by_alias=True),
    })

    assert should_retry_node(safe, RuntimeError("transient"))
    assert not should_retry_node(safe, ValidationError("bad input"))
    assert not should_retry_node(tool, ToolExecutionError("provider failed"))
    assert not should_retry_node(code, RuntimeError("sandbox failed"))
    assert not should_retry_node(agent, RuntimeError("model failed"))
    assert not should_retry_node(unknown_http, RuntimeError("unknown method"))
    assert not should_retry_node(write_http, RuntimeError("write failed"))
    assert should_retry_node(read_http, ToolExecutionError("read timeout"))
    assert not should_retry_node(nested, RuntimeError("child failed"))
    assert should_retry_node(pure_nested, RuntimeError("pure node failed"))


def test_tool_gateway_exception_finishes_call_with_redacted_failure(monkeypatch):
    from app.providers.tool import gateway as gateway_module

    calls, finished, commits = [], [], []
    original_record = nodes.runtime.record_tool_call
    original_finish = nodes.runtime.finish_tool_call
    original_gateway = gateway_module.ToolGateway
    original_context = gateway_module.build_context

    class FakeGateway:
        def __init__(self, _db):
            pass

        def resolve(self, *_args, **_kwargs):
            return None

        def execute(self, *_args, **_kwargs):
            raise SchemaValidationError("secret-value-must-not-be-stored")

    def fake_record(*_args, **_kwargs):
        call = SimpleNamespace(id="call-1", status="running")
        calls.append(call)
        return call

    def fake_finish(_db, call, payload, **_kwargs):
        call.status = "failed"
        finished.append(payload)

    monkeypatch.setattr(nodes.runtime, "record_tool_call", fake_record)
    monkeypatch.setattr(nodes.runtime, "finish_tool_call", fake_finish)
    monkeypatch.setattr(gateway_module, "ToolGateway", FakeGateway)
    monkeypatch.setattr(gateway_module, "build_context", lambda **_kwargs: {})
    ctx = SimpleNamespace(
        db=SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        workspace_id="workspace", user_id="user",
    )
    config = SimpleNamespace(tool_name="offline_tool", arguments={}, include_input=False)

    with pytest.raises(SchemaValidationError):
        nodes._execute_tool(ctx, config, {"input": 1}, run_id="run", step_id=None)

    assert len(calls) == 1 and calls[0].status == "failed"
    assert len(finished) == 1 and "secret-value" not in str(finished[0])
    assert finished[0]["error_code"] == "schema_validation_error"
    assert len(commits) == 2


def test_container_error_branch_does_not_run_normal_output(monkeypatch):
    real_execute = nodes.execute_node
    def execute(ctx, node, inputs, budget, **kwargs):
        if node.kind == "tool":
            raise ToolExecutionError("failed")
        return real_execute(ctx, node, inputs, budget, **kwargs)
    monkeypatch.setattr(nodes, "execute_node", execute)
    graph = graph_for({"id": "operation", "kind": "tool", "title": "操作", "config": {"toolName": "offline_test"},
                       "errorStrategy": "fail-branch"},
                      extra_nodes=[{"id": "error_end", "kind": "end", "title": "错误结果"}],
                      extra_edges=[{"id": "error", "source": "operation", "target": "error_end", "port": "error"}])
    output, trace = nodes._execute_container_subgraph(None, graph, {}, RuleBudget(time.monotonic() + 3),
                                                     execution_id="run", run_id=None, parent_node_id="container")
    assert output["errorMessage"] == "failed"
    assert trace["nodes"]["end"]["status"] == "skipped"
    assert trace["nodes"]["error_end"]["status"] == "completed"


def test_container_null_terminal_is_not_confused_with_no_terminal(monkeypatch):
    real_execute = nodes.execute_node
    monkeypatch.setattr(nodes, "execute_node", lambda ctx, node, inputs, budget, **kw:
                        None if node.kind == "tool" else real_execute(ctx, node, inputs, budget, **kw))
    output, _ = nodes._execute_container_subgraph(None, graph_for(), {}, RuleBudget(time.monotonic() + 3),
                                                 execution_id="run", run_id=None, parent_node_id="container")
    assert output is None
    iterator = FlowNode(id="iterate", kind="iterator", title="迭代", config={"subgraph": graph_for().model_dump(by_alias=True)})
    with pytest.raises(ValidationError, match="必须返回对象"):
        real_execute(None, iterator, {"input": [{"x": 1}]}, RuleBudget(time.monotonic() + 3))


def test_visual_loop_requires_no_unused_formula_and_declared_start_preserves_context():
    graph = FlowGraph.model_validate({"nodes": [
        {"id": "start", "kind": "start", "title": "开始", "config": {"variables": [{"variable": "x", "label": "数值", "type": "number"}]}},
        {"id": "end", "kind": "end", "title": "结束"}],
        "edges": [{"id": "edge", "source": "start", "target": "end"}]})
    LoopConfig.model_validate({"subgraph": graph.model_dump(by_alias=True)})
    WhileConfig.model_validate({"expression": {"value": False}, "subgraph": graph.model_dump(by_alias=True)})
    assert inspect_graph(graph)["valid"]
    output, _ = nodes._execute_container_subgraph(None, graph, {"x": 1, "_index": 2, "_iteration": 3},
        RuleBudget(time.monotonic() + 3), execution_id="run", run_id=None, parent_node_id="container")
    assert output == {"x": 1, "_index": 2, "_iteration": 3}
    with pytest.raises(ValueError, match="循环体"):
        LoopConfig.model_validate({})
