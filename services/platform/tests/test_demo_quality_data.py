"""食品演示样本、示意图和入库映射的纯文件/内存驱动验收。"""
from __future__ import annotations

from collections import Counter
import csv
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data import quality
from tools.demo_data.package import PackageError, Plan, batches
from agent_platform.modules.manufacturing.assessment import assess, overall
from agent_platform.modules.manufacturing.schemas import InspectionImport, Observation, TemplateInput
from test_demo_data_import import MemoryDatabase


def copy_fixture(tmp_path, mutate=None):
    rows = quality.fixture_rows()
    if mutate:
        mutate(rows)
    path = tmp_path / "食品产品质检.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=quality.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_platform_contract_has_three_dimensions_and_complete_source_mapping():
    template = TemplateInput.model_validate(quality.template_input())
    mapping = InspectionImport.model_validate(quality.inspection_import("owned-query", 3))
    assert Counter(metric.category for metric in template.metrics) == {"numeric": 1, "component": 2, "appearance": 1}
    assert {item.metric_id for item in mapping.mappings} == {metric.id for metric in template.metrics}
    assert mapping.sample_field == "sample_id" and mapping.revision == 3
    assert {metric.component_name for metric in template.metrics if metric.category == "component"} == {"蛋白质", "水分"}
    query = quality.query_input("owned-food-source", "frozen-import")
    assert query["parameters"] == {"import_id": "frozen-import"}
    assert "frozen-import" not in query["sql"] and "__aic_import_id`=:import_id" in query["sql"]


def test_sixteen_samples_exercise_real_existing_assessment_without_cv_or_models():
    template = TemplateInput.model_validate(quality.template_input())
    mapping = quality.inspection_import("owned-query", 1)["mappings"]
    outcomes, individual = {}, {}
    for row in quality.fixture_rows():
        metrics = []
        for metric, source in zip(template.metrics, mapping):
            observation = Observation(metric_id=metric.id,
                value=row[source["value_field"]] or None if source.get("value_field") else None,
                unit=source.get("unit", ""),
                defects=json.loads(row[source["defects_field"]]) if source.get("defects_field") else None,
                image_ref=row[source["image_ref_field"]] if source.get("image_ref_field") else "")
            result = assess(metric, observation, {"source_kind": "synthetic_demo_v1"})
            metrics.append(result["outcome"])
            individual[(row["sample_id"], metric.id)] = result["outcome"]
        outcomes[row["sample_id"]] = overall(metrics)
    assert Counter(outcomes.values()) == {"passed": 4, "failed": 6, "review": 1, "incomplete": 5}
    assert outcomes["DEMO-FD-QC-008"] == "failed"
    assert outcomes["DEMO-FD-QC-009"] == "passed"
    assert outcomes["DEMO-FD-QC-010"] == "review"
    assert outcomes["DEMO-FD-QC-016"] == "passed"
    assert individual[("DEMO-FD-QC-011", "net_weight")] == "incomplete"
    assert individual[("DEMO-FD-QC-012", "protein")] == "incomplete"
    assert individual[("DEMO-FD-QC-013", "moisture")] == "incomplete"
    assert individual[("DEMO-FD-QC-014", "appearance")] == "incomplete"
    assert individual[("DEMO-FD-QC-015", "appearance")] == "incomplete"


def test_local_svg_images_are_real_distinct_safe_diagrams_with_matching_boxes():
    namespace = {"s": "http://www.w3.org/2000/svg"}
    contents = []
    for name in quality.IMAGE_NAMES:
        path = quality.IMAGE_ROOT / f"demo/quality/{name}.svg"
        content = path.read_text(encoding="utf-8")
        contents.append(content)
        root = ET.fromstring(content)
        assert root.attrib["viewBox"] == "0 0 800 600"
        assert "合成演示" in root.find("s:title", namespace).text
        assert not re.search(r"<script|<foreignObject|\bon\w+\s*=|(?:href|url)\s*[=(]", content, re.I)
    assert len(set(contents)) == 5
    for row in quality.fixture_rows():
        for defect in json.loads(row["appearance_defects_json"]) or []:
            root = ET.parse(quality.IMAGE_ROOT / row["appearance_image_ref"].lstrip("/")).getroot()
            expected = tuple(round(value * scale, 5) for value, scale in zip(defect["bbox"], (800, 600, 800, 600)))
            rectangles = [tuple(float(element.attrib.get(key, 0)) for key in ("x", "y", "width", "height")) for element in root.findall(".//s:rect", namespace)]
            assert expected in rectangles


