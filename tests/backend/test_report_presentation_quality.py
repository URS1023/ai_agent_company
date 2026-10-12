"""Presentation repair preserves business intent, SQL and explicit component count."""
from copy import deepcopy
import json
from pathlib import Path

from app.modules.reports.generation_quality import clean_display_title, prepare_presentation, layout_findings
from app.modules.reports.generation_reference import apply_reference, missing_sections, required_sections
from app.modules.reports.generation_bindings import bind_table_headers


def test_sparse_five_component_order_report_fills_canvas_without_changing_queries():
    widgets = [
        {"id": str(i), "component": "JStatsSummary", "componentName": name, "x": 80 + i * 440, "y": 80, "w": 360, "h": 180,
         "query": {"sql": f"SELECT {aggregate}(amount) AS value FROM orders WHERE created_at >= :start", "parameters": {"start": "2026-09-01"}},
         "config": {"option": {"valueFontSize": 60, "titleFontSize": 22, "unit": "元"}}}
        for i, (name, aggregate) in enumerate([("总金额", "SUM"), ("订单数", "COUNT"), ("平均金额", "AVG")])
    ] + [
        {"id": "trend", "component": "JLine", "componentName": "金额趋势", "x": 80, "y": 300, "w": 880, "h": 400,
         "query": {"sql": "SELECT day, SUM(amount) FROM orders GROUP BY day"}, "config": {"option": {}}},
        {"id": "detail", "component": "JScrollBoard", "componentName": "订单明细", "x": 1020, "y": 300, "w": 820, "h": 400,
         "query": {"sql": "SELECT * FROM orders LIMIT 10"}, "config": {"option": {}}},
    ]
    before = deepcopy(widgets)
    canvas = {"width": 1920, "height": 1080}
    result = prepare_presentation(canvas, widgets, width=1920, height=1080)
    assert result["layoutAdjusted"] and result["remainingFindings"] == []
    assert len(widgets) == 5 and [w["id"] for w in widgets] == [w["id"] for w in before]
    assert [w["query"] for w in widgets] == [w["query"] for w in before]
    assert widgets[0]["y"] == widgets[1]["y"] == widgets[2]["y"]
    assert widgets[3]["y"] == widgets[4]["y"]
    assert max(w["y"] + w["h"] for w in widgets) >= 1040
    assert widgets[0]["config"]["option"]["card"]["valueFontSize"] == 60
    assert widgets[0]["unit"] == "元"
    assert canvas["visualMode"] == "ai" and canvas["backgroundLayers"] == "none"
    assert widgets[4]["config"]["option"]["title"]["show"] is True


def test_valid_custom_layout_and_units_remain_unchanged():
    widgets = [{"id": str(i), "component": "JStatsSummary", "x": 30 + i * 480, "y": 30, "w": 450, "h": 1020,
                "unit": "万元", "style": {"background": "#abcdef", "valueColor": "#123456"}, "config": {"option": {"card": {"valueFontSize": 62}}}}
               for i in range(2)]
    geometry = [{k: w[k] for k in ("x", "y", "w", "h")} for w in widgets]
    result = prepare_presentation({"backgroundColor": "#ffffff"}, widgets, width=1000, height=1080)
    assert not result["layoutAdjusted"]
    assert geometry == [{k: w[k] for k in ("x", "y", "w", "h")} for w in widgets]
    assert widgets[0]["unit"] == "万元" and widgets[0]["style"]["valueColor"] == "#123456"


def test_overlap_and_out_of_bounds_are_detected_and_repaired():
    widgets = [{"id": str(i), "component": "JBar", "x": 1600, "y": 800, "w": 700, "h": 400,
                "config": {"option": {}}} for i in range(4)]
    assert set(layout_findings(widgets, 1920, 1080)) >= {"overlap", "out_of_bounds"}
    result = prepare_presentation({}, widgets, width=1920, height=1080)
    assert result["remainingFindings"] == []


