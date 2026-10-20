"""验证源素材实例、独立配置、授权图片与真实离线画布。"""
from collections import Counter
from copy import deepcopy
from io import BytesIO
import re

from docx import Document
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.settings import Settings
from agent_platform.modules.reports.factory import component_instance, presets
from agent_platform.modules.reports.rendering import _asset, render_html
from agent_platform.modules.reports.schemas import ComponentInstanceInput
from agent_platform.modules.reports.service import ReportService, SUPPORTED_COMPONENTS, component_catalog, validate_layout


MATERIALS = {"JImg", "JDragBorder", "JDragDecoration", "JCustomIcon", "JCarousel"}


def instance(source_id, **overrides):
    return component_instance(ComponentInstanceInput(source_id=source_id, **overrides), SUPPORTED_COMPONENTS)


def material(component, **overrides):
    source_id = next(key for key, item in presets().items() if item["source_type"] == component)
    return instance(source_id, **overrides)


@pytest.fixture
def context(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "materials.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("material-owner", "workspace", frozenset({"m07.*", "m16.*"}))
    settings = Settings(data_dir=tmp_path / "isolated-material-data")
    with Session(engine) as session:
        yield ReportService(session, actor, settings), FileService(session, actor, settings), session
        session.rollback()
    engine.dispose()


def test_all_205_material_instances_keep_variants_and_clear_sample_values():
    catalog = component_catalog()
    items = [item for item in catalog["items"] if item["group"] == "material"]
    assert len(items) == 205 and all(item["renderer_supported"] for item in items)
    counts, image_paths = Counter(), []
    borders, decorations = set(), set()
    for item in items:
        widget = instance(item["id"], x=12, y=24)
        assert widget["source_component_id"] == item["id"]
        assert widget["w"] == presets()[item["id"]]["canvas"]["w"]
        assert widget["h"] == presets()[item["id"]]["canvas"]["h"]
        assert widget["x"] == 12 and widget["y"] == 24
        assert widget["config"]["chartDataParsed"] == [] and widget["config"]["chartData"] == "[]"
        assert not {"sql", "headers", "token", "password", "apiUrl"}.intersection(widget["config"])
        validate_layout({"canvas": {}, "widgets": [widget]})
        counts[widget["component"]] += 1
        if widget["component"] == "JDragBorder":
            borders.add(widget["config"]["option"]["type"])
        elif widget["component"] == "JDragDecoration":
            decorations.add(widget["config"]["option"]["type"])
        elif widget["component"] == "JImg" and widget["config"].get("imageUrl"):
            image_paths.append(widget["config"]["imageUrl"])
    assert counts == {"JImg": 178, "JDragBorder": 13, "JDragDecoration": 12, "JCarousel": 1, "JCustomIcon": 1}
    assert borders == {str(value) for value in range(1, 14)}
    assert decorations == {str(value) for value in range(1, 13)}
    assert len(set(image_paths)) == 177
    # 读取新平台明确提取的静态素材，不访问原项目、数据库或外部URL。
    assert all(_asset(path).startswith("data:image/png;base64,") for path in image_paths)
    first, second = material("JImg"), material("JImg")
    first["config"]["option"]["fit"] = "cover"
    assert second["id"] != first["id"] and second["config"]["option"]["fit"] == "contain"
    assert _asset("/jimu-screen/iconlib/../../../../.local/platform/master.key") == ""
    assert _asset("https://example.test/image.png") == ""


def test_border_decoration_icon_and_carousel_keep_actual_rendering():
    widgets = [instance(key) for key, item in presets().items() if item["source_type"] in {"JDragBorder", "JDragDecoration"}]
    content = render_html({"name": "素材变体", "layout": {"canvas": {}, "widgets": widgets}})
    assert all(f'jimu-border border-{value}' in content for value in range(1, 14))
    assert all(f'jimu-decoration decoration-{value}' in content for value in range(1, 13))
    icons = []
    for icon in ("fa-star", "fa-gears", "fa-industry", "fa-shield-halved", "fa-chart-line", "fa-clipboard-check"):
        widget = material("JCustomIcon")
        widget["config"]["option"].update(icon="fa-solid " + icon, iconColor="#ff8123")
        text = render_html({"name": "图标", "layout": {"canvas": {}, "widgets": [widget]}})
        icons.extend(re.findall(r'<path fill-rule="evenodd" fill="#ff8123" d="([^"]+)"', text))
    assert len(icons) == len(set(icons)) == 6
    slides = [item["canvas"]["config"]["imageUrl"] for item in presets().values() if item["source_type"] == "JImg" and item["canvas"]["config"].get("imageUrl")][:2]
    carousel = material("JCarousel")
    carousel["config"]["images"] = slides
    content = render_html({"name": "轮播首帧", "layout": {"canvas": {}, "widgets": [carousel]}})
    assert content.count('<figure>') == 1 and _asset(slides[0]) in content and _asset(slides[1]) not in content


def test_material_canvas_publish_and_actual_pdf_png(context, tmp_path):
    reports, _, _ = context
    image_id = next(key for key, item in presets().items() if item["source_type"] == "JImg" and item["canvas"]["config"].get("imageUrl"))
    widgets = [instance(image_id, x=30, y=70), material("JDragBorder", x=5, y=5), material("JDragDecoration", x=300, y=10), material("JCustomIcon", x=350, y=100), material("JCarousel", x=520, y=80)]
    widgets[0].update(w=220, h=220)
    widgets[1].update(w=790, h=390)
    widgets[2].update(w=350, h=40)
    widgets[3].update(w=100, h=100)
    widgets[4].update(w=220, h=220)
    widgets[4]["config"]["images"] = [widgets[0]["config"]["imageUrl"]]
    report = reports.create({"name": "原素材组合", "layout": {"canvas": {"width": 800, "height": 400, "backgroundColor": "#061b30"}, "widgets": widgets}})
    assert reports.publish(report["id"], report["revision"])["status"] == "published"
    pdf, _, _ = reports.export(report["id"], "pdf")
    png, _, _ = reports.export(report["id"], "png")
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    image = Image.open(BytesIO(png))
    assert image.size == (800, 400) and len(image.getcolors(320001)) > 100
    (tmp_path / "material-canvas.pdf").write_bytes(pdf)
    (tmp_path / "material-canvas.png").write_bytes(png)
    office, _, _ = reports.export(report["id"], "docx")
    assert Document(BytesIO(office)).paragraphs[0].text == "原素材组合"


def test_private_image_authorization_references_and_historical_export(context):
    reports, files, session = context
    image_path = next(item["canvas"]["config"]["imageUrl"] for item in presets().values() if item["source_type"] == "JImg" and item["canvas"]["config"].get("imageUrl"))
    import base64
    content = base64.b64decode(_asset(image_path).split(",", 1)[1])
    stored = files.store_bytes("生产图片.png", content, "image/png")
    widget = material("JImg")
    widget["config"]["file_id"] = stored["id"]
    report = reports.create({"name": "授权图片", "layout": {"canvas": {}, "widgets": [widget]}})
    assert {"type": "report", "id": report["id"]} in files.references(stored["id"])
    assert "data:image/png;base64," in reports.export(report["id"], "html")[0]
    assets = reports.image_assets(report["id"])
    assert assets["total"] == 1 and assets["items"][0]["file_id"] == stored["id"]
    assert assets["items"][0]["data_uri"].startswith("data:image/png;base64,")
    other = ReportService(session, Actor("other-user", "workspace", frozenset({"m07.*"})), reports.settings)
    with pytest.raises(DomainError) as error:
        other.get(report["id"])
    assert error.value.code == "file_not_found"
    with pytest.raises(DomainError) as error:
        other.image_assets(report["id"])
    assert error.value.code == "file_not_found"
    without = reports.patch(report["id"], {"revision": report["revision"], "layout": {"canvas": {}, "widgets": []}})
    assert other.get(without["id"]).revision == without["revision"]
    with pytest.raises(DomainError) as error:
        other.export(report["id"], "json", revision=1)
    assert error.value.code == "file_not_found"
    fake = files.store_bytes("假图片.png", b"not a bitmap", "image/png")
    widget["config"]["file_id"] = fake["id"]
    fake_report = reports.create({"name": "假扩展名", "layout": {"canvas": {}, "widgets": [widget]}})
    with pytest.raises(DomainError) as error:
        reports.export(fake_report["id"], "html")
    assert error.value.code == "image_content_invalid"


def test_invalid_material_config_and_fake_decoration_cannot_bypass_evidence(context):
    reports, _, _ = context
    for changes in ({"canvas": []}, {"widgets": [{"id": []}]}, {"widgets": [{"id": "a", "config": []}]}, {"widgets": [{"id": "a", "config": {"option": []}}]}, {"widgets": [{"id": "a", "config": {"sql": "select 1"}}]}, {"widgets": [{"id": "a", "component": "JText", "x": float("nan")}]}):
        layout = {"canvas": {}, "widgets": [{"id": "a", "component": "JText"}]}
        layout.update(deepcopy(changes))
        with pytest.raises(DomainError) as error:
            validate_layout(layout)
        assert error.value.status == 422
        with pytest.raises(DomainError) as error:
            reports.create({"name": "错误结构", "layout": layout})
        assert error.value.status == 422
    report = reports.create({"name": "禁止假装饰", "layout": {"canvas": {}, "widgets": [{"id": "m", "component": "JBar", "type": "decoration", "data": [{"value": 999}]}]}})
    with pytest.raises(DomainError) as error:
        reports.publish(report["id"], report["revision"])
    assert error.value.code == "report_data_not_ready"