@pytest.mark.parametrize("mutate", [
    lambda rows: rows[0].update(protein_pct="NaN"),
    lambda rows: rows[0].update(data_origin="real_factory"),
    lambda rows: rows[0].update(appearance_image_ref="https://example.invalid/photo.jpg"),
    lambda rows: rows[0].update(appearance_defects_json='{"passed":true}'),
    lambda rows: rows[0].update(observed_at="2026-10-21T09:00:00"),
])
def test_invalid_values_and_unapproved_image_origins_are_rejected(tmp_path, mutate):
    with pytest.raises(PackageError):
        quality.build_quality_plan(copy_fixture(tmp_path, mutate))


def test_plan_and_batches_keep_missing_values_and_original_text(tmp_path):
    plan = quality.build_quality_plan(copy_fixture(tmp_path))
    assert plan.public()["rows"] == 16 and plan.public()["source_kind"] == "synthetic"
    assert json.loads(json.dumps(plan.public())) == plan.public()
    assert {(a.database, a.table) for a in plan.assets} == {(quality.DATABASE, quality.TABLE)}
    streamed = list(batches(plan, plan.assets[0]))
    assert len(streamed) == 1
    rows = streamed[0][0]
    fields = {name: index + 3 for index, name in enumerate(quality.FIELDS)}
    assert rows[10][fields["net_weight_g"]] == ""
    assert rows[14][fields["appearance_defects_json"]] == "null"
    assert rows[0][fields["appearance_defects_json"]] == "[]"
    assert rows[0][fields["observed_at"]].endswith("+08:00")


def importer(plan, tmp_path, database, receipt):
    return quality.QualityImporter(plan, proof_file=tmp_path / "proof.json", username="demo-test",
        password="secret-not-persisted", receipt_file=tmp_path / receipt,
        connection_factory=database.connect,
        identity_guard=lambda *_: {"server_uuid": "owned-local", "data_directory": str(tmp_path), "port": 3306},
        connected_guard=lambda cursor, _: cursor.execute("SELECT identity"))


def test_quality_import_uses_food_tables_only_and_existing_batch_idempotency(tmp_path):
    plan = quality.build_quality_plan(copy_fixture(tmp_path))
    database = MemoryDatabase()
    first = importer(plan, tmp_path, database, "first.json").run()
    assert first["status"] == "succeeded", first
    assert first["rows_committed_this_run"] == 16
    assert set(database.metadata) == {(quality.DATABASE, table) for table in (quality.TABLE, "aic_demo_imports", "aic_demo_batches")}
    for sql, _ in database.calls:
        if sql.startswith(("CREATE", "INSERT", "UPDATE")):
            assert sql.split("`", 2)[1] == quality.DATABASE
    before = database.inserts
    second = importer(plan, tmp_path, database, "second.json").run()
    assert second["status"] == "succeeded" and second["rows_committed_this_run"] == 0
    assert database.inserts == before


def test_quality_import_refuses_different_database_before_any_write(tmp_path):
    plan = quality.build_quality_plan(copy_fixture(tmp_path))
    bad = Plan(plan.package_root, (replace(plan.assets[0], database="szh_new"),))
    database = MemoryDatabase()
    result = importer(bad, tmp_path, database, "wrong.json").run()
    assert result["error"]["code"] == "quality_target_invalid"
    assert not any(sql.startswith(("CREATE", "INSERT", "UPDATE")) for sql, _ in database.calls)


def test_plan_cli_never_constructs_database_importer(tmp_path, monkeypatch, capsys):
    output = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["quality", "--plan-output", str(output)])
    monkeypatch.setattr(quality, "QualityImporter", lambda *_args, **_kwargs: pytest.fail("plan must not connect"))
    assert quality.main() == 0
    public = json.loads(output.read_text(encoding="utf-8"))
    assert public["rows"] == 16 and public == json.loads(capsys.readouterr().out)
