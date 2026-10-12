"""Business-topic routing uses the authenticated turn, not invented constraints.

中文说明：业务主题路由只依据已鉴权的本轮提问，不得自行发明限定条件（或扩大范围）。
隔离方式：SimpleNamespace + MagicMock 替代会话与模型，纯函数断言，不连库。
边界：范围来自用户显式选择；缺失信息时追问而不是猜测。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.errors import PermissionErrorEx
from app.models import Run

from app.modules.analytics import business_overview, queries, sources
from app.providers.tool.builtin_provider import _business_action


@pytest.mark.parametrize("original,expects_overview", [
    ("收款与回款数据", True),
    ("查看财务数据", True),
    ("查询今年客户甲的回款明细", False),
])
def test_query_scope_preserves_the_user_request(monkeypatch, original, expects_overview):
    db = MagicMock()
    run = SimpleNamespace(id='r', conversation_id='c',
        workspace_id="w", started_by_user_id="u", context_snapshot={"user_request": original})
    db.get.side_effect = lambda kind, key: run if kind is Run else SimpleNamespace(
        workspace_id='w', owner_user_id='u')
    ctx = {"auth_context": SimpleNamespace(db=db, workspace_id="w", user_id="u"),
           "run_id": "r", "selected_datasource_ids": ["s"]}
    monkeypatch.setattr(sources, "get_source", lambda *a, **kw: SimpleNamespace(id="s", provider="mysql", connection_config={'version': 1}))
    overview = MagicMock(return_value={"results": []})
    query = MagicMock(return_value=SimpleNamespace(model_dump=lambda **kw: {"data": []}))
    monkeypatch.setattr(business_overview, "execute_overview", overview)
    monkeypatch.setattr(queries, "execute_query", query)
    _business_action("query_business_data", {
        "datasource_id": "s", "query": "查询收款和回款的详细数据，包括收款金额、收款日期、客户信息、回款状态等",
    }, ctx)
    assert overview.call_count == int(expects_overview)
    assert query.call_count == int(not expects_overview)
    if expects_overview:
        assert overview.call_args.args[2] == original
    else:
        assert query.call_args.args[1].query == original
        assert query.call_args.args[1].session_id == 'c'


def test_query_does_not_use_another_users_turn(monkeypatch):
    db = MagicMock()
    db.get.return_value = SimpleNamespace(
        workspace_id="other", started_by_user_id="other", context_snapshot={"user_request": "查看财务数据"},
    )
    ctx = {"auth_context": SimpleNamespace(db=db, workspace_id="w", user_id="u"),
           "run_id": "r", "selected_datasource_ids": ["s"]}
    monkeypatch.setattr(sources, "get_source", lambda *a, **kw: SimpleNamespace(id="s", provider="mysql"))
    overview = MagicMock(side_effect=AssertionError("unrelated run must not broaden the scope"))
    query = MagicMock(return_value=SimpleNamespace(model_dump=lambda **kw: {"data": []}))
    monkeypatch.setattr(business_overview, "execute_overview", overview)
    monkeypatch.setattr(queries, "execute_query", query)
    with pytest.raises(PermissionErrorEx):
        _business_action("query_business_data", {"datasource_id": "s", "query": "查询今年客户甲的回款明细"}, ctx)
    query.assert_not_called()
