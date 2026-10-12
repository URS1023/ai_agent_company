"""Exercise authorized paging with an in-memory query adapter, never a database.

中文说明：用内存查询适配器验证"带授权的分页"，从不连数据库。
隔离方式：SimpleNamespace 内存适配器 + monkeypatch，验证分页与授权过滤的组合。
"""
from types import SimpleNamespace

from app.errors import PermissionErrorEx
from app.modules.quality.concessions import _authorized_page
from app.modules.quality.concessions import _source_paths, _single_source_value
from app.errors import ValidationError
import pytest


class Query:
    def __init__(self, rows, limit=None, offset=0):
        self.rows, self.count, self.start = rows, limit, offset

    def limit(self, value):
        return Query(self.rows, value, self.start)

    def offset(self, value):
        return Query(self.rows, self.count, value)

    def all(self):
        return self.rows[self.start:self.start + self.count]


def page(rows, limit, offset=0):
    ctx = SimpleNamespace(db=SimpleNamespace(execute=lambda query: query))

    def resolve(row):
        if row % 2:
            raise PermissionErrorEx("source no longer visible")
        return row

    return _authorized_page(ctx, Query(rows), resolve, limit=limit, offset=offset)


def test_visibility_before_offset_and_no_inaccessible_total():
    items, total, more = page(list(range(241)), 30, 30)
    assert items == list(range(60, 120, 2))
    assert total is None and more is True


def test_full_last_page_has_no_phantom_next_page():
    items, total, more = page(list(range(60)), 30)
    assert items == list(range(0, 60, 2))
    assert total == 30 and more is False


def test_offset_beyond_end_returns_actual_visible_total():
    assert page(list(range(60)), 30, 100) == ([], 30, False)


def test_invisible_first_batch_does_not_hide_later_visible_rows():
    rows = list(range(1, 202, 2)) + [202, 204, 206]
    assert page(rows, 2) == ([202, 204], None, True)


def test_workflow_source_listing_sorts_only_references_not_saved_json(monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.dialects import mysql
    from app.common import authorization
    from app.modules.quality import concessions
    from app.modules.workflows import service
    from app.modules.workflows.models import WorkflowExecution

    statements = []
    def execute(query):
        statements.append(query)
        return SimpleNamespace(all=lambda: [])

    ctx = SimpleNamespace(workspace_id='local-test', db=SimpleNamespace(execute=execute))
    monkeypatch.setattr(concessions, 'require_permission', lambda *args: None)
    monkeypatch.setattr(authorization, 'access_policy', lambda ctx: SimpleNamespace(permissions={'workflows.view'}))
    monkeypatch.setattr(concessions, 'visible_records', lambda *args: select(WorkflowExecution))
    monkeypatch.setattr(service, '_domain_filter', lambda *args: True)
    assert concessions.list_source_options(ctx, kind='workflow', limit=10, offset=0) == ([], 0, False)
    query = statements[0]
    assert [column.key for column in query.selected_columns] == ['id', 'workflow_id', 'status']
    sql = str(query.compile(dialect=mysql.dialect()))
    assert 'ORDER BY workflow_executions.created_at DESC' in sql
    assert 'result_json' not in sql and 'snapshot' not in sql


def test_nested_source_paths_support_objects_arrays_and_decimal_text():
    assert list(_source_paths({"rows": [{"weight": "82.5", "count": 0}]})) == [
        ("$['rows'][0]['weight']", "text"), ("$['rows'][0]['count']", "number"),
    ]


@pytest.mark.parametrize("key", ["apiKey", "access_key", "privateKey", "credential", "Authorization"])
def test_source_credentials_are_not_listed_or_read_directly(key):
    assert list(_source_paths({key: "not-a-real-secret"})) == []
    with pytest.raises(ValidationError):
        _single_source_value({key: "not-a-real-secret"}, "$['" + key + "']")
