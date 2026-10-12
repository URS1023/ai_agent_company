"""Complete template round-trips, real bindings, revisions and scoped AI runs."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.models import Conversation, DataSource, Run, WorkspaceMember
from app.modules.reports.content import (
    builtin_templates,
    component_catalog,
    component_types,
    hifi_templates,
    lynx_report_catalog,
    template_skeleton,
    validate_content,
)
from app.modules.reports.generation import (
    _column_kind,
    _compact_schemas,
    _template_slots,
)
from app.modules.reports.models import (
    Report, ReportGeneration, ReportRevision, ReportSourceImport,
    ReportTemplateRevision, ReportTemplateUsage,
)
from app.modules.reports.source_reports import ensure_lynx_source_reports
from app.modules.reports.template_lock import template_visual_fingerprint, visual_fingerprint
from app.modules.system.models import Role
from app.providers import model
from test_analytics import source
from test_system_foundation import activate_user, create_role, create_user

REPORTS = "/api/v1/reports"
pytestmark = pytest.mark.usefixtures("approved_report_semantics")
TEMPLATES = "/api/v1/report-templates"
PROMPTS = "/api/v1/report-prompts"


def widget(source_id=None):
    result = {"id": "department-total", "component": "JBar", "componentName": "部门金额",
              "x": 32, "y": 80, "w": 560, "h": 320, "config": {}}
    if source_id:
        result["config"] = {
            "dataType": 3, "datasourceId": source_id,
            "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department",
            "drilldown": {"sql": "SELECT id, department, amount FROM sales WHERE department=:department ORDER BY id",
                         "parameter": "department", "triggerField": "department", "title": "部门明细"},
        }
    return result


def create_report(client, headers, **overrides):
    payload = {"reportName": "财务与设备大屏", "templateMode": "none",
               "content": {"canvas": {"width": 1920, "height": 1080}, "widgets": [widget()]}, **overrides}
    response = client.post(REPORTS, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def create_custom_layout_template(client, headers, name="自动选择布局"):
    response = client.post(TEMPLATES, headers=headers, json={
        "name": name,
        "category": "验收模板",
        "content": {
            "canvas": {"width": 1280, "height": 720, "backgroundColor": "#07111f"},
            "widgets": [{
                **widget(),
                "x": 48,
                "y": 96,
                "w": 760,
                "h": 420,
                "config": {"option": {"title": {"text": "部门金额"}}},
            }],
        },
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_visual_fingerprint_treats_browser_integral_numbers_as_the_same_geometry():
    source_content = {
        "canvas": {"width": 1920, "height": 1080, "styleLocked": True},
        "widgets": [{"id": "metric", "x": 640.0, "y": 0.0, "w": 320.0, "h": 180.0,
                     "component": "JStatsSummary", "config": {"fontSize": 18.5}}],
    }
    browser_content = deepcopy(source_content)
    browser_content["widgets"][0].update({"x": 640, "y": 0, "w": 320, "h": 180})
    assert visual_fingerprint(source_content) == visual_fingerprint(browser_content)
    browser_content["widgets"][0]["x"] = 641
    assert visual_fingerprint(source_content) != visual_fingerprint(browser_content)


def assert_chart_option_runtime_is_empty(option):
    if not isinstance(option, dict):
        return
    datasets = option.get("dataset") or []
    if isinstance(datasets, dict):
        datasets = [datasets]
    for dataset in datasets:
        if isinstance(dataset, dict) and "source" in dataset:
            assert dataset["source"] == []
    for axis_key in ("xAxis", "yAxis", "singleAxis", "parallelAxis"):
        axes = option.get(axis_key) or []
        if isinstance(axes, dict):
            axes = [axes]
        for axis in axes:
            if isinstance(axis, dict) and "data" in axis:
                assert axis["data"] == []
    legends = option.get("legend") or []
    if isinstance(legends, dict):
        legends = [legends]
    for legend in legends:
        if isinstance(legend, dict) and "data" in legend:
            assert legend["data"] == []
    series = option.get("series") or []
    if isinstance(series, dict):
        series = [series]
    for item in series:
        if not isinstance(item, dict):
            continue
        for key in ("data", "nodes", "links", "edges"):
            if key in item:
                assert item[key] == []


@pytest.mark.parametrize(
    "source_report",
    lynx_report_catalog()["reports"],
    ids=lambda row: "lynx-" + row["sourceRecordId"],
)
def test_every_lynx_report_keeps_one_visual_contract_when_runtime_data_is_empty(source_report):
    source_content = source_report["content"]
    empty_template = template_skeleton(source_content)
    source_widgets = source_content["widgets"]
    template_widgets = empty_template["widgets"]

    assert len(template_widgets) == len(source_widgets) == source_report["componentCount"]
    assert [item["id"] for item in template_widgets] == [item["id"] for item in source_widgets]
    assert [item.get("component") for item in template_widgets] == [item.get("component") for item in source_widgets]
    assert [
        (item.get("x"), item.get("y"), item.get("w"), item.get("h"), item.get("rotation"))
        for item in template_widgets
    ] == [
        (item.get("x"), item.get("y"), item.get("w"), item.get("h"), item.get("rotation"))
        for item in source_widgets
    ]
    assert template_visual_fingerprint(empty_template) == template_visual_fingerprint(source_content)
    assert empty_template["canvas"]["styleLocked"] is True
    assert empty_template["canvas"]["bindingContract"] == "data-only-v1"
    assert empty_template["canvas"]["templateVisualHash"] == template_visual_fingerprint(source_content)
    for widget_row in template_widgets:
        assert not any(key in widget_row for key in (
            "data", "value", "rawData", "datasourceId", "query", "generatedSql", "queryId",
        ))
        config = widget_row.get("config") or {}
        assert "sql" not in config and "datasourceId" not in config and "queryId" not in config
        assert config.get("chartData") == "[]" and config.get("chartDataParsed") == []
        assert_chart_option_runtime_is_empty(widget_row.get("echartsOption"))
        assert_chart_option_runtime_is_empty(config.get("option"))


def test_component_catalog_distinguishes_111_renderers_from_24_category_identifiers(
    client, administrator,
):
    catalog = component_catalog()
    meta = catalog["meta"]
    source_types = set(meta["sourceRenderableLeafTypeNames"])
    category_only_types = set(meta["sourceCategoryOnlyTypeNames"])
    toolbar_types = {
        item["compType"]
        for group in catalog["toolbarGroups"]
        for category in group["categories"]
        for item in category["items"]
    }
    business_group = next(group for group in catalog["toolbarGroups"] if group["key"] == "business")
    business_types = {
        item["compType"]
        for category in business_group["categories"]
        for item in category["items"]
    }

    assert meta["sourceDistinctTypeIdentifiers"] == 135
    assert meta["sourceCategoryTypeIdentifiers"] == 25
    assert meta["sourceCategoryOnlyTypeIdentifiers"] == len(category_only_types) == 24
    assert meta["sourceRenderableLeafRecords"] == 264
    assert meta["sourceRenderableLeafTypes"] == len(source_types) == 111
    assert meta["primaryLeafCount"] == 133
    assert meta["primaryRenderableLeafTypes"] == 98
    assert meta["supplementalLeafRecords"] == 16
    assert meta["supplementalRenderableLeafTypes"] == len(business_types) == 13
    assert len(catalog["toolbarGroups"]) == meta["toolbarGroupCount"] == 9
    assert source_types == toolbar_types
    assert source_types.issubset(component_types())
    assert category_only_types.isdisjoint(component_types())
    assert business_types == set(meta["supplementalRenderableLeafTypeNames"])

    for component in source_types:
        validated = validate_content({
            "canvas": {"width": 1920, "height": 1080},
            "widgets": [{
                "id": f"catalog-{component}",
                "component": component,
                "x": 0,
                "y": 0,
                "w": 320,
                "h": 180,
                "config": {},
            }],
        })
        assert validated["widgets"][0]["component"] == component

    with pytest.raises(ValidationError):
        validate_content({
            "widgets": [{
                "id": "category-is-not-a-widget",
                "component": "chart",
                "x": 0,
                "y": 0,
                "w": 320,
                "h": 180,
                "config": {},
            }],
        })

    response = client.get(REPORTS + "/component-catalog", headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["meta"] == meta


@pytest.mark.parametrize(
    "template", [item for item in builtin_templates() if item.get("enabled", True)],
    ids=lambda row: row["id"],
)
def test_all_enabled_templates_preserve_every_component(client, administrator, template):
    response = client.post(REPORTS, headers=administrator, json={
        "reportName": template["name"], "templateMode": "manual", "templateId": template["id"],
    })
    assert response.status_code == 201, response.text
    record = response.json()["data"]
    content = json.loads(record["content"])
    assert content == template["content"]
    assert len(content["widgets"]) == template["componentCount"]
    assert record["width"] == content["canvas"]["width"]
    assert set(item["component"] for item in content["widgets"]).issubset(component_types())
    reopened = client.get(REPORTS + "/" + record["id"], headers=administrator)
    assert reopened.json()["data"]["content"] == record["content"]
    assert record["templateId"] == template["id"] and record["templateMode"] == "manual"


def test_no_default_template_and_invalid_modes_do_not_create_reports(client, administrator, db):
    assert client.post(REPORTS, headers=administrator, json={"reportName": "缺少选择", "templateMode": "manual"}).status_code == 422
    assert client.post(REPORTS, headers=administrator, json={"reportName": "自动不能首项兜底", "templateMode": "auto"}).status_code == 422
    assert client.post(REPORTS, headers=administrator, json={
        "reportName": "不能隐藏选中", "templateMode": "none", "templateId": "not-a-template",
    }).status_code == 422
    assert db.scalar(select(Report.id)) is None
    response = client.post(REPORTS, headers=administrator, json={"reportName": "空白"})
    assert response.status_code == 201, response.text
    record = response.json()["data"]
    assert record["templateId"] is None and record["templateMode"] == "none"
    assert json.loads(record["content"])["widgets"] == []


def test_current_lynx_catalog_populates_real_reports_and_visual_source_templates(
    client, administrator,
):
    catalog = lynx_report_catalog()
    reports = catalog["reports"]
    source_templates = builtin_templates()
    assert catalog["schemaVersion"] == "test5.lynx-source-reports/v2"
    assert catalog["sourceRecordCount"] == catalog["reportCount"] == 33
    assert catalog["enabledReportCount"] == 33
    assert catalog["duplicateNameCount"] == 3
    assert len(reports) == 33
    assert len(source_templates) == 33
    assert len({item["id"] for item in source_templates}) == 33
    assert {item["sourceRecordId"] for item in reports} == {
        item["sourceId"] for item in catalog["records"]
    }
    assert {item["sourceRecordId"] for item in source_templates} == {
        item["sourceRecordId"] for item in reports
    }
    assert all(item["sourceKind"] == "lynx-report-screen" for item in reports)
    assert all(item["sourceKind"] == "lynx-report-screen" for item in source_templates)
    assert all(item["builtin"] and item["styleLocked"] for item in source_templates)
    assert all(item["bindingContract"] == "data-only-v1" for item in source_templates)
    assert all(
        item["id"] == f"lynx-source-template-{item['sourceRecordId']}"
        for item in source_templates
    )
    assert all(
        item["preview"] == (
            "/assets/analytics/jimu-screen/report-thumbnails/"
            f"lynx-{item['sourceRecordId']}.png"
        )
        for item in source_templates
    )
    assert all(item["visualHash"] == visual_fingerprint(item["content"]) for item in reports)
    assert all(
        {widget["component"] for widget in item["content"]["widgets"]}.issubset(component_types())
        for item in reports
    )
    assert sum(item["componentCount"] for item in reports) == 784
    assert sum(item["componentCount"] for item in source_templates) == 784
    assert sum(item["sqlWidgetCount"] for item in reports) == 165
    assert sum(item["dataSlotCount"] for item in reports) == 548
    reports_by_source_id = {item["sourceRecordId"]: item for item in reports}
    for template in source_templates:
        source_report = reports_by_source_id[template["sourceRecordId"]]
        assert template_visual_fingerprint(template["content"]) == template_visual_fingerprint(
            source_report["content"],
        )
        assert template["visualHash"] == template_visual_fingerprint(template["content"])
        assert template["content"]["canvas"]["templateSourceReportId"] == (
            f"lynx-report-screen:{template['sourceRecordId']}"
        )

    preview_root = (
        Path(__file__).resolve().parents[2]
        / "apps" / "web" / "assets" / "analytics" / "jimu-screen" / "report-thumbnails"
    )
    assert all(
        (preview_root / f"lynx-{item['sourceRecordId']}.png").is_file()
        for item in source_templates
    )

    response = client.get(REPORTS + "/source-catalog", headers=administrator)
    assert response.status_code == 200, response.text
    evidence = response.json()["data"]
    assert evidence["reportCount"] == evidence["enabledReportCount"] == 33
    assert len(evidence["records"]) == 33
    assert evidence["catalogSha256"] == catalog["catalogSha256"]
    template_response = client.get(TEMPLATES, headers=administrator)
    assert template_response.status_code == 200, template_response.text
    listed_templates = template_response.json()["data"]
    hifi_templates_list = [item for item in listed_templates if item["id"].startswith("hifi-template-")]
    assert len(hifi_templates_list) == 20
    assert len(listed_templates) == 20
    assert client.get(TEMPLATES + "/lynx-source-catalog", headers=administrator).status_code == 404


def test_hifi_reference_catalog_is_exactly_twenty_editable_data_templates(
    client, administrator,
):
    """参考目录只暴露 20 套模板；视觉锁定而数据结构仍可绑定。"""

    templates = hifi_templates()
    assert len(templates) == 20
    assert [item["id"] for item in templates] == [f"hifi-template-{index:02d}" for index in range(1, 21)]
    preview_root = Path(__file__).resolve().parents[2] / "apps" / "web" / "assets" / "analytics"
    for item in templates:
        assert item["sourceKind"] == "hifi-reference"
        assert item["styleLocked"] is True
        assert item["content"]["canvas"]["styleLocked"] is True
        assert (preview_root / item["preview"].removeprefix("/assets/analytics/")).is_file()
        assert {widget["component"] for widget in item["content"]["widgets"]}.issubset(component_types())
        validate_content(item["content"])

    listed = client.get(TEMPLATES, headers=administrator)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [item["id"] for item in templates]

    template_id = templates[0]["id"]
    deleted = client.delete(TEMPLATES + "/" + template_id, headers=administrator)
    assert deleted.status_code == 200, deleted.text
    active_ids = {item["id"] for item in client.get(TEMPLATES, headers=administrator).json()["data"]}
    assert template_id not in active_ids
    recycle = client.get(TEMPLATES + "?includeDeleted=true", headers=administrator)
    assert recycle.status_code == 200, recycle.text
    deleted_view = next(item for item in recycle.json()["data"] if item["id"] == template_id)
    assert deleted_view["deleted"] is True

    recovered = client.post(TEMPLATES + "/" + template_id + "/recover", headers=administrator)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["data"]["styleLocked"] is True


def test_lynx_source_template_can_be_deleted_and_recovered_without_mutating_source(
    client, administrator,
):
    source_template = builtin_templates()[0]
    source_snapshot = deepcopy(source_template)

    deleted = client.delete(TEMPLATES + "/" + source_template["id"], headers=administrator)
    assert deleted.status_code == 200, deleted.text
    assert len(client.get(TEMPLATES, headers=administrator).json()["data"]) == 20
    recycle_bin = client.get(
        TEMPLATES + "?includeDeleted=true", headers=administrator,
    ).json()["data"]
    deleted_view = next(item for item in recycle_bin if item["id"] == source_template["id"])
    assert deleted_view["deleted"] is True
    assert deleted_view["content"] == source_snapshot["content"]

    recovered = client.post(
        TEMPLATES + "/" + source_template["id"] + "/recover", headers=administrator,
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["data"]["deleted"] is False
    assert len(client.get(TEMPLATES, headers=administrator).json()["data"]) == 20
    assert builtin_templates()[0] == source_snapshot


def test_direct_lynx_import_waits_for_variantorm_instead_of_breaking_bootstrap(
    administrator, db,
):
    owner = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == administrator["X-Workspace-Id"],
        WorkspaceMember.role == "admin",
    ))
    result = ensure_lynx_source_reports(
        db, administrator["X-Workspace-Id"], owner.user_id,
    )
    assert result["sourceCount"] == 33
    assert result["createdCount"] == 0
    assert result["skippedReason"] == "variantorm_datasource_missing"


def test_current_lynx_interface_records_materialize_as_editable_idempotent_reports(
    client, administrator, db, source,
):
    workspace_id = administrator["X-Workspace-Id"]
    owner = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.role == "admin",
    ))
    datasource = db.get(DataSource, source["id"])
    datasource.name = "variantorm"
    db.commit()

    first = ensure_lynx_source_reports(db, workspace_id, owner.user_id)
    db.commit()
    catalog = lynx_report_catalog()
    sources = {item["sourceRecordId"]: item for item in catalog["reports"]}
    assert first["sourceCount"] == first["createdCount"] == 33
    assert first["existingCount"] == first["deletedCount"] == 0

    mappings = db.scalars(select(ReportSourceImport).where(
        ReportSourceImport.workspace_id == workspace_id,
    )).all()
    assert len(mappings) == 33
    assert len({item.source_record_id for item in mappings}) == 33
    assert len({item.report_id for item in mappings}) == 33
    assert all(item.source_kind == "lynx-report-screen" for item in mappings)
    assert all(item.source_template_id == "" for item in mappings)
    assert all("Authorization" not in item.source_endpoint for item in mappings)

    reports = db.scalars(select(Report).where(Report.workspace_id == workspace_id)).all()
    assert len(reports) == 33
    reports_by_id = {item.id: item for item in reports}
    for mapping in mappings:
        report = reports_by_id[mapping.report_id]
        source_report = sources[mapping.source_record_id]
        assert report.name == source_report["name"]
        assert report.template_id is None and report.template_mode == "none"
        assert report.status == "draft"
        assert report.datasource_ids == [datasource.id]
        assert visual_fingerprint(report.content) == source_report["visualHash"]
        assert report.lineage["entry"] == "lynx_interface_import"
        assert report.lineage["sourceReportId"] == mapping.source_record_id
        assert report.lineage["preserveSourceWidgets"] is True
        assert report.lineage["styleLocked"] is False
        assert report.thumbnail.endswith(f"/lynx-{mapping.source_record_id}.png")

    assert len(db.scalars(select(ReportRevision).where(
        ReportRevision.workspace_id == workspace_id,
        ReportRevision.reason == "智慧云脑 Cloud Brain 接口报表导入",
    )).all()) == 33
    assert db.scalars(select(ReportTemplateUsage).where(
        ReportTemplateUsage.workspace_id == workspace_id,
    )).all() == []
    listed_templates = client.get(TEMPLATES, headers=administrator).json()["data"]
    hifi_templates_list = [item for item in listed_templates if item["id"].startswith("hifi-template-")]
    assert len(hifi_templates_list) == 20

    listed = client.get(REPORTS + "/list", headers=administrator)
    assert listed.status_code == 200, listed.text
    listed_rows = listed.json()["data"]
    assert len(listed_rows) == 33
    assert {item["lineage"]["sourceReportId"] for item in listed_rows} == set(sources)

    # Imported reports preserve the source canvas at first load but are real,
    # editable reports: a geometry update must survive an API round trip.
    editable = next(item for item in listed_rows if any(
        not widget.get("locked")
        for widget in json.loads(item["content"])["widgets"]
    ))
    content = json.loads(editable["content"])
    target = next(widget for widget in content["widgets"] if not widget.get("locked"))
    original_x = target["x"]
    target["x"] = original_x + 7
    edited = client.put(REPORTS + "/" + editable["id"], headers=administrator, json={
        "reportName": editable["reportName"],
        "content": content,
        "version": editable["version"],
    })
    assert edited.status_code == 200, edited.text
    reopened = client.get(REPORTS + "/" + editable["id"], headers=administrator).json()["data"]
    persisted = next(
        widget for widget in json.loads(reopened["content"])["widgets"]
        if widget["id"] == target["id"]
    )
    assert persisted["x"] == original_x + 7

    second = ensure_lynx_source_reports(db, workspace_id, owner.user_id)
    db.commit()
    assert second["sourceCount"] == second["existingCount"] == 33
    assert second["createdCount"] == second["deletedCount"] == 0
    assert len(db.scalars(select(Report).where(Report.workspace_id == workspace_id)).all()) == 33

    deleted_id = mappings[0].report_id
    deleted = client.delete(REPORTS + "/" + deleted_id, headers=administrator)
    assert deleted.status_code == 200, deleted.text
    db.expire_all()
    third = ensure_lynx_source_reports(db, workspace_id, owner.user_id)
    db.commit()
    assert third["createdCount"] == 0 and third["existingCount"] == 33
    assert third["deletedCount"] == 1
    assert db.get(Report, deleted_id).status == "deleted"
    assert len(client.get(REPORTS + "/list", headers=administrator).json()["data"]) == 32


def test_full_save_noop_save_conflict_and_restore_keep_revisions(client, administrator, db):
    record = create_report(client, administrator)
    content = json.loads(record["content"])
    content["widgets"][0]["x"] = 192
    edited = client.put(REPORTS + "/" + record["id"], headers=administrator, json={
        "reportName": record["reportName"], "content": content, "version": record["version"],
    })
    assert edited.status_code == 200, edited.text
    version = edited.json()["data"]["version"]
    assert version == 2
    unchanged = client.put(REPORTS + "/" + record["id"], headers=administrator, json={
        "reportName": record["reportName"], "content": content, "version": version,
    })
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["data"]["version"] == 3
    stale = client.put(REPORTS + "/" + record["id"], headers=administrator, json={
        "reportName": "不能覆盖", "version": version,
    })
    assert stale.status_code == 409
    restored = client.post(REPORTS + "/" + record["id"] + "/restore", headers=administrator,
                           json={"version": 3, "revision": 1})
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["version"] == 4
    assert json.loads(restored.json()["data"]["content"])["widgets"][0]["x"] == 32
    revisions = client.get(REPORTS + "/" + record["id"] + "/revisions", headers=administrator).json()["data"]
    assert [item["revision"] for item in revisions] == [4, 3, 2, 1]
    assert client.delete(REPORTS + "/" + record["id"], headers=administrator).status_code == 200
    assert client.get(REPORTS + "/" + record["id"], headers=administrator).status_code == 404
    assert len(db.scalars(select(ReportRevision).where(ReportRevision.report_id == record["id"])).all()) == 5


def test_saved_bindings_and_drilldown_work_for_viewer_without_sql_execution(client, administrator, source):
    record = create_report(client, administrator, content={
        "widgets": [widget(source["id"])], "canvas": {"width": 1920, "height": 1080},
    })
    role = create_role(client, administrator, "dashboard_viewer", ["home.view", "dashboards.view", "datasources.view"], "workspace")
    create_user(client, administrator, [role["id"]])
    viewer = activate_user(client)
    saved = client.get(REPORTS + "/" + record["id"] + "/widgets/department-total/data", headers=viewer)
    assert saved.status_code == 200, saved.text
    assert {row["department"]: row["total"] for row in saved.json()["data"]["data"]} == {"设备": 900, "财务": 200}
    assert client.post(REPORTS + "/query", headers=viewer, json={
        "datasourceId": source["id"], "query": "SELECT * FROM sales", "mode": "sql",
    }).status_code == 403
    result = client.post(REPORTS + "/drilldown", headers=viewer, json={
        "reportId": record["id"], "widgetId": "department-total", "point": {"name": "设备"},
    })
    assert result.status_code == 200, result.text
    assert [row["amount"] for row in result.json()["data"]["data"]] == [100, 300, 500]
    injection = client.post(REPORTS + "/drilldown", headers=viewer, json={
        "reportId": record["id"], "widgetId": "department-total",
        "parameters": {"department": "' OR 1=1 --"},
    })
    assert injection.status_code == 200 and injection.json()["data"]["data"] == []
    assert client.post(REPORTS + "/drilldown", headers=viewer, json={
        "reportId": record["id"], "widgetId": "department-total", "sql": "SELECT * FROM restricted_data",
    }).status_code == 422


def test_revisions_cannot_bypass_revoked_source_scope(client, administrator, source):
    record = create_report(client, administrator, content={"widgets": [widget(source["id"])]})
    # Replace bound data with an empty layout; previous revision still has source data.
    replacement = client.put(REPORTS + "/" + record["id"], headers=administrator, json={
        "reportName": record["reportName"], "version": 1, "datasourceIds": [], "datasourceId": None,
        "content": {"widgets": []},
    })
    assert replacement.status_code == 200, replacement.text
    role = create_role(client, administrator, "no_source_viewer", ["home.view", "dashboards.view"], "workspace")
    create_user(client, administrator, [role["id"]])
    viewer = activate_user(client)
    revisions = client.get(REPORTS + "/" + record["id"] + "/revisions", headers=viewer)
    assert revisions.status_code == 200, revisions.text
    rows = revisions.json()["data"]
    assert rows[0]["available"] and rows[0]["snapshot"] is not None
    assert rows[1]["available"] is False and rows[1]["snapshot"] is None


def test_custom_template_is_data_free_and_disable_does_not_break_existing_report(client, administrator, source):
    base = widget(source["id"])
    base["config"].update({"chartData": '[{"amount":123456789}]', "headers": {"Authorization": "private"},
                           "option": {"series": [{"data": [123456789]}], "password": "private"}})
    response = client.post(TEMPLATES, headers=administrator, json={"name": "部门模板", "content": {"widgets": [base]}})
    assert response.status_code == 201, response.text
    template = response.json()["data"]
    serialized = json.dumps(template["content"])
    assert "123456789" not in serialized and "private" not in serialized and "datasourceId" not in serialized
    record = client.post(REPORTS, headers=administrator, json={
        "reportName": "已建报表", "templateMode": "manual", "templateId": template["id"],
    }).json()["data"]
    assert client.delete(TEMPLATES + "/" + template["id"], headers=administrator).status_code == 200
    renamed = client.put(REPORTS + "/" + record["id"], headers=administrator, json={"reportName": "仍可编辑", "version": 1})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["templateId"] == template["id"]
    assert client.post(REPORTS, headers=administrator, json={
        "reportName": "不能使用停用模板新建", "templateMode": "manual", "templateId": template["id"],
    }).status_code == 422


def test_confirmed_report_becomes_locked_template_and_empty_instance_is_visually_identical(
    client, administrator, source, db,
):
    workspace_id = administrator["X-Workspace-Id"]
    owner = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.role == "admin",
    ))
    datasource = db.get(DataSource, source["id"])
    datasource.name = "variantorm"
    db.commit()
    ensure_lynx_source_reports(db, workspace_id, owner.user_id)
    db.commit()
    mapping = db.scalar(select(ReportSourceImport).where(
        ReportSourceImport.workspace_id == workspace_id,
        ReportSourceImport.source_record_id == "29",
    ))
    original = db.get(Report, mapping.report_id)
    original_content = deepcopy(original.content)

    promoted_response = client.post(
        TEMPLATES + "/from-report/" + original.id,
        headers=administrator,
        json={"name": "经营驾驶舱锁定模板", "category": "经营分析", "tags": ["经营", "真实看板"]},
    )
    assert promoted_response.status_code == 201, promoted_response.text
    template = promoted_response.json()["data"]
    assert template["styleLocked"] is True
    assert template["bindingContract"] == "data-only-v1"
    assert template["sourceReportId"] == original.id
    assert template["sourceReportVersion"] == original.version
    assert template["preview"] == original.thumbnail
    assert template["componentCount"] == len(original_content["widgets"])
    assert template["visualHash"] == template_visual_fingerprint(original_content)
    assert original.content == original_content

    instance_response = client.post(REPORTS, headers=administrator, json={
        "reportName": "经营驾驶舱空数据验收",
        "templateMode": "manual",
        "templateId": template["id"],
    })
    assert instance_response.status_code == 201, instance_response.text
    instance = instance_response.json()["data"]
    instance_content = json.loads(instance["content"])
    assert instance_content == template["content"]
    assert instance["datasourceIds"] == []
    assert instance["width"] == original.width and instance["height"] == original.height
    assert instance["backgroundColor"] == template["content"]["canvas"].get("backgroundColor", "#07111f")
    assert instance["thumbnail"] == original.thumbnail
    assert template_visual_fingerprint(instance_content) == template_visual_fingerprint(original_content)
    assert instance["lineage"]["templateBindingContract"] == "data-only-v1"
    assert instance["lineage"]["templateSourceReportId"] == original.id


def test_designer_can_move_template_instance_without_changing_template(client, administrator):
    template = create_custom_layout_template(client, administrator, "可编辑模板实例")
    original = deepcopy(template["content"])
    instance = client.post(REPORTS, headers=administrator, json={
        "reportName": "拖动验收", "templateMode": "manual", "templateId": template["id"],
    }).json()["data"]
    moved = json.loads(instance["content"])
    moved["widgets"][0].update({"x": 180, "y": 220, "w": 680, "h": 300})
    saved = client.put(f"{REPORTS}/{instance['id']}", headers=administrator, json={
        "reportName": instance["reportName"], "version": instance["version"],
        "content": moved, "editTemplateLayout": True,
    })
    assert saved.status_code == 200, saved.text
    result = saved.json()["data"]
    assert result["lineage"]["layoutCustomized"] is True
    assert result["templateId"] == template["id"]
    restored = client.get(f"{REPORTS}/{instance['id']}", headers=administrator).json()["data"]
    widget = json.loads(restored["content"])["widgets"][0]
    assert (widget["x"], widget["y"], widget["w"], widget["h"]) == (180, 220, 680, 300)
    assert json.loads(restored["content"])["canvas"]["styleLocked"] is False
    # Metadata-only saves must not reapply the old template geometry.
    renamed = client.put(f"{REPORTS}/{instance['id']}", headers=administrator, json={
        "reportName": "已移动并重命名", "version": restored["version"],
    })
    assert renamed.status_code == 200, renamed.text
    assert client.get(f"{TEMPLATES}/{template['id']}", headers=administrator).json()["data"]["content"] == original


def test_locked_template_accepts_customer_requirement_json_and_model_returns_bindings_only(
    client, administrator, source, monkeypatch, db,
):
    template = create_custom_layout_template(client, administrator, "客户需求 JSON 模板")
    slot = _template_slots(template)[0]
    contexts = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[-1]["content"])
        contexts.append(context)
        return {"content": json.dumps({
            "title": "华北经营分析",
            "templateId": template["id"],
            "bindings": [{
                "widgetId": slot["id"],
                "datasourceId": source["id"],
                "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department",
                "parameters": {},
            }],
        })}

    monkeypatch.setattr(model, "chat_for_run", respond)
    requirement_document = {
        "schemaVersion": "dashboard-requirement-v1",
        "objective": "分析华北区域的财务与设备金额",
        "metrics": ["金额合计"],
        "dimensions": ["部门"],
        "filters": ["区域=华北"],
        "timeRange": "全部可用数据",
    }
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "requirementDocument": requirement_document,
        "templateMode": "manual",
        "templateId": template["id"],
        "datasourceId": source["id"],
        "width": template["content"]["canvas"]["width"],
        "height": template["content"]["canvas"]["height"],
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert len(contexts) == 1
    assert contexts[0]["customerRequirement"] == {
        **requirement_document,
        "notes": "",
    }
    assert result["templateId"] == template["id"]
    assert result["templateVisualHash"] == visual_fingerprint(template["content"])
    assert visual_fingerprint({"canvas": result["canvas"], "widgets": result["widgets"]}) == visual_fingerprint(template["content"])
    assert result["widgets"][0]["dataStatus"] == "verified"
    generation = db.get(ReportGeneration, result["generationId"])
    assert generation.request_json["customer_requirement"] == contexts[0]["customerRequirement"]


def test_template_versions_publish_usage_and_verified_import_export(client, administrator, db):
    initial_content = {
        "canvas": {"width": 1920, "height": 1080},
        "widgets": [{
            **widget(),
            "config": {
                "dataType": 3,
                "datasourceId": "private-source",
                "sql": "SELECT secret FROM private_table",
                "headers": {"Authorization": "private-token"},
                "chartData": '[{"secret":123456789}]',
            },
        }],
    }
    created_response = client.post(TEMPLATES, headers=administrator, json={
        "name": "可治理销售模板",
        "description": "版本、交换与使用记录验收",
        "category": "经营分析",
        "tags": ["销售", "治理"],
        "content": initial_content,
    })
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()["data"]
    assert created["version"] == 1 and created["revisionCount"] == 1 and created["usageCount"] == 0
    assert db.scalar(select(ReportTemplateRevision).where(
        ReportTemplateRevision.template_id == created["id"],
    )).reason == "新建模板"

    changed_content = deepcopy(created["content"])
    changed_content["widgets"][0]["x"] = 88
    updated_response = client.put(TEMPLATES + "/" + created["id"], headers=administrator, json={
        "name": "可治理销售模板 v2",
        "description": created["description"],
        "category": created["category"],
        "tags": created["tags"],
        "content": changed_content,
        "enabled": True,
        "version": created["version"],
    })
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()["data"]
    assert updated["version"] == 2 and updated["revisionCount"] == 2
    revisions = client.get(TEMPLATES + "/" + created["id"] + "/revisions", headers=administrator).json()["data"]
    assert [item["revision"] for item in revisions] == [2, 1]
    assert revisions[0]["reason"] == "编辑模板" and revisions[0]["restorable"] is True

    exported_response = client.get(TEMPLATES + "/" + created["id"] + "/export", headers=administrator)
    assert exported_response.status_code == 200, exported_response.text
    package = exported_response.json()["data"]
    serialized = json.dumps(package, ensure_ascii=False)
    assert package["schemaVersion"] == "report-template-package-v1"
    assert "private-source" not in serialized and "private-token" not in serialized
    assert "private_table" not in serialized and "123456789" not in serialized

    tampered = deepcopy(package)
    tampered["template"]["name"] = "被篡改的名称"
    rejected = client.post(TEMPLATES + "/import", headers=administrator, json=tampered)
    assert rejected.status_code == 422
    assert "校验失败" in rejected.text

    imported_response = client.post(TEMPLATES + "/import", headers=administrator, json=package)
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()["data"]
    assert imported["id"] != created["id"] and imported["version"] == 1
    assert imported["revisionCount"] == 1 and imported["content"] == package["template"]["content"]

    report_response = client.post(REPORTS, headers=administrator, json={
        "reportName": "模板真实使用大屏", "templateMode": "manual", "templateId": created["id"],
    })
    assert report_response.status_code == 201, report_response.text
    report = report_response.json()["data"]
    usage = client.get(TEMPLATES + "/" + created["id"] + "/usage", headers=administrator).json()["data"]
    assert len(usage) == 1 and usage[0]["reportId"] == report["id"] and usage[0]["templateVersion"] == 2
    listed = next(item for item in client.get(TEMPLATES, headers=administrator).json()["data"] if item["id"] == created["id"])
    assert listed["usageCount"] == 1 and listed["lastUsedAt"]
    assert db.scalar(select(ReportTemplateUsage).where(ReportTemplateUsage.report_id == report["id"])) is not None

    disabled_response = client.delete(TEMPLATES + "/" + created["id"], headers=administrator)
    assert disabled_response.status_code == 200, disabled_response.text
    disabled = next(
        item for item in client.get(TEMPLATES + "?includeDeleted=true", headers=administrator).json()["data"]
        if item["id"] == created["id"]
    )
    assert disabled["enabled"] is False and disabled["version"] == 3 and disabled["revisionCount"] == 3
    assert client.post(TEMPLATES + "/" + created["id"] + "/publish", headers=administrator, json={"version": 2}).status_code == 409
    published_response = client.post(TEMPLATES + "/" + created["id"] + "/publish", headers=administrator, json={"version": 3})
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()["data"]
    assert published["enabled"] is True and published["version"] == 4

    restored_response = client.post(TEMPLATES + "/" + created["id"] + "/restore", headers=administrator, json={
        "version": published["version"], "revision": 1,
    })
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()["data"]
    assert restored["version"] == 5 and restored["name"] == "可治理销售模板"
    assert restored["content"]["widgets"][0]["x"] == 32
    final_revisions = client.get(TEMPLATES + "/" + created["id"] + "/revisions", headers=administrator).json()["data"]
    assert [item["revision"] for item in final_revisions] == [5, 4, 3, 2, 1]


def test_prompt_categories_all_source_assets_reuse_and_content_versions(client, administrator):
    categories = client.get(PROMPTS + "/categories", headers=administrator).json()["data"]
    prompts = client.get(PROMPTS + "/templates", headers=administrator).json()["data"]
    assert len([row for row in categories if row["builtin"]]) == 9
    assert len([row for row in prompts if row["builtin"]]) == 36
    assert all(item["content"].strip() for item in prompts)
    category = client.post(PROMPTS + "/categories", headers=administrator, json={"name": "财务与设备"}).json()["data"]
    request = {"categoryId": category["id"], "name": "部门金额", "content": "生成部门金额及设备支出对比", "mode": "screen"}
    response = client.post(PROMPTS + "/templates", headers=administrator, json=request)
    assert response.status_code == 201, response.text
    prompt = response.json()["data"]
    used = client.post(PROMPTS + "/templates/" + prompt["id"] + "/use", headers=administrator)
    assert used.status_code == 200, used.text
    assert used.json()["data"]["useCount"] == 1 and used.json()["data"]["version"] == prompt["version"]
    same = client.put(PROMPTS + "/templates/" + prompt["id"], headers=administrator, json={**request, "version": prompt["version"]})
    assert same.status_code == 200, same.text
    assert same.json()["data"]["version"] == 2
    assert client.put(PROMPTS + "/templates/" + prompt["id"], headers=administrator, json={**request, "version": 1}).status_code == 409
    assert client.delete(PROMPTS + "/categories/" + category["id"], headers=administrator).status_code == 409
    assert client.delete(PROMPTS + "/templates/" + prompt["id"], headers=administrator).status_code == 200
    assert client.delete(PROMPTS + "/categories/" + category["id"], headers=administrator).status_code == 200
    assert client.delete(PROMPTS + "/templates/" + prompts[0]["id"], headers=administrator).status_code == 403


def fake_generated(source_id):
    chart = widget()
    chart["query"] = {"datasourceId": source_id, "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department"}
    chart["chartType"] = "bar"
    chart["config"] = {"option": {
        "title": "销售金额趋势",
        "series": [{"type": "bar", "data": [99999999]}],
    }}
    return {"title": "财务与设备", "templateId": None, "widgets": [chart]}


def test_generation_uses_real_queries_not_model_numbers_and_has_nonchat_run(client, administrator, source, monkeypatch, db):
    calls = []
    def respond(*args, **kwargs):
        calls.append(kwargs)
        return {"content": json.dumps(fake_generated(source["id"])), "model": "isolated-fixture", "usage": {"total_tokens": 10}}
    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "展示财务与设备", "templateMode": "none",
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert calls[0]["workspace_id"] == administrator["X-Workspace-Id"]
    assert "99999999" not in json.dumps(result)
    assert {row["department"]: row["total"] for row in result["widgets"][0]["data"]} == {"设备": 900, "财务": 200}
    assert result["widgets"][0]["config"]["option"]["title"]["text"] == "销售金额趋势"
    assert result["widgets"][0]["config"]["option"]["title"]["textStyle"]["fontSize"] >= 18
    assert result["templateId"] is None and result["queryIds"]
    run = db.get(Run, result["runId"])
    assert run.status == "completed" and run.conversation_id
    assert run.context_snapshot["execution_scope"] == "standalone"
    assert db.get(Conversation, run.conversation_id).archived_at is not None
    conversations = client.get("/api/chat/conversations", headers=administrator).json()
    assert run.conversation_id not in {item["id"] for item in conversations["items"]}
    assert client.get(
        "/api/chat/conversations/" + run.conversation_id, headers=administrator,
    ).status_code == 404
    operation = client.get(
        "/api/v1/operations/runs/" + run.id, headers=administrator,
    ).json()["data"]
    assert operation["conversationId"] == "" and operation["conversationPath"] == ""
    detail = client.get(REPORTS + "/generations/" + result["generationId"], headers=administrator)
    assert detail.status_code == 200 and detail.json()["data"]["status"] == "completed"
    assert [item["phase"] for item in detail.json()["data"]["events"]] == [
        "metadata", "schema_selection", "design", "query", "semantic_review", "completed",
    ]


def test_auto_template_selection_uses_reference_with_business_queries(client, administrator, source, monkeypatch):
    selected = create_custom_layout_template(client, administrator, "自动选择布局 A")
    calls = []
    def respond(profile, messages, **kwargs):
        context = json.loads(messages[-1]["content"])
        calls.append(context)
        if "candidates" in context:
            return {"content": json.dumps({"templateId": selected["id"], "reason": "生产结构适配"})}
        assert "manualTemplate" not in context
        assert context["templateSlots"] == []
        assert context["templateReference"]["id"] == selected["id"]
        return {"content": json.dumps(fake_generated(source["id"]))}
    monkeypatch.setattr(model, "chat_for_run", respond)
    canvas = selected["content"]["canvas"]
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生产与部门金额", "templateMode": "auto", "width": canvas["width"], "height": canvas["height"],
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert len(calls) == 2 and result["templateId"] == selected["id"]
    assert result["templateAdapted"] is True
    assert any(item.get("generatedSql") for item in result["widgets"])


def test_manual_template_ignores_model_template_change_and_preserves_all_slots(
    client, administrator, source, monkeypatch,
):
    selected = create_custom_layout_template(client, administrator, "自动选择布局 B")
    slots = _template_slots(selected)
    data_slot = slots[0]
    calls = 0

    def respond(profile, messages, **kwargs):
        nonlocal calls
        calls += 1
        context = json.loads(messages[-1]["content"])
        if "candidates" in context:
            return {"content": json.dumps({"templateId": selected["id"], "reason": "财务指标匹配"})}
        return {"content": json.dumps({
            "title": "财务与设备真实数据大屏",
            "templateId": "hallucinated-template",
            "widgets": [
                {
                    "id": data_slot["id"],
                    "component": "JBar",
                    "componentName": "部门金额",
                    "query": {"datasourceId": source["id"], "sql": (
                        "SELECT department, SUM(amount) AS total FROM sales GROUP BY department"
                    )},
                },
                {
                    "id": "model-added-widget",
                    "component": "JBar",
                    "query": {"datasourceId": source["id"], "sql": "SELECT COUNT(*) AS total FROM sales"},
                },
            ],
        })}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生成部门金额数字大屏，使用当前已授权的真实数据",
        "templateMode": "manual", "templateId": selected["id"],
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["templateId"] == selected["id"]
    assert len(result["widgets"]) == len(selected["content"]["widgets"])
    assert "model-added-widget" not in {item["id"] for item in result["widgets"]}
    slot_ids = {item["id"] for item in slots}
    assert all(item.get("generatedSql") for item in result["widgets"] if item["id"] in slot_ids)
    assert calls == 1


def test_generation_retries_optional_clarification(client, administrator, source, monkeypatch):
    responses = iter([
        {"content": json.dumps({"clarification": "请补充时间范围"})},
        {"content": json.dumps(fake_generated(source["id"]))},
    ])
    calls = []

    def respond(profile, messages, **kwargs):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生成财务和设备数字大屏，使用当前已授权的真实数据",
        "templateMode": "none",
    })
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert "全部可用数据" in calls[1][-1]["content"]


def test_large_metadata_is_compacted_and_sensitive_columns_are_not_sent_to_model():
    irrelevant = [{
        "tableName": f"audit_archive_{index}", "tableComment": "历史审计归档",
        "columns": [{"columnName": "id", "dataType": "INTEGER", "comment": "主键"}],
    } for index in range(80)]
    schemas = [{
        "id": "source-1", "name": "variantorm", "dialect": "mysql", "description": "",
        "tables": [
            *irrelevant,
            {"tableName": "t_devicealert", "tableComment": "设备预警", "columns": [
                {"columnName": "c_alertLevel", "dataType": "VARCHAR", "comment": "预警级别"},
                {"columnName": "c_mima", "dataType": "VARCHAR", "comment": "密码"},
            ]},
            {"tableName": "t_yingshouzhangkuan", "tableComment": "应收账款", "columns": [
                {"columnName": "c_yingshoujine", "dataType": "DECIMAL", "comment": "应收金额"},
            ]},
        ],
    }]
    compact = _compact_schemas(schemas, "财务和设备数字大屏")
    tables = compact[0]["tables"]
    names = {item["tableName"] for item in tables}
    assert {"t_devicealert", "t_yingshouzhangkuan"}.issubset(names)
    assert len(tables) <= 24
    assert "c_mima" not in {
        column["columnName"] for table in tables for column in table.get("columns", [])
    }
    assert compact[0]["catalogTableCount"] == 82


def test_longtext_is_not_a_numeric_measure():
    assert _column_kind({"dataType": "LONGTEXT"}) == "text"


def test_chart_result_without_numeric_series_repairs_original_metric_query(
    client, administrator, source, monkeypatch,
):
    candidate = fake_generated(source["id"])
    candidate["widgets"][0]["query"]["sql"] = "SELECT department FROM sales GROUP BY department"
    responses = iter([candidate, {"datasourceId": source["id"],
        "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department"}])
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": json.dumps(next(responses))})
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生成财务和设备数字大屏", "templateMode": "none",
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["queryCorrections"]
    assert "SUM" in result["widgets"][0]["generatedSql"]
    assert {row["department"]: row["total"] for row in result["widgets"][0]["data"]} == {
        "设备": 900, "财务": 200,
    }


def test_chart_aliases_keep_verified_business_query_and_use_component_orientation(
    client, administrator, source, monkeypatch,
):
    sql = "SELECT department, SUM(amount) AS total FROM sales GROUP BY department"
    candidate = {
        "title": "财务与设备",
        "templateId": None,
        "widgets": [
            {
                "id": "ranking", "component": "JHorizontalBar", "componentName": "部门金额排行",
                "x": 20, "y": 40, "w": 700, "h": 360, "chartType": "horizontalBar",
                "query": {"datasourceId": source["id"], "sql": sql}, "config": {"option": {}},
            },
            {
                "id": "structure", "component": "JRing", "componentName": "部门金额结构",
                "x": 760, "y": 40, "w": 500, "h": 360, "chartType": "ring",
                "query": {"datasourceId": source["id"], "sql": "SELECT id AS 类型, amount AS 数量 FROM sales WHERE amount IS NOT NULL ORDER BY id"},
                "config": {"option": {"labelField": "类型", "valueField": "数量"}},
            },
        ],
    }
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": json.dumps(candidate)})
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生成部门金额排行和结构", "templateMode": "none",
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["queryCorrections"] == []
    assert len(result["queryIds"]) == 2
    ranking, structure = result["widgets"]
    assert "sales" in ranking["generatedSql"] and "sales" in structure["generatedSql"]
    assert ranking["config"]["option"]["xAxis"]["type"] == "value"
    assert ranking["config"]["option"]["yAxis"]["type"] == "category"
    assert structure["config"]["option"]["series"][0]["type"] == "pie"
    assert structure["config"]["option"]["series"][0]["data"][0] == {"name": "1", "value": 100}


def test_repeated_invalid_json_fails_without_substituting_metrics(client, administrator, source, monkeypatch, db):
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": "not json"})
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "生成财务和设备数字大屏，使用当前已授权的真实数据",
        "templateMode": "none",
    })
    assert response.status_code == 502, response.text
    record = db.scalar(select(ReportGeneration))
    assert record.status == "failed" and record.result_json == {}
    assert not any(event["phase"] == "query" for event in record.events)


@pytest.mark.parametrize("failure", ["invalid_shape"])
def test_generation_failures_are_persisted_without_success_or_static_fallback(client, administrator, source, monkeypatch, db, failure):
    candidate = fake_generated(source["id"])
    request = {"theme": "生成报表", "templateMode": "none", "datasourceId": source["id"]}
    if failure == "invalid_shape":
        candidate["widgets"][0]["config"] = ["not-an-object"]
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": json.dumps(candidate)})
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json=request)
    assert response.status_code in {422, 502}, response.text
    record = db.scalar(select(ReportGeneration))
    assert record.status == "failed" and record.result_json == {}
    assert record.events[-1]["phase"] == "failed"
    assert db.get(Run, record.usage["runId"]).status == "failed"


def test_generation_result_cannot_bypass_revoked_source_access(client, administrator, source, monkeypatch):
    role = create_role(client, administrator, "report_author", ["home.view", "dashboards.view", "dashboards.execute", "datasources.view"], "workspace")
    user = create_user(client, administrator, [role["id"]])
    author = activate_user(client)
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": json.dumps(fake_generated(source["id"]))})
    response = client.post(REPORTS + "/widget/ai-generate", headers=author, json={"requirement": "生成金额图表", "datasourceId": source["id"]})
    assert response.status_code == 200, response.text
    generation_id = response.json()["data"]["generationId"]
    changed = client.patch("/api/v1/system/roles/" + role["id"], headers=administrator,
                           json={"version": role["version"], "permissions": ["home.view", "dashboards.view", "dashboards.execute"]})
    assert changed.status_code == 200, changed.text
    assert client.get(REPORTS + "/generations/" + generation_id, headers=author).status_code == 403


@pytest.mark.parametrize("path", ["/analytics", "/analytics/sql", "/analytics/history", "/analytics/quality",
                                 "/datasources", "/dashboards", "/templates", "/prompts", "/report/designer"])
def test_module_pages_use_normal_shell_not_chat(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert 'id="platform-root"' in response.text and 'id="analytics-app"' not in response.text
    assert client.get("/analytics/nonexistent").status_code == 404


def test_lynx_interface_template_assets_keep_their_original_paths(client):
    for path in ("/jimu-screen/bg4.png", "/jimu-screen/templates/wisdom-cloud-brain.png"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == "image/png"
