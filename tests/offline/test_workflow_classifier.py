"""Exercise classification and routing contracts without model/network/DB calls.

中文说明：工作流分类与路由契约（不调用模型、不发网络请求、不连数据库）。
隔离方式：纯函数 + monkeypatch，本地执行。
边界：分类结果决定后续工具选择，误判会放大成错误数据，因此要求显式且可解释。
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.errors import ToolExecutionError
from app.modules.workflows import nodes
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import check_config
from app.modules.workflows.schemas import ClassifierConfig


def model_node():
    return SimpleNamespace(kind="classifier", config={
        "mode": "model", "modelProfile": "workspace_classifier",
        "promptTemplate": "{{ input }}", "temperature": 0,
        "cases": [{"id": "equipment", "label": "设备", "description": "设备运行、维修与保养"},
                  {"id": "finance", "label": "财务", "description": "收入、费用与收款"}],
        "fallback": "other",
    })


def test_question_classification_accepts_text_and_only_configured_routes(monkeypatch):
    captured = []
    def model(*args):
        captured.append(args)
        return {"content": '```json\n{"category":"equipment"}\n```', "model": "configured", "usage": {"total_tokens": 9}}
    monkeypatch.setattr(nodes, "_recorded_model_call", model)
    node = model_node()
    check_config(node)
    result = nodes.execute_node(None, node, {"input": "本周哪些设备需要保养？"},
                                RuleBudget(time.monotonic() + 2), run_id="run", step_id="step")
    assert result["category"] == "equipment" and result["matched"] is True
    assert result["data"] == "本周哪些设备需要保养？"
    assert captured[0][1:4] == ("run", "step", "workspace_classifier")
    assert "维修与保养" in captured[0][4][0]["content"]
    assert captured[0][4][1]["content"] == result["data"]


@pytest.mark.parametrize("content", ['{"category":"invented"}', '[]', 'not JSON'])
def test_invalid_model_class_never_becomes_success_or_fallback(monkeypatch, content):
    monkeypatch.setattr(nodes, "_recorded_model_call", lambda *_: {"content": content})
    with pytest.raises(ToolExecutionError):
        nodes.execute_node(None, model_node(), {"input": "问题"}, RuleBudget(time.monotonic() + 2), run_id="run")


def test_unmatched_model_route_and_legacy_rules_remain_distinct(monkeypatch):
    monkeypatch.setattr(nodes, "_recorded_model_call", lambda *_: {"content": '{"category":"other"}'})
    unmatched = nodes.execute_node(None, model_node(), {"input": "无关问题"}, RuleBudget(time.monotonic() + 2), run_id="run")
    assert unmatched["category"] == "other" and unmatched["matched"] is False
    def forbidden(*_):
        raise AssertionError("legacy rule nodes must not call a model")
    monkeypatch.setattr(nodes, "_recorded_model_call", forbidden)
    node = SimpleNamespace(kind="classifier", config={"cases": [
        {"id": "yes", "label": "符合", "expression": {"value": True}}]})
    assert ClassifierConfig.model_validate(node.config).mode == "rules"
    check_config(node)
    result = nodes.execute_node(None, node, {"input": {"amount": 0}}, RuleBudget(time.monotonic() + 2))
    assert result["category"] == "yes" and result["data"]["amount"] == 0


def test_rules_require_predicates_and_fallback_cannot_shadow_a_class():
    with pytest.raises(ValueError, match="判断条件"):
        ClassifierConfig.model_validate({"cases": [{"id": "yes", "label": "符合"}]})
    with pytest.raises(ValueError, match="重复"):
        ClassifierConfig.model_validate({"mode": "model", "cases": [{"id": "other", "label": "其他"}]})


def test_only_selected_class_edge_activates_and_stale_ports_fail_validation():
    from app.modules.workflows.schemas import FlowGraph
    from app.modules.workflows.engine import active_parent_ids
    from app.modules.workflows.graph import inspect_graph
    graph = FlowGraph.model_validate({"nodes": [
        {"id": "start", "kind": "start", "title": "开始"},
        {"id": "classify", "kind": "classifier", "title": "分类", "config": model_node().config},
        *[{"id": key, "kind": "end", "title": key} for key in ("equipment", "finance", "other")],
    ], "edges": [
        {"id": "initial", "source": "start", "target": "classify"},
        *[{"id": "route_" + key, "source": "classify", "target": key, "port": "case:" + key}
          for key in ("equipment", "finance", "other")],
    ]})
    assert inspect_graph(graph)["valid"]
    states = {"classify": {"status": "completed", "output": {"category": "equipment", "matched": True}}}
    assert active_parent_ids(graph, "equipment", states) == ["classify"]
    assert active_parent_ids(graph, "finance", states) == []
    assert active_parent_ids(graph, "other", states) == []
    states["classify"]["output"] = {"category": "other", "matched": False}
    assert active_parent_ids(graph, "other", states) == ["classify"]
    graph.edges[1].port = "case:deleted"
    assert not inspect_graph(graph)["valid"]
