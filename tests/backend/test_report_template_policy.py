"""Explicit templates are SQL-only; unspecified templates are visual references."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app.modules.reports import generation
from app.modules.reports.models import Report
from app.modules.reports.template_lock import visual_fingerprint
from app.providers import model
from test_analytics import source
from test_reports import create_custom_layout_template, fake_generated

pytestmark = pytest.mark.usefixtures("approved_report_semantics")


def test_explicit_custom_template_ignores_model_visual_changes_and_saves_original_size(
    client, administrator, source, monkeypatch, db,
):
    template = create_custom_layout_template(client, administrator, "部门金额模板")
    seen = []

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        seen.append(context)
        assert context["templateId"] == template["id"]
        assert context["templateReference"] is None and context["templateSlots"]
        candidate = fake_generated(source["id"])
        candidate["title"] = "模型擅自修改标题"
        candidate["canvas"] = {"width": 1920, "height": 1080, "backgroundColor": "red"}
        candidate["widgets"][0].update(x=900, y=800, w=100, h=50, component="JLine", unit="万元")
        return {"content": json.dumps(candidate)}

    monkeypatch.setattr(model, "chat_for_run", reply)
    payload = {"requirement": "按部门统计金额", "datasourceIds": [source["id"]], "templateMode": "manual",
               "templateId": template["id"], "requestKey": "manual-sql-only", "width": 1920, "height": 1080}
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    report = db.scalar(select(Report))
    assert report.name == template["name"]
    assert report.width == 1280 and report.height == 720
    assert report.content["canvas"]["width"] == 1280 and report.content["canvas"]["height"] == 720
    assert visual_fingerprint(report.content) == visual_fingerprint(template["content"])
    assert len(report.content["widgets"]) == len(template["content"]["widgets"])
    assert report.content["widgets"][0]["dataStatus"] == "verified"
    assert len(seen) == 1
    repeated = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert repeated.status_code in {200, 201} and len(seen) == 1
    assert len(db.scalars(select(Report)).all()) == 1


def test_automatic_choice_always_provides_visual_reference_instead_of_fixed_bindings(
    client, administrator, source, monkeypatch,
):
    template = create_custom_layout_template(client, administrator, "销售金额模板")
    monkeypatch.setattr(generation, "list_templates", lambda ctx: [deepcopy(template)])
    stages = []

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if "candidates" in context:
            stages.append("selection")
            return {"content": json.dumps({"templateId": template["id"]})}
        stages.append("design")
        assert context["templateSlots"] == [] and context["templateId"] is None
        assert context["templateReference"]["id"] == template["id"]
        return {"content": json.dumps(fake_generated(source["id"]))}

    monkeypatch.setattr(model, "chat_for_run", reply)
    response = client.post("/api/v1/reports/screen/ai-generate", headers=administrator, json={
        "theme": "按部门统计销售金额", "datasourceId": source["id"], "templateMode": "auto"})
    assert response.status_code == 200, response.text
    assert stages == ["selection", "design"]
    assert response.json()["data"]["templateAdapted"] is True
