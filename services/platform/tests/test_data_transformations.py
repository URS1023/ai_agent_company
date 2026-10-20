"""固定小样本验证处理/质量/指标，集成仅新tmp SQLite与CSV，无服务或外部查询。"""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import Settings
from agent_platform.modules.data import models as m
from agent_platform.modules.data.metrics import MetricService, metric_result, validate_definition
from agent_platform.modules.data.processing import run_pipeline
from agent_platform.modules.data.quality import check_quality
from agent_platform.modules.data.schemas import ProcessingInput, QualityInput
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.values import calculate, decimal_text, decimal_value, typed


def test_manufacturing_pipeline_keeps_decimal_precision_and_source_lines():
    rows = [{"batch": "B1", "qty": "0.1", "price": "0.2", "unit": "kg", "status": "ok"}, {"batch": "B1", "qty": "0.2", "price": "0.3", "unit": "kg", "status": "ok"}, {"batch": "B2", "qty": "", "price": "1", "unit": "kg", "status": "hold"}, {"batch": "B3", "qty": "2", "price": "2", "unit": "g", "status": "bad"}]
    original = deepcopy(rows)
    operations = [
        {"type": "drop_null", "field": "qty"},
        {"type": "filter", "conditions": [{"field": "status", "operator": "in", "value": ["ok"]}]},
        {"type": "derive", "output_field": "amount", "expression": {"op": "multiply", "args": [{"field": "qty"}, {"field": "price"}]}},
        {"type": "cast", "field": "qty", "output_field": "qty_num", "data_type": "decimal"},
        {"type": "rename", "field": "batch", "output_field": "batch_no"},
        {"type": "aggregate", "group_by": ["batch_no", "unit"], "aggregations": [{"name": "amount", "function": "sum", "field": "amount"}, {"name": "average_qty", "function": "avg", "field": "qty_num", "unit_field": "unit"}, {"name": "samples", "function": "count"}]},
    ]
    parsed = ProcessingInput(name="批次成本", query_id="source", operations=operations).model_dump()
    result = run_pipeline(rows, list(rows[0]), parsed["operations"])
    assert result["rows"] == [{"batch_no": "B1", "unit": "kg", "amount": "0.08", "average_qty": "0.15", "samples": 2}]
    assert result["source_row_numbers"] == [[1, 2]]
    assert result["steps"][0]["input_rows"] == 4 and len(result["steps"]) == 6
    assert rows == original


def test_old_selection_deduplication_and_missing_values_still_work():
    result = run_pipeline([{"id": "1", "value": "a"}, {"id": "1", "value": "a"}, {"id": "2", "value": None}], ["id", "value"], [{"type": "select", "fields": ["id", "value"]}, {"type": "drop_null", "field": "value"}, {"type": "deduplicate", "fields": ["id"]}])
    assert result["rows"] == [{"id": "1", "value": "a"}] and result["source_row_numbers"] == [[1]]


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "wrong", "1e999999"])
def test_numeric_cast_fails_at_exact_row_without_silent_zero(value):
    with pytest.raises(DomainError) as invalid:
        run_pipeline([{"value": "0"}, {"value": value}], ["value"], [{"type": "cast", "field": "value", "data_type": "decimal"}])
    assert invalid.value.fields["row_number"] == 2 and invalid.value.code == "data_processing_invalid"
    result = run_pipeline([{"value": value}], ["value"], [{"type": "cast", "field": "value", "data_type": "decimal", "on_error": "null"}])
    assert result["rows"] == [{"value": None}]
    assert result["steps"][0]["coerced_null_row_numbers"] == [1]


def test_derived_missing_zero_divisor_and_literal_text_are_explicit():
    expression = {"op": "divide", "args": [{"field": "n"}, {"field": "d"}]}
    result = run_pipeline([{"n": "1", "d": "0"}, {"n": "", "d": "2"}], ["n", "d"], [{"type": "derive", "output_field": "ratio", "expression": expression, "on_error": "null"}])
    assert [row["ratio"] for row in result["rows"]] == [None, None]
    result = run_pipeline([{"name": "批次"}], ["name"], [{"type": "derive", "output_field": "label", "expression": {"op": "concat", "args": [{"field": "name"}, {"value": " A "}]}}])
    assert result["rows"][0]["label"] == "批次 A "
    for operation in ({"type": "script", "script": "__import__('os')"}, {"type": "derive", "output_field": "x", "expression": "value * 2"}):
        with pytest.raises(DomainError):
            run_pipeline([{"value": "1"}], ["value"], [operation])


