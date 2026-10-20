"""来源组件的纯HTML导出回归与离线视觉夹具；不创建连接、数据库或查询。"""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re

import pytest

from agent_platform.modules.reports.capabilities import SOURCE_COMPONENTS
from agent_platform.modules.reports.factory import component_instance
from agent_platform.modules.reports.rendering import render_html
from agent_platform.modules.reports.schemas import ComponentInstanceInput
from agent_platform.modules.reports.service import SUPPORTED_COMPONENTS
from agent_platform.modules.reports.source_presentations import CALENDAR_TYPES


ROWS = [
    {"name": "月度复核", "value": "8.0000", "observed": "2026-09-29T00:10:00Z", "note": "本轮检查证据丁，记录与现场复核结果一致。"},
    {"name": "一线复检", "value": "12.3400", "observed": "2026-09-30T16:30:00Z", "note": "本轮检查证据甲，复检已完成。"},
    {"name": "二线维护", "value": "-2.5000", "observed": "2026-10-03T00:10:00Z", "note": "本轮检查证据乙，等待处理确认。"},
    {"name": "三线巡检", "value": None, "observed": "2026-10-05", "note": "本轮检查证据丙，缺少测量值。"},
]


def source_widget(source, *, identifier=None, rows=None):
    """使用实际组件工厂生成配置，只以显式离线数据检查样式，不冒充业务查询证据。"""
    widget = component_instance(ComponentInstanceInput(source_id=source, id=identifier), SUPPORTED_COMPONENTS)
    widget["binding"] = {"label_field": "name", "value_field": "value", "time_field": "observed", "description_field": "note"}
    widget["data"] = deepcopy(ROWS if rows is None else rows)
    widget["config"]["background"] = "transparent"
    return widget


def widget_of(component, **kwargs):
    source = next(source for source, kind in SOURCE_COMPONENTS.items() if kind == component)
    return source_widget(source, **kwargs)


def report(widgets, *, height=930):
    return {"name": "离线导出样式回归", "layout": {"canvas": {"width": 1400, "height": height, "backgroundColor": "#07182a"}, "widgets": widgets}}


@pytest.mark.parametrize("component", sorted(set(SOURCE_COMPONENTS.values())))
def test_source_default_foreground_is_light_on_dark_canvas(component):
    widget = widget_of(component, rows=ROWS[1:2] if "Gauge" in component else ROWS)
    widget["config"].pop("color", None)
    html = render_html(report([widget]))
    section = re.search(r'<section[^>]+style="([^"]+)"', html)
    assert "color:#eaf8ff;" in section.group(1)
    if "Gauge" in component:
        assert 'stroke="#eaf8ff" stroke-width="3"' in html
    assert '<script' not in html


def test_explicit_source_colors_and_existing_component_default_are_preserved():
    widget = widget_of("JColorGauge", rows=ROWS[1:2])
    widget["config"]["color"] = "#abcdef"
    assert "color:#abcdef;" in render_html(report([widget]))
    widget["referenceSkin"] = {"textColor": "#123456"}
    assert "color:#123456;" in render_html(report([widget]))
    legacy = {"id": "legacy", "component": "JText", "config": {"text": "保留原默认值"}}
    assert "color:#173758;" in render_html(report([legacy]))


def test_calendar_full_description_flows_after_canvas_and_identical_calendars_share_details():
    description = "月末检查说明完整保留，不能靠鼠标悬停或滚动才能看到。" * 40
    rows = [{**ROWS[0], "note": description, "private": "未映射字段不能导出"}, ROWS[1]]
    widgets = [widget_of(component, identifier=component, rows=rows) for component in sorted(CALENDAR_TYPES)]
    html = render_html(report(widgets))
    canvas, details = html.split('</main>', 1)
    assert description not in canvas
    assert details.count(escape(description)) == 1
    assert details.count('<article class="source-agenda">') == 1
    assert details.count('<span id="source-agenda-') == 3
    assert "未映射字段不能导出" not in html
    assert "一线复检" not in html
    assert '2026-09-29 08:10' in details and '月度复核' in details
    assert 'white-space:pre-wrap;overflow:visible' in html


def test_calendar_keeps_all_month_events_and_separates_explicit_business_titles():
    rows = [{"name": f"事项{index}", "value": str(index), "observed": "2026-09-29", "note": f"完整说明{index}<&>"} for index in range(45)]
    first = widget_of("JCalendar", identifier="calendar-one", rows=rows)
    second = widget_of("JMultiViewCalendar", identifier="calendar-two", rows=rows)
    first["title"], second["title"] = "一号产线", "二号产线"
    html = render_html(report([first, second]))
    details = html.split('</main>', 1)[1]
    assert details.count('<article class="source-agenda">') == 2
    assert details.count('<tr>') == 92
    assert '完整说明44&lt;&amp;&gt;' in details and '事项44' in details
    assert '一号产线 · 2026-09 月日程' in details and '二号产线 · 2026-09 月日程' in details


def test_source_cards_static_grid_keeps_all_four_records_and_vertical_variant():
    for source, component in SOURCE_COMPONENTS.items():
        if component not in {"JCardScroll", "JCardCarousel"}:
            continue
        html = render_html(report([source_widget(source)]))
        assert '--source-card-columns:4' in html
        for row in ROWS:
            assert row["name"] in html and row["note"] in html
        assert ('source-cards vertical' in html) == ("竖向" in source)


def test_negative_background_bar_value_stays_outside_the_colored_column():
    html = render_html(report([widget_of("JBackgroundBar")]))
    rectangle = re.search(r'<rect data-value="-2.5000"[^>]+ y="([^"]+)"', html)
    label = re.search(r'<text[^>]+ y="([^"]+)"[^>]*>-2.5000</text>', html)
    assert float(label.group(1)) < float(rectangle.group(1))


def write_offline_fixtures(destination):
    """生成三份明确的纯样式夹具供本机Chrome离线截图，调用方使用新排他目录。"""
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    widgets = {source: source_widget(source, identifier="visual-" + str(index))
               for index, source in enumerate(SOURCE_COMPONENTS)}
    gauges, calendars, cards = [], [], []
    for source, widget in widgets.items():
        component = widget["component"]
        if "Gauge" in component:
            widget["data"] = deepcopy(ROWS[1:2])
            widget["config"]["option"] = {"min": "-10" if component == "JColorGauge" else "0", "max": "50" if component == "JColorGauge" else "40"}
            widget.update(x=40 + len(gauges) * 660, y=80, w=620, h=500)
            gauges.append(widget)
        elif component in CALENDAR_TYPES or component == "JDevHistory":
            widget.update(x=20 + len(calendars) % 2 * 700, y=20 + len(calendars) // 2 * 460, w=660, h=420)
            calendars.append(widget)
        else:
            index = len(cards)
            widget.update(x=20 + index % 2 * 700, y=20 if index < 2 else 330 + (index - 2) // 2 * 290,
                          w=660, h=270 if index < 2 else 260)
            cards.append(widget)
    fixtures = {"gauges": report(gauges, height=700), "calendar": report(calendars), "cards": report(cards)}
    for name, content in fixtures.items():
        (destination / f'{name}.html').write_text(render_html(content), encoding='utf-8')
    (destination / 'fixtures.json').write_text(json.dumps({"scope": "纯工厂/样式夹具；不是业务验收数据", "http": 0,
        "sql": 0, "model": 0, "reports": list(fixtures)}, ensure_ascii=False), encoding='utf-8')
