"""验证业务日期边界与关系接口合同，不读取外部服务。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.modules.analytics.metadata import _column_scope
from app.modules.analytics.schemas import TableView
from app.providers.tool import builtin_provider


def test_business_clock_keeps_local_date_and_explicit_utc(monkeypatch):
    """UTC 午夜前的时刻在东八区已经是次日，两个日期必须可区分。"""
    local = datetime(2026, 9, 26, 6, 30, tzinfo=timezone(timedelta(hours=8)))

    class Clock:
        @classmethod
        def now(cls, zone):
            return SimpleNamespace(astimezone=lambda: local)

    monkeypatch.setattr(builtin_provider, "datetime", Clock)
    spec = SimpleNamespace(name="current_datetime")
    result = builtin_provider.invoke(spec, {}, {})
    assert result.data["date"] == "2026-09-26" and result.data["timezone_offset"] == "+0800"
    assert result.data["utc_iso"] == "2026-09-25T22:30:00+00:00"


def test_table_view_retains_only_visible_declared_relations():
    """界面保留真实业务关系，同时不显示已排除的对端字段。"""
    relation = {"sourceTable": "stock", "sourceField": "material", "targetTable": "material", "targetField": "key", "relationType": "reference"}
    tables = [{"tableName": "stock", "columns": [{"columnName": "material", "dataType": "text"}], "declaredRelations": [relation]},
              {"tableName": "material", "columns": [{"columnName": "key", "dataType": "text"}]}]
    visible = _column_scope(SimpleNamespace(connection_config={}), tables)
    assert TableView.model_validate(visible[0]).declaredRelations == [relation]
    hidden = _column_scope(SimpleNamespace(connection_config={"allowedTables": ["stock"]}), tables)
    assert TableView.model_validate(hidden[0]).declaredRelations == []
    assert tables[0]["declaredRelations"] == [relation]