def test_reference_visual_adaptation_never_copies_template_units_values_or_queries():
    template = {"id": "reference", "name": "设备模板", "content": {
        "canvas": {"width": 1920, "height": 1080, "theme": {"text": "#eaf8ff", "panel": "#092a59", "grid": "#277dff55"}},
        "widgets": [{"id": "sample", "component": "JStatsSummary", "data": [{"value": 99999}], "unit": "%",
                     "query": {"sql": "SELECT secret FROM source"}, "config": {"option": {"unit": "%", "valueColor": "#ffb647"}}}]}}
    widgets = [{"id": "finance", "component": "JStatsSummary", "componentName": "应收金额", "unit": "元",
                "x": 40, "y": 40, "w": 450, "h": 200, "query": {"sql": "SELECT amount FROM finance"},
                "data": [{"value": 200}], "config": {"option": {}}},
               {"id": "equipment", "component": "JStatsSummary", "componentName": "设备数", "unit": "台",
                "x": 500, "y": 40, "w": 450, "h": 200, "query": {"sql": "SELECT id FROM devices"},
                "data": [{"value": 2}], "config": {"option": {}}}]
    original = deepcopy(widgets)
    canvas = {"width": 1920, "height": 1080}
    apply_reference(canvas, widgets, template, adapt_layout=True)
    assert [{k: w[k] for k in ['id', 'unit', 'query', 'data']} for w in widgets] == [{k: w[k] for k in ['id', 'unit', 'query', 'data']} for w in original]
    assert not (set(layout_findings(widgets, 1920, 1080)) & {"overlap", "out_of_bounds"})
    assert all(w["referenceStyle"] == "tech-panel" for w in widgets)
    assert canvas["templateReference"]["id"] == "reference"
    # An explicit component-count layout cannot gain/delete decorations or slots.
    before = deepcopy(widgets)
    apply_reference(canvas, widgets, template, adapt_layout=False)
    assert len(widgets) == len(before) and [{k: w[k] for k in ['x','y','w','h']} for w in widgets] == [{k: w[k] for k in ['x','y','w','h']} for w in before]


def test_scope_check_accepts_authorized_finance_table_beyond_first_three():
    schemas = [{"id": "local", "tables": [{"tableName": f"finance_{i}", "tableComment": "财务金额"} for i in range(5)]}]
    sections = required_sections("财务", schemas)
    assert missing_sections([{"query": {"datasourceId": "local", "sql": "SELECT amount FROM finance_4"}}], sections) == []
    assert missing_sections([{"query": {"datasourceId": "other", "sql": "SELECT amount FROM finance_4"}}], sections)


def test_table_display_labels_bind_to_explicit_fields_and_do_not_hide_real_rows():
    widget = {"config": {"dataMapping": {"columns": ["payee", "request_amount"]},
                         "option": {"header": ["收款单位", "申请金额（元）"]}}}
    bind_table_headers(widget, ["payee", "request_amount"])
    assert widget["config"]["option"]["header"] == [
        {"key": "payee", "label": "收款单位"}, {"key": "request_amount", "label": "申请金额（元）"},
    ]
    unmapped = {"config": {"option": {"header": ["收入", "成本"]}}}
    bind_table_headers(unmapped, ["payee", "request_amount"])
    assert unmapped["config"]["option"]["header"] == [
        {"key": "payee", "label": "payee"}, {"key": "request_amount", "label": "request_amount"},
    ]


def test_reference_decoration_is_transparent_and_long_pie_labels_use_scroll_legend():
    widgets = [{"id": "stars", "component": "JDataConstellation", "style": {"background": "#092a59"}},
               {"id": "pie", "component": "JPie", "w": 900, "h": 227, "query": {"sql": "SELECT payee, amount FROM payments"},
                "config": {"option": {"series": [{"type": "pie", "data": [{"name": "很长的真实收款单位名称", "value": 20}]}]}}}]
    template = {"id": "reference", "name": "模板", "content": {"canvas": {"theme": {"text": "#eaf8ff"}}, "widgets": []}}
    before = deepcopy(widgets[1]["config"]["option"]["series"][0]["data"])
    apply_reference({}, widgets, template, adapt_layout=False)
    assert widgets[0].get("referenceStyle") != "tech-panel"
    assert widgets[0]["style"]["background"] == "transparent" and widgets[0]["style"]["borderWidth"] == 0
    option = widgets[1]["config"]["option"]
    assert option["legend"]["type"] == "scroll" and option["legend"]["orient"] == "vertical"
    assert option["series"][0]["label"]["show"] is False
    assert option["series"][0]["data"] == before