def test_aggregate_empty_missing_and_units_are_not_invented():
    specs = [{"name": "sum", "function": "sum", "field": "value"}, {"name": "count", "function": "count", "field": "value"}]
    result = run_pipeline([{"value": None}], ["value"], [{"type": "aggregate", "aggregations": specs}])
    assert result["rows"] == [{"sum": None, "count": 0}]
    assert result["steps"][0]["aggregation_stats"][0]["missing_count"] == 1
    assert run_pipeline([], ["value"], [{"type": "aggregate", "aggregations": specs}])["rows"] == [{"sum": None, "count": 0}]
    with pytest.raises(DomainError, match="计量单位"):
        run_pipeline([{"value": "1", "unit": "kg"}, {"value": "1", "unit": "g"}], ["value", "unit"], [{"type": "aggregate", "aggregations": [{"name": "total", "function": "sum", "field": "value", "unit_field": "unit"}]}])


def test_adaptive_arithmetic_preserves_large_adjacent_decimal_values():
    left = decimal_value("9007199254740993.000000000000000001")
    result = calculate("add", [left, decimal_value("0.000000000000000001")])
    assert decimal_text(result) == "9007199254740993.000000000000000002"
    assert decimal_text(calculate("round", [Decimal("1E100")], digits=2)) == "1" + "0" * 100
    assert typed("2026-10-21T08:00:00+08:00", "datetime").isoformat() == "2026-10-21T00:00:00+00:00"
    with pytest.raises(ValueError):
        typed("2026-10-21 08:00:00", "datetime")


def test_quality_rules_locate_all_bad_rows_and_report_typed_comparisons():
    rows = [{"id": "A", "weight": "1.000000000000000001", "state": "ok", "limit": "2", "code": "ABC"}, {"id": "A", "weight": "0.999999999999999999", "state": "bad", "limit": "0.5", "code": "A"}, {"id": "B", "weight": "wrong", "state": "", "limit": "2", "code": "ABCD"}]
    data = QualityInput(query_id="q", required_fields=["state"], unique_fields=["id"], rules=[{"type": "range", "field": "weight", "minimum": "1"}, {"type": "enum", "field": "state", "values": ["ok"]}, {"type": "type", "field": "weight", "data_type": "decimal"}, {"type": "length", "field": "code", "min_length": 2, "max_length": 3}, {"type": "compare", "field": "weight", "other_field": "limit", "data_type": "decimal", "operator": "lte"}]).model_dump()
    result = check_quality(rows, list(rows[0]), data)
    assert result["outcome"] == "failed" and result["rows_checked"] == 3
    checks = {check["rule"]: check for check in result["checks"]}
    assert checks["required"]["row_numbers"] == [3]
    assert checks["unique"]["row_numbers"] == [1, 2] and checks["unique"]["count"] == 1
    assert checks["unique"]["affected_count"] == 2
    assert checks["range"]["row_numbers"] == [2, 3]
    assert checks["enum"]["row_numbers"] == [2] and checks["enum"]["skipped_count"] == 1
    assert checks["type"]["row_numbers"] == [3]
    assert checks["length"]["row_numbers"] == [2, 3]
    assert checks["compare"]["row_numbers"] == [2, 3]
    assert checks["range"]["examples"][0]["value"] == "0.999999999999999999"


def test_quality_full_line_locations_have_explicit_example_limits_and_empty_scope():
    rows = [{"id": "same"} for _ in range(25)]
    result = check_quality(rows, ["id"], {"unique_fields": ["id"]})
    check = result["checks"][0]
    assert check["row_numbers"] == list(range(1, 26)) and len(check["examples"]) == 20 and check["examples_truncated"]
    assert check_quality([], ["id"], {"required_fields": ["id"]})["outcome"] == "incomplete"
    assert check_quality([{"id": "A"}], ["id"], {"required_fields": ["id"]}, truncated=True)["outcome"] == "incomplete"
    with pytest.raises(DomainError) as invalid:
        check_quality(rows, ["id"], {"rules": [{"type": "range", "field": "id", "minimum": "2", "maximum": "1"}]})
    assert invalid.value.code == "data_quality_rule_invalid"


def test_metric_ratio_uses_exact_values_and_never_turns_no_denominator_into_zero():
    definition = validate_definition({"version": 1, "kind": "ratio", "group_by": ["line"], "numerator": {"function": "sum", "field": "good"}, "denominator": {"function": "sum", "field": "total"}, "scale": "100", "unit": "%"})
    rows = [{"line": "A", "good": "9", "total": "10"}, {"line": "B", "good": "1", "total": "0"}, {"line": "C", "good": None, "total": None}]
    result = metric_result(rows, list(rows[0]), definition)
    assert result["rows"][0]["value"] == "90" and result["rows"][0]["unit"] == "%"
    assert result["rows"][1]["value"] is None and result["rows"][1]["status"] == "zero_denominator"
    assert result["rows"][2]["status"] == "missing_denominator"
    with pytest.raises(DomainError):
        validate_definition({"unit": "%", "formula": "good/total"})


