"""定义模型可直接使用的演示文稿正文结构，避免无字段对象导致内容遗漏。"""
from __future__ import annotations

from copy import deepcopy


def staged_slide_write_schema() -> dict:
    """为正文、图表、表格和对比面板提供完整的原生工具 JSON Schema。"""
    text = {"type": "string"}
    number_array = {"type": "array", "items": {"type": "number"}}
    string_array = {"type": "array", "items": text}
    series = {"type": "array", "items": {
        "type": "object", "properties": {
            "name": text, "label": {"type": "string", "description": "name 的兼容别名"},
            "values": number_array, "data": {**number_array, "description": "values 的兼容别名"},
        }, "description": "每个序列填写 name 和 values；values 长度与 categories 一致",
    }}
    chart_properties = {
        "chart_type": {"type": "string", "enum": ["column", "bar", "line", "pie", "doughnut", "area"]},
        "categories": {**string_array, "description": "分类或时间标签，不可省略"},
        "series": series,
        "insight": {"type": "string", "description": "图表体现的有证据结论"},
        "source": {"type": "string", "description": "查询或材料来源，不得编造"},
    }
    rows = {"type": "array", "items": {"type": "array", "items": {
        "type": ["string", "number", "boolean", "null"],
    }}}
    table_properties = {
        "headers": {**string_array, "description": "表头，单独提供时 rows 只放数据行"},
        "columns": {**string_array, "description": "headers 的兼容别名"},
        "rows": rows, "source": text, "insight": text,
    }
    narrative_properties = {
        "type": {"type": "string", "description": "正文用 card 或 metric；图表用 chart，表格用 table"},
        "title": text, "label": {"type": "string", "description": "title 的兼容别名"},
        "description": {"type": "string", "description": "具体解释、证据或行动；不能只有标题"},
        "text": {"type": "string", "description": "description 的兼容别名"},
        "data": {"type": "string", "description": "description 的兼容别名；数字序列使用 series[].values"},
        "value": {"type": ["string", "number"], "description": "指标值，不接受嵌套对象"},
    }
    narrative = {"type": ["object", "string"], "properties": deepcopy(narrative_properties)}
    panel = {
        "type": ["array", "object"], "items": deepcopy(narrative),
        "properties": {"title": text, "items": {"type": "array", "items": deepcopy(narrative)}},
        "description": "推荐传正文条目数组，每侧至少两个含解释的条目",
    }
    item = {
        "type": ["object", "string"],
        "properties": {
            **deepcopy(narrative_properties), **deepcopy(chart_properties), **deepcopy(table_properties),
            "steps": {"type": "array", "items": deepcopy(narrative)},
        },
    }
    return {"type": "object", "properties": {
        "draft_id": {"type": "string", "description": "create_presentation_plan 返回的目标草稿标识；多稿任务须显式选择"},
        "slides": {
            "type": ["array", "string"], "minItems": 1, "maxItems": 50,
            "description": "正文数组；优先传原生数组。必须填写对应版式的正文载荷，notes 和 key_message 不能代替正文",
            "items": {"type": "object", "properties": {
                "slide_number": {"type": "integer",
                    "description": "从1开始的正文索引，不等于有封面时的总稿物理页码。必须参考 outline 的 slide_number/page_number 映射"},
                "physical_page_number": {"type": "integer", "minimum": 1,
                    "description": "总稿物理页码，从1开始。用户说修改第N页时优先传此字段；不能指向自动封面或结尾，与slide_number同时给出时必须映射同一页"},
                "title": text,
                "layout": {"type": "string", "description": "推荐content/cards/metrics/chart/table/comparison/timeline；正文不写cover或closing"},
                "section": text,
                "key_message": {"type": "string", "description": "本页核心结论；仍须提供 items/chart/table/left/right 等正文"},
                "statement": {"type": "string", "description": "仅 statement 版式使用；其他版式不能以此代替具体内容"},
                "items": {"type": "array", "items": item,
                    "description": "content/cards/metrics页放3至4个带说明条目；timeline放4至5个步骤；普通说明条目使用title和description"},
                "chart": {"type": "object", "properties": deepcopy(chart_properties),
                    "required": ["categories", "series"],
                    "description": "chart版式使用。饼图例：chart_type=pie，categories=[已巡检,未巡检]，series=[{name:设备数,values:[真实数量,真实数量]}]"},
                "table": {"type": "object", "properties": deepcopy(table_properties),
                    "required": ["headers", "rows"], "description": "table版式使用，至少两行有来源的数据"},
                **deepcopy(chart_properties),
                "headers": deepcopy(table_properties["headers"]),
                "columns": deepcopy(table_properties["columns"]),
                "rows": deepcopy(rows),
                "left": deepcopy(panel), "right": deepcopy(panel),
                "left_title": text, "right_title": text,
                "notes": {"type": "string", "description": "演讲备注，不会作为页面正文渲染，不能用来承载唯一的分析内容"},
            }},
        },
    }, "required": ["slides"]}
