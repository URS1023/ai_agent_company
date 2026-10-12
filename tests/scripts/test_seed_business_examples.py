"""Pure contract checks for the repeatable public-API business example.

中文说明：校验 scripts/seed_business_examples.py 里可重复执行的示例数据契约
（SQL 与请求体构造），保证示例脚本的输入结构稳定。
隔离方式：只导入并断言纯函数/常量，不发请求、不连数据库。
"""
from scripts.seed_business_examples import (
    VEHICLE_SQL,
    alert_device_payload,
    alert_rule_payload,
    application_payload,
    inspection_payloads,
    point_payloads,
    quality_metric_payloads,
    quality_template_payload,
)


def test_alert_example_maps_every_rule_field_to_the_real_vehicle_query():
    device = alert_device_payload("source-01")
    points = point_payloads("device-01")
    rule = alert_rule_payload("device-01", "advice-01")

    assert device["datasourceId"] == "source-01"
    assert device["code"].startswith("DEMO-")
    assert "t_cheliangxinxi1" in VEHICLE_SQL
    assert {item["fieldName"] for item in points} <= set(VEHICLE_SQL.replace(",", " ").split())
    assert rule["recordKeyField"] == "c_chepaihaoma"
    assert rule["rules"][0]["expression"]["op"] == "and"
    assert [item["op"] for item in rule["rules"][0]["expression"]["args"]] == ["eq", "gte"]


def test_quality_example_has_fixed_ordered_standards_and_two_real_outcomes():
    metrics = [
        {**payload, "id": f"metric-{index}", "version": 1}
        for index, payload in enumerate(quality_metric_payloads(), start=1)
    ]
    template = {**quality_template_payload([item["id"] for item in metrics]), "id": "template-01", "version": 1}
    device = {"id": "device-01"}
    inspections = inspection_payloads(template, device, metrics)

    assert template["metricIds"] == ["metric-1", "metric-2"]
    assert [item["sampleId"] for item in inspections] == ["DEMO-QC-PASS-001", "DEMO-QC-FAIL-001"]
    assert inspections[0]["observations"][0]["value"] == "0.21"
    assert inspections[0]["observations"][1]["defects"] == []
    assert inspections[1]["observations"][0]["value"] == "0.27"
    assert inspections[1]["observations"][1]["defects"][0]["severity"] == "severe"
    assert len({item["requestKey"] for item in inspections}) == 2


def test_application_example_has_one_entry_and_one_explicit_dependency():
    payload = application_payload("workflow-01", "datasource-01")
    bindings = payload["spec"]["bindings"]

    assert payload["code"] == "demo-equipment-quality-solution"
    assert [item["role"] for item in bindings].count("entry") == 1
    assert bindings[0] == {
        "type": "workflow",
        "id": "workflow-01",
        "role": "entry",
        "purpose": "真实车辆保养预警入口",
        "required": True,
    }
    assert bindings[1]["type"] == "datasource"
    assert bindings[1]["id"] == "datasource-01"
    assert payload["spec"]["inputSchema"]["additionalProperties"] is False
