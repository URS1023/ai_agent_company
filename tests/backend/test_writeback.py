"""Real managed SQLite writes, atomicity, explicit scope and uncertain COMMIT."""
from decimal import Decimal
import sqlite3

import pytest
from sqlalchemy import Column, Integer, Numeric, SmallInteger, String, select
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.engine import Connection

from app.errors import ValidationError
from app.models import DataSource
from app.modules.analytics.connections import managed_database_path
from app.modules.writeback.models import WriteOperation
from app.modules.writeback.values import convert
from test_analytics import source, run_sql
from test_alerts import equipment, policy, business_node
from test_collection import create_source, upstream
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute as execute_flow, graph, node, result

API = "/api/v1/writeback"


def execute(client, headers, flow, inputs=None, **options):
    return execute_flow(client, headers, flow, inputs, **{"confirmWrites": True, **options})


def test_explicit_write_confirmation_required_before_any_node(client, administrator, source):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    assert execute(client, administrator, flow, confirmWrites=False).status_code == 422
    assert execute(client, administrator, flow, confirmWrites="true").status_code == 422


def target(client, headers, source, **extra):
    payload = {"name": "检测结果表", "datasourceId": source["id"], "sourceVersion": source["version"],
               "tableName": "sales", "columns": ["id", "amount", "note"], "modes": ["insert", "update"], **extra}
    return result(client.post(API + "/targets", headers=headers, json=payload), 201), payload


def write_node(target, **extra):
    return node("write", "output-table", {"targetId": target["id"], "targetVersion": target["version"],
                "mode": "insert", "recordPath": "$.data", "fields": [
                    {"column": field, "path": "$['" + field + "']"} for field in ["id", "amount", "note"]], **extra})


def rows(source, headers):
    with sqlite3.connect(managed_database_path(headers["X-Workspace-Id"], source["databaseName"])) as connection:
        return connection.execute("SELECT id, amount, note FROM sales ORDER BY id").fetchall()


def test_real_insert_idempotency_and_documented_receipt(client, administrator, source):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    inputs = {"data": [{"id": 10, "amount": 810, "note": "<script>bound value</script>"}]}
    first = result(execute(client, administrator, flow, inputs, idempotencyKey="write-exactly-once"))
    assert first["status"] == "completed", first
    assert rows(source, administrator)[-1] == (10, 810, "<script>bound value</script>")
    assert result(execute(client, administrator, flow, inputs, idempotencyKey="write-exactly-once")) == first
    receipts = result(client.get(API + "/operations", headers=administrator, params={"executionId": first["id"]}))
    assert len(receipts) == 1 and receipts[0]["status"] == "succeeded" and receipts[0]["affectedRows"] == 1
    assert receipts[0]["snapshot"]["target"]["tableName"] == "sales"
    assert "password" not in str(receipts)
    assert run_sql(client, administrator, source, "UPDATE sales SET amount=0").status_code == 422


def test_whole_batch_rollback_and_persisted_failure_receipt(client, administrator, source):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    before = rows(source, administrator)
    run = result(execute(client, administrator, flow, {"data": [
        {"id": 10, "amount": 1, "note": "first"}, {"id": 1, "amount": 2, "note": "duplicate primary key"},
    ]}), 502)
    assert run["status"] == "failed" and rows(source, administrator) == before
    operation = result(client.get(API + "/operations", headers=administrator, params={"executionId": run["id"]}))[0]
    assert operation["status"] == "failed" and operation["affectedRows"] == 0
    assert run["result"]["nodes"]["write"]["references"]["write_operation_id"] == operation["id"]


def test_unique_key_update_and_missing_row_rolls_back_prior_changes(client, administrator, source):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected, mode="update", keys=["id"])))
    good = result(execute(client, administrator, flow, {"data": [{"id": 1, "amount": 82, "note": "温度预警"}]}))
    assert good["status"] == "completed" and rows(source, administrator)[0] == (1, 82, "温度预警")
    before = rows(source, administrator)
    failed = result(execute(client, administrator, flow, {"data": [
        {"id": 1, "amount": 0, "note": "must rollback"}, {"id": 999, "amount": 1, "note": "absent"}]}), 502)
    assert failed["status"] == "failed" and rows(source, administrator) == before


@pytest.mark.parametrize("data", [
    [{"id": 6, "amount": "1.5", "note": "integer must not round"}],
    [{"id": 6, "amount": 1}],
    [{"id": 6, "amount": 1, "note": {"unsafe": "implicit JSON"}}],
    [{"id": 6, "amount": 1, "note": "x"}] * 1001,
])
def test_invalid_mappings_never_start_dml(client, administrator, source, data, db):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    before = rows(source, administrator)
    run = result(execute(client, administrator, flow, {"data": data}), 502)
    assert run["status"] == "failed" and rows(source, administrator) == before
    assert db.scalars(select(WriteOperation)).all() == []


def test_disabled_and_changed_targets_block_before_upstream_http(client, administrator, source, upstream):
    selected, payload = target(client, administrator, source)
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": http["id"]}), write_node(selected)))
    result(client.put(API + "/targets/" + selected["id"], headers=administrator, json={**payload, "version": 1, "name": "经审核的新目标"}))
    assert execute(client, administrator, flow).status_code == 409 and upstream[1] == []


