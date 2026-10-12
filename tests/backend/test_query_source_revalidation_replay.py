"""真实 Worker 持久回放必须按当前列范围验证，不能重复执行旧 SQL。"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import DataSource, ToolCall
from app.modules.analytics import sources
from app.modules.analytics.models import QueryHistory
from test_analytics import source
from test_analytics_config_lifecycle import client
from test_query_source_revalidation import edit_source
from test_tool_replay_authorization import replay_actor, grant


def test_worker_replay_rejects_revoked_columns_without_reexecution(
    client, administrator, source, replay_actor, monkeypatch,
):
    actor = replay_actor
    grant(actor, "datasources", source["id"], ["view", "execute"])
    args = {"datasource_id": source["id"], "query": "SELECT amount FROM sales", "mode": "sql"}
    actor.cache("query_business_data", args)
    value = {"queryId": "replay-query-proof", "runId": "replay-run", "datasourceId": source["id"],
             "datasourceVersion": 1, "generatedSql": "SELECT amount FROM sales", "parameters": {},
             "data": [{"amount": 100}], "columns": [{"columnName": "amount", "dataType": "bigint"}]}
    actor.db.add(QueryHistory(id=value["queryId"], workspace_id=actor.workspace_id,
        owner_user_id=actor.user.id, datasource_id=source["id"], run_id="replay-run", query_mode="sql",
        query_text=value["generatedSql"], generated_sql=value["generatedSql"], status="success",
        datasource_version=1, result_json=value))
    call = actor.db.get(ToolCall, "replay-call")
    receipt = {"status": "success", "data": value}
    call.result_json = receipt
    actor.db.commit()
    assert actor.replay("query_business_data", args) == receipt
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    edit_source(client, administrator, source)
    output = actor.replay("query_business_data", args)
    assert output["status"] == "failed", output
    assert "amount" not in str(output.get("data"))
    actor.db.expire_all()
    assert actor.db.get(ToolCall, "replay-call").result_json == receipt
    assert actor.db.scalars(select(QueryHistory)).one().result_json == value


@pytest.mark.parametrize("change", ["owner", "query_id", "rows", "sql", "columns", "missing_proof",
                                    "malformed_history", "malformed_history_columns"])
def test_worker_replay_requires_matching_authorized_query_proof(client, administrator, source, replay_actor, change):
    """伪造来源、他人记录或混搭列数据都不能借当前单源权限读取旧回执。"""
    actor = replay_actor
    grant(actor, "datasources", source["id"], ["view", "execute"])
    args = {"datasource_id": source["id"], "query": "SELECT amount FROM sales", "mode": "sql"}
    actor.cache("query_business_data", args)
    value = {"queryId": "replay-query-proof", "datasourceId": source["id"],
             "generatedSql": "SELECT amount FROM sales", "parameters": {},
             "data": [{"amount": 100}], "columns": [{"columnName": "amount", "dataType": "bigint"}]}
    record = QueryHistory(id=value["queryId"], workspace_id=actor.workspace_id,
        owner_user_id=actor.user.id, datasource_id=source["id"], run_id="replay-run", query_mode="sql",
        query_text=value["generatedSql"], generated_sql=value["generatedSql"], status="success",
        datasource_version=1, result_json=deepcopy(value))
    actor.db.add(record)
    call = actor.db.get(ToolCall, "replay-call")
    call.result_json = {"status": "success", "data": deepcopy(value)}
    actor.db.commit()
    # 当前角色没有历史查看权；执行回执复用不能新增历史查看权限要求或重发 SQL。
    assert actor.replay("query_business_data", args) == call.result_json
    forged = deepcopy(value)
    if change == "owner":
        record.owner_user_id = actor.db.get(DataSource, source["id"]).owner_user_id
        assert record.owner_user_id != actor.user.id
    elif change == "query_id":
        forged["queryId"] = "not-the-saved-query"
    elif change == "rows":
        forged["data"] = [{"amount": 999}]
    elif change == "sql":
        forged["generatedSql"] = "SELECT id FROM sales"
    elif change == "columns":
        forged["columns"] = [{"columnName": "id"}]
    elif change == "malformed_history":
        record.result_json = [deepcopy(value)]
    elif change == "malformed_history_columns":
        record.result_json = {**deepcopy(value), "columns": 7}
    else:
        forged.pop("queryId")
    call.result_json = {"status": "success", "data": forged}
    actor.db.commit()
    result = actor.replay("query_business_data", args)
    assert result["status"] == "failed", result
    assert result.get("metadata", {}).get("operation_reexecuted") is False
    actor.db.expire_all()
    assert actor.db.get(ToolCall, "replay-call").result_json["data"] == forged