def test_template_48_uses_original_regions_and_frames_instead_of_generic_two_columns():
    catalog = json.loads((Path(__file__).resolve().parents[2] / "backend/app/modules/reports/resources/lynx_source_reports.json").read_text(encoding="utf-8"))
    template = next(t for t in catalog["reports"] if str(t["sourceRecordId"]) == "48")
    template = {**template, "id": "lynx-source-template-48"}
    components = ["JStatsSummary"] * 4 + ["JPie", "JRing", "JBar", "JBar", "JScrollBoard", "JScrollBoard"]
    widgets = [{"id": str(i), "component": c, "componentName": f"实际指标{i}",
                "query": {"sql": f"SELECT metric_{i} FROM authorized"}, "data": [{"value": i}],
                "config": {"option": {}}, "x": 0, "y": 0, "w": 1, "h": 1} for i, c in enumerate(components)]
    original = deepcopy(widgets)
    canvas = {"width": 1920, "height": 1080}
    apply_reference(canvas, widgets, template, adapt_layout=True)
    actual = [w for w in widgets if w.get("query")]
    assert canvas["templateReference"]["layoutPolicy"] == "template-regions"
    assert [w["query"] for w in actual] == [w["query"] for w in original]
    assert [w["data"] for w in actual] == [w["data"] for w in original]
    assert len([w for w in widgets if w["component"] == "JDragBorder"]) == 10
    hero = next(w for w in actual if w.get("templateVisualRole") == "hero-number")
    assert hero["templateSlotId"] == "showcase-19-widget-5"
    assert hero["x"] == round(272 * 1920 / 767)
    assert hero["w"] == round(224 * 1920 / 767)
    assert not set(layout_findings(widgets, 1920, 1080)) & {"overlap", "out_of_bounds"}
    assert all(w["referenceStyle"] == "template-slot" for w in actual)


def test_cyber_styling_and_clean_titles():
    # Test title cleaning
    assert clean_display_title("销售总金额指标卡") == "销售总金额"
    assert clean_display_title("核心主视觉设备运行状态趋势") == "设备运行状态趋势"
    assert clean_display_title("组件1") == "组件1"  # fallback if title would become empty
    assert clean_display_title("产品合格率（图表）") == "产品合格率"

    # Test cyber styling injection in prepare_presentation
    widgets = [
        {
            "id": "kpi-1", "component": "JStatsSummary", "componentName": "总营收指标卡",
            "x": 80, "y": 80, "w": 400, "h": 200,
            "query": {"sql": "SELECT SUM(rev) FROM t"},
            "config": {"option": {"card": {}}},
        },
        {
            "id": "trend-1", "component": "JLine", "componentName": "营收趋势图",
            "x": 80, "y": 300, "w": 860, "h": 400,
            "query": {"sql": "SELECT m, SUM(rev) FROM t GROUP BY m"},
            "config": {"option": {"series": [{"type": "line", "data": [10, 20, 30]}]}},
        },
        {
            "id": "bar-1", "component": "JBar", "componentName": "部门产值柱状图",
            "x": 980, "y": 300, "w": 860, "h": 400,
            "query": {"sql": "SELECT d, SUM(val) FROM t GROUP BY d"},
            "config": {"option": {"series": [{"type": "bar", "data": [15, 25, 35]}]}},
        },
        {
            "id": "pie-1", "component": "JPie", "componentName": "分类占比饼图",
            "x": 80, "y": 720, "w": 860, "h": 320,
            "query": {"sql": "SELECT c, COUNT(1) FROM t GROUP BY c"},
            "config": {"option": {"series": [{"type": "pie", "data": [{"name": "A", "value": 10}, {"name": "B", "value": 20}]}]}},
        },
        {
            "id": "table-1", "component": "JScrollBoard", "componentName": "明细表格",
            "x": 980, "y": 720, "w": 860, "h": 320,
            "query": {"sql": "SELECT * FROM t LIMIT 10"},
            "config": {"option": {}},
        },
    ]
    canvas = {"width": 1920, "height": 1080}
    prepare_presentation(canvas, widgets, width=1920, height=1080)

    # 1. Cleaned titles (mechanical suffixes like '指标卡' stripped, business titles preserved)
    assert widgets[0]["componentName"] == "总营收"
    assert widgets[1]["componentName"] == "营收趋势图"
    assert widgets[2]["componentName"] == "部门产值柱状图"
    assert widgets[3]["componentName"] == "分类占比饼图"

    # 2. Cyber styles on KPI card
    card = widgets[0]["config"]["option"]["card"]
    assert card.get("valueFontFamily") == "Bahnschrift, 'Microsoft YaHei UI', sans-serif"
    assert card.get("titleColor") == "#9eb0c8"

    # 3. Cyber line smoothing and glow
    line_series = widgets[1]["config"]["option"]["series"][0]
    assert line_series.get("smooth") == 0.35
    assert line_series.get("lineStyle", {}).get("width") == 3

    # 4. Cyber bar radius and background rail slot
    bar_series = widgets[2]["config"]["option"]["series"][0]
    assert bar_series.get("itemStyle", {}).get("borderRadius") == [4, 4, 0, 0]
    assert bar_series.get("showBackground") is True
    assert bar_series.get("barMaxWidth") == 36

    # 5. Cyber donut pie with padding angle and borderRadius
    pie_series = widgets[3]["config"]["option"]["series"][0]
    assert pie_series.get("radius") == ["46%", "68%"]
    assert pie_series.get("padAngle") == 2
    assert pie_series.get("itemStyle", {}).get("borderRadius") == 4