def test_datasource_version_change_blocks_writes(client, administrator, source, db):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    stored = db.get(DataSource, source["id"])
    stored.connection_config = {**stored.connection_config, "version": 2}
    db.commit()
    assert execute(client, administrator, flow).status_code == 422


def test_missing_permission_rejected_before_http_and_cannot_create_whitelist(client, administrator, source, upstream):
    selected, payload = target(client, administrator, source)
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": http["id"]}), write_node(selected)))
    role = create_role(client, administrator, scope="workspace", permissions=["datasources.view", "datasources.edit", "workflows.view", "workflows.edit", "workflows.execute"])
    member = create_user(client, administrator, role_ids=[role["id"]])
    headers = activate_user(client)
    assert client.post(API + "/targets", headers=headers, json=payload).status_code == 403
    assert execute(client, headers, flow).status_code == 403 and upstream[1] == []


@pytest.mark.parametrize("changes", [{"tableName": "restricted_data", "columns": ["private_note"]}, {"columns": ["missing"]}, {"modes": []}])
def test_target_requires_real_permitted_table_and_fields(client, administrator, source, changes):
    payload = {"name": "unsafe", "datasourceId": source["id"], "sourceVersion": 1, "tableName": "sales", "columns": ["id"], "modes": ["insert"], **changes}
    assert client.post(API + "/targets", headers=administrator, json=payload).status_code == 422


def test_commit_acknowledgement_loss_is_uncertain_not_safe_to_retry(client, administrator, source, monkeypatch):
    selected, _ = target(client, administrator, source)
    flow = create_flow(client, administrator, graph(write_node(selected)))
    original = Connection.commit
    def lose_ack(connection):
        original(connection)
        if source["databaseName"] in str(connection.engine.url) and "mode=rw" in str(connection.engine.url):
            raise ConnectionError("lost after COMMIT")
    monkeypatch.setattr(Connection, "commit", lose_ack)
    inputs = {"data": [{"id": 10, "amount": 90, "note": "committed"}]}
    run = result(execute(client, administrator, flow, inputs, idempotencyKey="ambiguous-commit-1"), 502)
    assert run["status"] == "failed" and rows(source, administrator)[-1] == (10, 90, "committed")
    record = result(client.get(API + "/operations", headers=administrator, params={"executionId": run["id"]}))[0]
    assert record["status"] == "uncertain" and record["affectedRows"] is None
    assert result(execute(client, administrator, flow, inputs, idempotencyKey="ambiguous-commit-1"), 502) == run


def test_decimal_conversion_preserves_large_precision_and_rejects_rounding():
    column = Column("amount", Numeric(38, 2), nullable=False)
    value = "1234567890123456789012345678.12"
    assert convert(value, column, "postgres") == Decimal(value)
    with pytest.raises(ValidationError):
        convert("1.234", column, "postgres")
    with pytest.raises(ValidationError):
        convert("0.1", column, "sqlite")
    assert convert("80.000000000000000001", Column("exact", String), "sqlite") == "80.000000000000000001"


@pytest.mark.parametrize("kind,value,provider", [
    (Integer(), 2**31, "mysql"), (SmallInteger(), 2**15, "postgres"),
    (TINYINT(unsigned=True), -1, "mysql"), (TINYINT(unsigned=True), 256, "mysql"),
])
def test_integer_overflow_rejected_before_non_strict_driver_can_truncate(kind, value, provider):
    with pytest.raises(ValidationError):
        convert(value, Column("value", kind), provider)


def test_writeback_api_has_typed_responses_and_descriptions(client, administrator):
    spec = client.app.openapi()
    operations = [op for path, methods in spec["paths"].items() if path.startswith(API) for method, op in methods.items() if method in {"get", "post", "put", "delete"}]
    assert len(operations) == 6
    assert all(op.get("summary") and op.get("description") for op in operations)
    assert "TargetInput" in spec["components"]["schemas"] and "OperationView" in spec["components"]["schemas"]
    assert client.get(API + "/targets").status_code == 401


def test_http_alert_problems_write_actual_target_and_export_receipt(client, administrator, source, upstream):
    selected, _ = target(client, administrator, source)
    http, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    rule, _ = policy(client, administrator, equipment(client, administrator, datasourceId=http["id"]))
    writer = write_node(selected, recordPath="$.issues", fields=[
        {"column": "id", "origin": "literal", "value": 10},
        {"column": "amount", "origin": "literal", "value": 1},
        {"column": "note", "origin": "row", "path": "$.message"},
    ])
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": http["id"]}), business_node(rule), writer))
    run = result(execute(client, administrator, flow))
    assert run["status"] == "completed" and rows(source, administrator)[-1] == (10, 1, "检查冷却系统")
    report_id = run["result"]["outputs"]["end"]["alertReportId"]
    report = result(client.get("/api/v1/alerts/reports/" + report_id, headers=administrator))
    assert report["writeOperations"][0]["status"] == "succeeded" and report["summary"]["issues"] == 1
    exported = client.get("/api/v1/alerts/reports/" + report_id + "/export", headers=administrator)
    assert exported.status_code == 200 and "实际数据库写入凭据" in exported.text and "succeeded" in exported.text
    assert len(upstream[1]) == 1
