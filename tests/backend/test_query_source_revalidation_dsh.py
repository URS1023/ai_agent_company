"""DSH SQL 的授权结构基线与同轮缓存必须独立验证，不能重查业务数据兜底。"""
import pytest

from app.modules.analytics import dsh_query_execution, sources
from test_analytics_config_lifecycle import client
from test_data_profile import profile_fixture
from test_dsh_query_execution import dsh_fixture, execute


@pytest.mark.parametrize("cached", [False, True])
def test_dsh_scope_preparation_source_edit_rejects_cached_or_new_query(
    client, administrator, dsh_fixture, monkeypatch, cached,
):
    if cached:
        first = execute(dsh_fixture)
        assert first.status == "success", first.error_summary
    before = len(dsh_fixture.calls)
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    original = dsh_query_execution.validate_scope

    def validate(*args, **kwargs):
        value = original(*args, **kwargs)
        response = client.put("/api/v1/analytics/datasources/" + dsh_fixture.source.id,
            headers=administrator, json={"name": "已收窄", "type": "sqlite",
                "databaseName": dsh_fixture.path.name, "version": 1,
                "allowedTables": ["invoices"], "allowedColumns": {"invoices": ["id"]}})
        assert response.status_code == 200, response.text
        return value

    monkeypatch.setattr(dsh_query_execution, "validate_scope", validate)
    result = execute(dsh_fixture)
    assert result.status == "failed" and result.error_code == "conflict", result.to_dict()
    assert len(dsh_fixture.calls) == before


def test_dsh_unchanged_cache_has_no_extra_query(dsh_fixture):
    first = execute(dsh_fixture)
    second = execute(dsh_fixture)
    assert first.status == second.status == "success", second.error_summary
    assert second.metadata["same_turn_result_reused"]
    assert second.data["queryId"] == first.data["queryId"]
    assert len(dsh_fixture.calls) == 1


def test_dsh_tool_disabled_during_scope_preparation_cannot_return_cache(dsh_fixture, monkeypatch):
    from app.db import new_session
    from app.modules.capabilities.models import ToolMethodMetadata
    first = execute(dsh_fixture)
    assert first.status == "success", first.error_summary
    original = dsh_query_execution.validate_scope

    def validate(*args, **kwargs):
        value = original(*args, **kwargs)
        with new_session() as db:
            db.add(ToolMethodMetadata(workspace_id=dsh_fixture.context["workspace_id"],
                tool_name="query_business_data", code="query_business_data", enabled=False,
                created_by_user_id=dsh_fixture.context["user_id"], updated_by_user_id=dsh_fixture.context["user_id"]))
            db.commit()
        return value

    monkeypatch.setattr(dsh_query_execution, "validate_scope", validate)
    result = execute(dsh_fixture)
    assert result.status == "failed" and result.error_code == "forbidden", result.to_dict()
    assert len(dsh_fixture.calls) == 1
