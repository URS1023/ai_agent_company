"""离线校验模型调用记录的落库结构与失败可见性。

验证行为：模型调用轨迹写入符合真实 ORM 列定义；记录失败时可见，且不把供应商原始错误暴露给用户。
隔离方式：MagicMock 会话 + SimpleNamespace 响应，断言 ORM 字段与脱敏文案，不连库、不发请求。
边界：字段名必须与 ModelCall 模型一致；错误摘要只保留可读信息。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models import ModelCall
from app.modules.runtime import agent
from app.providers import model


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_model_trace_records_use_real_orm_schema(monkeypatch, status):
    monkeypatch.setattr(model, "_profile_config", lambda *a, **kw: {
        "provider": "test", "model": "configured-model",
    })
    db = MagicMock()
    run = SimpleNamespace(id="run", workspace_id="workspace", model_profile="reasoning")
    started = datetime.utcnow()
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}

    agent._record_model_call(
        db, run, started, status, usage,
        error="provider error with secret-test-value", model_name="actual-model",
    )

    db.add.assert_called_once()
    row = db.add.call_args.args[0]
    assert isinstance(row, ModelCall)
    assert row.run_id == run.id and row.workspace_id == run.workspace_id
    assert row.model == "actual-model" and row.provider == "test"
    assert row.status == status and row.usage_json == usage
    assert row.error_code == ("model_error" if status == "failed" else None)
    assert row.started_at == started and row.completed_at >= started
    assert "secret-test-value" not in str(row.__dict__)


def test_trace_failure_is_visible_without_exposing_provider_error(monkeypatch, caplog):
    def fail(*a, **kw):
        raise ValueError("secret-test-value")

    monkeypatch.setattr(model, "_profile_config", fail)
    db = MagicMock()
    run = SimpleNamespace(id="run", workspace_id="workspace", model_profile="reasoning")

    agent._record_model_call(db, run, datetime.utcnow(), "failed", None)

    db.add.assert_not_called()
    assert "model_call_record_failed run_id=run error_type=ValueError" in caplog.text
    assert "secret-test-value" not in caplog.text
