"""Follow-up completion is tool-free and does not replace the answer on failure.

中文说明：追问补全不调用任何工具；补全失败时不得替换（覆盖）原有回答内容。
隔离方式：SimpleNamespace + MagicMock，离线运行，不连库、不调用模型。
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.modules.runtime import agent


def test_followups_use_finished_answer_and_track_usage(monkeypatch):
    suggestions = ["导出这两批资金申请的差异核对表", "按部门汇总这两批资金申请", "生成这两批资金支付的分析图表"]
    model = MagicMock(return_value={"content": json.dumps(suggestions), "usage": {"total_tokens": 42}})
    monkeypatch.setattr(agent, "chat_for_run", model)
    monkeypatch.setattr(agent, "_record_model_call", MagicMock())
    usage = {}
    result, error = agent._generate_followups(None, SimpleNamespace(id="run", workspace_id="w"),
        "查看财务和订单数据", "合同号字段全部为空。下一步建议：按部门汇总资金申请。", [], usage)
    assert result == suggestions and not error
    assert model.call_count == 1 and model.call_args.kwargs["tools"] is None
    context = json.loads(model.call_args.args[1][-1]["content"])
    assert "合同号字段全部为空" in context["answer"]
    assert usage["total_tokens"] == 42


def test_failed_suggestions_do_not_raise_or_invent_templates(monkeypatch):
    model = MagicMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(agent, "chat_for_run", model)
    monkeypatch.setattr(agent, "_record_model_call", MagicMock())
    questions, error = agent._generate_followups(None, SimpleNamespace(id="run", workspace_id="w"), "问题", "正文", [], {})
    assert len(questions) == 3 and not error and all('问题' in item for item in questions)
    assert model.call_count == 1


def test_invalid_suggestion_payload_is_isolated(monkeypatch):
    monkeypatch.setattr(agent, "chat_for_run", lambda *a, **kw: {"content": '{"questions": "bad"}'})
    monkeypatch.setattr(agent, "_record_model_call", MagicMock())
    questions, error = agent._generate_followups(None, SimpleNamespace(id="run", workspace_id="w"), "问题", "正文", [], {})
    assert len(questions) == 3 and not error
