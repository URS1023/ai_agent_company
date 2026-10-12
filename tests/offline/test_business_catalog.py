"""离线校验业务数据目录在「不取样本行」的前提下把表结构与问题一起交给模型。

验证行为：目录只使用已保存的表结构（带 sampleRows 也必须被剔除）；
模型收到的提示里同时包含业务问题与现有表结构。
隔离方式：SimpleNamespace + MagicMock 替代 db 与模型调用，monkeypatch 掉权限校验，
不建会话、不连库、不调用 provider（需通过 tests/offline/run_python_checks.py 运行）。
边界：样本行绝不外泄给模型；结构字段名保持原样。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import ValidationError
from app.modules.analytics import business_catalog as catalog
from app.modules.analytics import queries
from app.modules.analytics.schemas import QueryInput


def test_catalog_uses_saved_schema_without_samples(monkeypatch):
    source = SimpleNamespace(id="source")
    ctx = SimpleNamespace(workspace_id="workspace", db=MagicMock())
    tables = [{"tableName": "orders", "tableComment": "订单", "columns": [{"columnName": "amount"}], "sampleRows": [{"amount": 9}]}]
    ctx.db.scalar.return_value = SimpleNamespace(status="ready", source_version=1, tables_json=tables)
    monkeypatch.setattr(catalog, "require_permission", lambda *_: None)
    monkeypatch.setattr(catalog, "source_version", lambda *_: 1)
    recall = MagicMock(return_value=(tables, {}))
    monkeypatch.setattr(catalog, "recall_tables", recall)
    result = catalog.business_schema(ctx, source, "财务和订单")
    assert result["tables"][0]["tableName"] == "orders"
    assert "sampleRows" not in result["tables"][0]
    assert recall.call_args.kwargs == {"include_samples": False}
    ctx.db.scalar.return_value.source_version = 0
    with pytest.raises(ValidationError, match="同步元数据"):
        catalog.business_schema(ctx, source, "财务和订单")
    assert recall.call_count == 1


def test_query_model_receives_business_question_and_existing_schema(monkeypatch):
    import json
    source = SimpleNamespace(id="source", provider="mysql", connection_config={"database": "local_fixture"})
    ctx = SimpleNamespace(workspace_id="workspace")
    payload = QueryInput(datasourceId="source", query="查看财务和订单数据", mode="natural_language")
    tables = [{"tableName": "orders", "columns": [{"columnName": "amount"}]}]
    model = MagicMock(return_value={"content": '{"sql":"SELECT amount FROM orders LIMIT 10","explanation":"订单明细，未限定时间"}'})
    monkeypatch.setattr(queries.model, "chat_for_run", model)
    sql, _ = queries.natural_language_sql(ctx, source, payload, tables)
    messages = model.call_args.args[1]
    context = json.loads(messages[1]["content"])
    assert context["schema"] == tables
    assert context["question"] == "查看财务和订单数据"
    assert "用户未提供表名不构成澄清理由" in messages[0]["content"]
    assert sql == "SELECT amount FROM orders LIMIT 10"