def test_metric_grouped_physical_units_are_not_overwritten_by_display_unit():
    definition = validate_definition({"version": 1, "kind": "aggregate", "group_by": ["unit"], "aggregation": {"function": "sum", "field": "quantity", "unit_field": "unit"}})
    result = metric_result([{"unit": "kg", "quantity": "1"}, {"unit": "g", "quantity": "2"}], ["unit", "quantity"], definition)
    assert [row["unit"] for row in result["rows"]] == ["kg", "g"]
    assert [row["value"] for row in result["rows"]] == ["1", "2"]
    assert result["metric_unit_field"] == "metric_unit" and len(set(result["columns"])) == len(result["columns"])


@pytest.fixture
def controlled(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "processing-owned.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        actor = Actor("owner", "workspace", frozenset({"*"}), ("admin",), is_admin=True)
        settings = Settings(data_dir=tmp_path / "new-files")
        data = DataService(session, actor, settings)
        dataset = data.import_csv({"name": "本轮新批次", "csv_text": "batch,quantity,unit\nA,0.1,kg\nA,0.2,kg\n"})
        query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
        yield session, actor, data, dataset, query
        session.rollback()
    engine.dispose()


def test_saved_processing_metric_snapshots_export_and_revocation_do_not_requery(controlled, monkeypatch):
    session, actor, data, dataset, query = controlled
    from agent_platform.modules.data.router import export_processing
    processing = data.process({"name": "数量合计", "query_id": query["id"], "operations": [{"type": "aggregate", "group_by": ["unit"], "aggregations": [{"function": "sum", "field": "quantity", "name": "total", "unit_field": "unit"}]}]})
    assert processing["result"]["rows"] == [{"unit": "kg", "total": "0.3"}]
    definition = {"version": 1, "kind": "aggregate", "aggregation": {"function": "sum", "field": "quantity", "unit_field": "unit"}, "unit": "kg"}
    store = RecordStore(session, actor)
    metric = store.create("business_metric", {"name": "产量", "datasource_id": dataset["datasource_id"], "definition": definition})
    saved = MetricService(session, actor).execute(metric["id"], {"query_id": query["id"], "revision": metric["revision"]})
    assert saved["result"]["rows"][0]["value"] == "0.3"
    store.update("business_metric", metric["id"], {"definition": {**definition, "aggregation": {"function": "avg", "field": "quantity"}}}, metric["revision"])
    monkeypatch.setattr(DataService, "_path", lambda *args: pytest.fail("处理与指标历史不得重查源"))
    assert data.get_processing(saved["id"]).result["metric"]["definition"]["aggregation"]["function"] == "sum"
    assert data.visible_processing()["total"] == 2
    assert b"0.3" in export_processing(saved["id"], session, actor).body
    reader = DataService(session, Actor("reader", "workspace", frozenset({"m05.read", "m06.read"}), ("operator",)), data.settings)
    assert reader.get_processing(saved["id"]).id == saved["id"]
    source = data.get_source(dataset["datasource_id"])
    data.patch_source(source.id, {"revision": source.revision, "field_roles": {dataset["table_name"]: {"quantity": ["manager"]}}})
    with pytest.raises(DomainError):
        reader.get_processing(saved["id"])
    assert reader.visible_processing()["total"] == 0


def test_failed_pipeline_persists_no_partial_job_and_truncated_metric_refuses(controlled):
    session, actor, data, dataset, query = controlled
    before = session.scalar(select(func.count()).select_from(m.ProcessingJob))
    with pytest.raises(DomainError):
        data.process({"name": "失败处理", "query_id": query["id"], "operations": [{"type": "derive", "output_field": "value", "expression": {"op": "divide", "args": [{"field": "quantity"}, {"value": "0"}]}}]})
    assert session.scalar(select(func.count()).select_from(m.ProcessingJob)) == before
    stored = data.get_query(query["id"])
    stored.result = {**stored.result, "truncated": True}
    with pytest.raises(DomainError) as truncated:
        data.process({"name": "部分数据", "query_id": query["id"], "operations": [{"type": "drop_null", "field": "quantity"}]})
    assert truncated.value.code == "processing_input_truncated"
