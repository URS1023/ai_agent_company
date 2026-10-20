"""隔离文件验证制造业务闭环，不读取既有数据库或外部连接。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.assessment import assess
from agent_platform.modules.manufacturing.maintenance import analyze
from agent_platform.modules.manufacturing.schemas import AnalysisInput, InspectionImport, Metric, Observation, SourceAlertEvaluation
from agent_platform.modules.manufacturing.service import ManufacturingService
from agent_platform.modules.data.service import DataService
from agent_platform.modules.reports import models as report_models


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "manufacturing-test.sqlite3"
    engine = create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("test-user", "test-workspace", frozenset({"*"}), ("admin",))
    settings = Settings(data_dir=tmp_path / "isolated-data")
    with Session(engine) as session:
        yield ManufacturingService(session, actor), DataService(session, actor, settings), settings
        session.rollback()
    engine.dispose()


def test_decimal_and_missing_evidence_do_not_pass():
    metric = Metric(id="weight", name="重量", unit="g", standard_min="1.00000000000000000001", standard_max="2")
    assert assess(metric, Observation(metric_id="weight", value="1.00000000000000000000", unit="g"), {})["outcome"] == "failed"
    assert assess(metric, Observation(metric_id="weight", value="1.5", unit="kg"), {})["outcome"] == "review"
    assert assess(metric, None, {})["outcome"] == "incomplete"
    appearance = Metric(id="surface", name="外观", category="appearance")
    assert assess(appearance, Observation(metric_id="surface", image_ref="db/image/1", defects=[]), {})["outcome"] == "passed"
    assert assess(appearance, Observation(metric_id="surface", image_ref="db/image/1"), {})["outcome"] == "incomplete"


def test_database_import_quality_standard_snapshot_and_review(context):
    service, data, settings = context
    product = service.create(m.Product, {"name": "精密零件", "code": "P1"})
    template = service.create(m.InspectionTemplate, {"name": "尺寸检验", "target_type": "product", "metrics": [{"id": "size", "name": "尺寸", "unit": "mm", "standard_min": "9.9", "standard_max": "10.1"}]})
    inspection = service.create_inspection({"name": "首件检验", "target_type": "product", "product_id": product["id"], "template_id": template["id"], "template_revision": 1, "expected_samples": 2})
    changed = service.change(m.InspectionTemplate, template["id"], {"metrics": [{"id": "size", "name": "尺寸", "unit": "mm", "standard_min": "10.2", "standard_max": "10.4"}]}, 1)
    assert changed["revision"] == 2
    dataset = data.import_csv({"name": "数据库尺寸数据", "csv_text": "sample_id,size,unit\nS1,10.00,mm\nS2,10.20,mm\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
    imported = service.import_inspection(inspection["id"], InspectionImport(revision=1, query_id=query["id"], mappings=[{"metric_id": "size", "value_field": "size", "unit_field": "unit"}]), settings)
    result = service.run_inspection(inspection["id"], imported["revision"])
    service.session.expire_all()
    stored_run = service.session.get(m.InspectionRun, result["result"]["run_id"])
    assert stored_run.result["run_id"] == stored_run.id
    assert stored_run.result["execution_task_id"] == stored_run.task_id
    assert stored_run.result == result["result"]
    assert TaskService(service.session, service.actor).get(result["result"]["execution_task_id"])["status"] == "waiting_review"
    assert result["outcome"] == "failed"
    assert [s["outcome"] for s in result["result"]["samples"]] == ["passed", "failed"]
    assert result["standard_snapshot"]["metrics"][0]["standard_min"] == "9.9"
    assert result["result"]["samples"][0]["metrics"][0]["provenance"]["type"] == "query"
    reviewed = service.review(inspection["id"], {"revision": result["revision"], "outcome": "failed", "note": "尺寸超差，隔离待处理"})
    assert reviewed["inspection"]["status"] == "reviewed"
    assert TaskService(service.session, service.actor).get(result["result"]["execution_task_id"])["status"] == "succeeded"
    assert len(service.inspection_results(inspection["id"])["reviews"]) == 1
    with pytest.raises(DomainError) as conflict:
        service.review(inspection["id"], {"revision": result["revision"], "outcome": "passed", "note": "原始版本已过期"})
    assert conflict.value.code == "revision_conflict"


def test_sample_ownership_and_incomplete_cannot_review_passed(context):
    service, _, _ = context
    device = service.create(m.Device, {"name": "循环泵"})
    template = service.create(m.InspectionTemplate, {"name": "温度", "target_type": "device", "metrics": [{"id": "temp", "name": "温度", "unit": "℃", "standard_max": "80"}]})
    def inspection():
        return service.create_inspection({"name": "设备巡检", "target_type": "device", "device_id": device["id"], "template_id": template["id"], "template_revision": 1})
    one, two = inspection(), inspection()
    sample = service.add_sample(one["id"], {"external_ref": "A"})
    with pytest.raises(DomainError) as error:
        service.add_evidence(two["id"], {"revision": 1, "sample_id": sample["id"], "observations": []})
    assert error.value.code == "sample_task_mismatch"
    result = service.run_inspection(one["id"], sample["inspection_revision"])
    assert result["outcome"] == "incomplete"
    with pytest.raises(DomainError) as error:
        service.review(one["id"], {"revision": result["revision"], "outcome": "passed", "note": "缺少全部观测数据"})
    assert error.value.code == "missing_evidence"


def test_maintenance_matched_load_weighting_and_insufficient():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for period, measurements in [("baseline", [(20, 100)] * 6 + [(80, 200)] * 6), ("recent", [(20, 120)] * 6 + [(80, 240)] * 6)]:
        for load, value in measurements:
            rows.append({"timestamp": (origin + timedelta(minutes=len(rows))).isoformat(), "period": period, "load": load, "value": value})
    config = AnalysisInput(device_id="isolated", min_samples=5)
    result = analyze(rows, config)
    assert result["status"] == "attention"
    assert result["relative_change"] == pytest.approx(0.2)
    assert result["coverage"] == 1
    no_overlap = [dict(r, load=load + (100 if r["period"] == "recent" else 0)) for r in rows for load in [r["load"]]]
    assert analyze(no_overlap, config)["status"] == "insufficient_data"
    assert "failure_probability" not in result


def test_alert_dedup_and_state_transition(context):
    service, _, _ = context
    device = service.create(m.Device, {"name": "设备"})
    rule = service.create(m.AlertRule, {"name": "温度超限", "device_id": device["id"], "operator": "gt", "threshold": "80", "unit": "℃", "dedupe_minutes": 60})
    first = service.evaluate_alert(rule["id"], {"value": "81", "unit": "℃", "observed_at": "2026-01-01T00:00:00+00:00", "create_incident": True})
    second = service.evaluate_alert(rule["id"], {"value": "82", "unit": "℃", "observed_at": "2026-01-01T00:10:00+00:00", "create_incident": True})
    assert second["deduplicated"] is True
    assert second["incident"]["id"] == first["incident"]["id"]
    assert second["incident"]["occurrences"] == 2
    incident = second["incident"]
    resolved = service.incident_action(incident["id"], {"revision": incident["revision"], "action": "resolve", "note": "已现场检修"})
    assert resolved["status"] == "resolved"
    with pytest.raises(DomainError):
        service.incident_action(incident["id"], {"revision": resolved["revision"], "action": "acknowledge", "note": "重复受理"})


def test_new_evidence_invalidates_execution_but_keeps_run(context):
    service, _, _ = context
    device = service.create(m.Device, {"name": "泵"})
    template = service.create(m.InspectionTemplate, {"name": "温度", "target_type": "device", "metrics": [{"id": "temp", "name": "温度", "unit": "℃", "standard_max": "80"}]})
    inspection = service.create_inspection({"name": "巡检", "target_type": "device", "device_id": device["id"], "template_id": template["id"], "template_revision": 1})
    first = service.run_inspection(inspection["id"], 1)
    task_id = first["result"]["execution_task_id"]
    sample = service.add_sample(inspection["id"], {"external_ref": "新增样本"})
    assert sample["inspection_revision"] == first["revision"] + 1
    assert service.get(m.Inspection, inspection["id"]).result == {}
    assert TaskService(service.session, service.actor).get(task_id)["status"] == "cancelled"
    from sqlalchemy import select
    run = service.session.scalar(select(m.InspectionRun).where(m.InspectionRun.task_id == task_id))
    assert run.result["outcome"] == "incomplete"


def test_source_alert_reads_latest_query_and_repeated_poll_does_not_count_twice(context):
    service, data, settings = context
    device = service.create(m.Device, {"name": "数据库预警设备"})
    rule = service.create(m.AlertRule, {"name": "真实温度超限", "device_id": device["id"], "operator": "gt", "threshold": "80", "unit": "℃", "dedupe_minutes": 60})
    dataset = data.import_csv({"name": "告警观测", "csv_text": "timestamp,temperature,unit\n2026-10-20T00:00:00Z,70,℃\n2026-10-20T00:01:00Z,82,℃\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
    config = SourceAlertEvaluation(query_id=query["id"], value_field="temperature", time_field="timestamp", unit_field="unit")
    first = service.evaluate_source_alert(rule["id"], config, settings)
    assert first["value"] == "82" and first["matched"] is True
    assert first["incident"]["evidence"]["provenance"]["type"] == "query"
    repeat = service.evaluate_source_alert(rule["id"], config, settings)
    assert repeat["same_observation"] is True
    assert repeat["incident"]["occurrences"] == 1


def test_maintenance_does_not_mix_recipes_or_inflate_duplicate_samples():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"timestamp": (origin + timedelta(minutes=i)).isoformat(), "load": 50, "value": 10 if i < 5 else 20, "period": "baseline" if i < 5 else "recent", "recipe": "A" if i < 5 else "B"} for i in range(10)]
    config = AnalysisInput(device_id="isolated", condition_fields=["recipe"])
    assert analyze(rows, config)["status"] == "insufficient_data"
    same_recipe = [dict(row, recipe="A") for row in rows]
    assert analyze(same_recipe, config)["status"] == "attention"
    duplicated = [same_recipe[0]] * 5 + [same_recipe[5]] * 5
    result = analyze(duplicated, config)
    assert result["status"] == "insufficient_data"
    assert result["valid_count"] == 2 and result["duplicate_count"] == 8
