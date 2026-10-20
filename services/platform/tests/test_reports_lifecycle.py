"""报表真实取数、版本恢复、发布门槛与导出内容验证。"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.modules.data.service import DataService
from agent_platform.modules.reports.service import ReportService, builtin_templates, component_catalog, validate_layout


@pytest.fixture
def context(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "reports-test.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    settings = Settings(data_dir=tmp_path / "isolated-data")
    actor = Actor("report-user", "workspace", frozenset({"*"}), ("admin",))
    with Session(engine) as session:
        yield ReportService(session, actor, settings), DataService(session, actor, settings)
        session.rollback()
    engine.dispose()


def test_twenty_templates_keep_editable_structure():
    templates = builtin_templates()
    assert len(templates) == 20
    for template in templates:
        layout = validate_layout(template["layout"])
        assert layout["widgets"]
        assert any(not w.get("locked") for w in layout["widgets"])


def test_report_binding_refresh_publish_restore_and_real_exports(context):
    service, data = context
    dataset = data.import_csv({"name": "产量", "csv_text": "name,value\n一班,10\n二班,20\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
    layout = {"canvas": {"width": 1440, "height": 900}, "widgets": [{"id": "trend", "component": "JLine", "x": 0, "y": 0, "w": 600, "h": 300, "binding": {"query_id": query["id"]}}]}
    report = service.create({"name": "制造日报", "layout": layout})
    with pytest.raises(DomainError) as error:
        service.publish(report["id"], 1)
    assert error.value.code == "report_data_not_ready"
    refreshed = service.refresh(report["id"], 1)
    assert refreshed["layout"]["widgets"][0]["data"][1]["value"] == "20"
    published = service.publish(report["id"], refreshed["revision"])
    assert published["status"] == "published"
    assert published["published_revision"] == published["revision"]
    content, media, suffix = service.export(report["id"], "html")
    assert "<svg" in content and "制造日报" in content and media.startswith("text/html")
    csv, _, _ = service.export(report["id"], "csv")
    assert "一班" in csv and "20" in csv
    snapshot, _, _ = service.export(report["id"], "json")
    assert json.loads(snapshot)["id"] == report["id"]
    restored = service.restore(report["id"], published["revision"], 1)
    assert restored["status"] == "draft"
    assert restored["revision"] == published["revision"] + 1
    assert "data" not in restored["layout"]["widgets"][0]
    assert service.revisions(report["id"])["total"] == 4
    with pytest.raises(DomainError) as error:
        service.patch(report["id"], {"revision": 1, "name": "过期编辑"})
    assert error.value.status == 409


def test_export_escapes_html_and_spreadsheet_formulas(context):
    service, _ = context
    report = service.create({"name": "<script>alert(1)</script>", "layout": {"canvas": {}, "widgets": [{"id": "txt", "component": "text", "data": [{"value": "=cmd|' /c calc'!A0"}]}]}})
    text, _, _ = service.export(report["id"], "html")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
    csv, _, _ = service.export(report["id"], "csv")
    assert "'=cmd" in csv


def test_forged_report_data_cannot_be_published(context):
    service, data = context
    dataset = data.import_csv({"name": "检测", "csv_text": "value\n10\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
    report = service.create({"name": "不能伪造证据", "layout": {"canvas": {}, "widgets": [{"id": "m", "component": "metric", "binding": {"query_id": query["id"]}, "data": [{"value": "999"}], "data_provenance": {"type": "query", "query_id": query["id"], "truncated": False}}]}})
    with pytest.raises(DomainError) as error:
        service.publish(report["id"], report["revision"])
    assert error.value.code == "report_data_evidence_mismatch"


def test_all_twenty_templates_refresh_from_small_local_source(context):
    """20次单条小查询替换20套模板数据，不属于并发或压力测试。"""
    service, data = context
    dataset = data.import_csv({"name": "模板换数验收", "csv_text": "name,value,unit\n一车间,17,台\n二车间,23,台\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"', "max_rows": 10})
    for template in builtin_templates():
        bindings = {w["id"]: query["id"] for w in template["layout"]["widgets"] if w.get("type") not in ("title", "decoration") and w.get("component") not in ("JText", "JDragImg")}
        report = service.generate({"name": template["name"], "template_id": template["id"], "bindings": bindings})
        for widget in report["layout"]["widgets"]:
            if widget.get("binding"):
                assert widget["data"][0]["value"] == "17"
                assert widget["config"]["referenceSample"] is False
        assert service.publish(report["id"], report["revision"])["status"] == "published"
    catalog = component_catalog()
    assert catalog["total"] == 326
    assert any(not c["renderer_supported"] for c in catalog["items"])
