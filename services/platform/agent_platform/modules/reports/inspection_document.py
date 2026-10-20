"""检验正式文档按正文流分页，所有明细完整输出，不受大屏画布高度裁剪。"""
from html import escape
from datetime import datetime, timedelta, timezone
import json

from .inspection_source import OUTCOMES


CATEGORIES = {"numeric": "数值", "component": "成分", "appearance": "外观"}
SEVERITIES = {"minor": "轻微", "moderate": "中等", "severe": "严重", "review": "待复核"}
METADATA = {"inspection_name": "检验任务", "device_name": "设备", "product_name": "产品", "batch_number": "批次",
            "template_name": "检验标准", "calculated_at": "检测时间", "system_outcome": "系统判定",
            "review_status": "复核状态", "review_outcome": "复核结论", "standard_revision": "标准版本", "captured_at": "生成时间"}
SUMMARY = {"expected_samples": "应检样本", "actual_samples": "实检样本", "missing_samples": "缺少样本",
           "passed_samples": "合格", "failed_samples": "不合格", "review_samples": "待复核", "incomplete_samples": "数据不完整"}


def display(value):
    """空值明确保留为空缺，不能将未测数值显示成0。"""
    return "—" if value is None or value == "" else str(value)


def business_time(value):
    """文档展示北京时间，证据快照保留原始ISO时间与时区。"""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return display(value)


def _defects(value):
    """缺陷名、置信度、等级和位置使用可读标签，不把JSON键当业务说明。"""
    if not value:
        return ""
    rows = json.loads(value)
    return "；".join(str(row.get("defect_type", "缺陷")) + "（置信度 " + str(row.get("confidence", "—")) + "，" + SEVERITIES.get(row.get("severity"), "待判定") + ("，位置 " + str(row["bbox"]) if row.get("bbox") else "") + "）" for row in rows)


def _values(report, view):
    widget = next((item for item in report["layout"]["widgets"] if (item.get("binding") or {}).get("view") == view), None)
    return widget.get("data", []) if widget else []


def _range(row, unit_field="unit"):
    low, high = row.get("standard_min"), row.get("standard_max")
    if row.get("category") == "appearance":
        confidence = row.get("minimum_confidence")
        severity = row.get("blocking_severities")
        if isinstance(severity, str):
            severity = [part.strip() for part in severity.replace("，", ",").split(",") if part.strip()]
        severity_text = "、".join(SEVERITIES.get(part, part) for part in (severity or []))
        return "；".join(item for item in ("判定置信度 ≥ " + str(confidence) if confidence is not None else "", "不合格级别：" + severity_text if severity_text else "")) or "按外观标准"
    value = "—" if low is None and high is None else f"{display(low)} ～ {display(high)}" if low is not None and high is not None else "≥ " + display(low) if low is not None else "≤ " + display(high)
    return value + (" " + str(row[unit_field]) if row.get(unit_field) else "")


def _standard_key(row):
    """名称相同但阈值不同的指标仍对应独立标准，不能仅按指标名称合并。"""
    severity = row.get("blocking_severities")
    if isinstance(severity, str):
        severity = tuple(part.strip() for part in severity.replace("，", ",").split(",") if part.strip())
    elif isinstance(severity, list):
        severity = tuple(severity)
    return (row.get("metric", row.get("name")), row.get("category"), row.get("component_name"),
            row.get("standard_min"), row.get("standard_max"), row.get("standard_unit", row.get("unit")),
            row.get("minimum_confidence"), severity)


def _metric_section(category, label, selected, component_label, standards):
    """按实际数据选择列；外观明细引用固定标准，完整约束只在标准快照呈现一次。"""
    appearance = category == "appearance"
    has_images = any(row.get("image_ref") not in (None, "") for row in selected)
    has_values = not appearance or any(row.get("value") not in (None, "") for row in selected)
    columns = ["样本", "检验项" if appearance else "指标 / " + component_label]
    widths = [12, 22] if appearance else [12, 16]
    if has_values:
        columns.append("实测")
        widths.append(32)
    columns.extend(["标准", "判定", "依据"])
    widths.extend([12, 10, 30] if appearance else [14, 9, 17])
    if has_images:
        columns.append("图像来源")
        widths.append(22 if appearance else 13)
    # 列宽使用相对权重，额外图像或外观实测列出现时仍可组成完整的一行。
    total_width = sum(widths)
    widths = [round(value * 100 / total_width, 3) for value in widths]
    rows = []
    for row in selected:
        name = display(row.get("metric"))
        if row.get("component_name"):
            name = display(row["component_name"]) + " / " + name
        cells = [display(row.get("sample")), name]
        if has_values:
            cells.append(display(row.get("value")) + (" " + str(row["unit"]) if row.get("unit") else ""))
        reference = standards.get(_standard_key(row))
        # 未匹配到标准时保留该行全部判定约束，不用错误引用掩盖来源差异。
        standard = "标准 " + str(reference) if appearance and reference else _range(row, "standard_unit")
        reason = display(row.get("reason"))
        if appearance and reason == "检测结果明确返回无缺陷":
            reason = "未检出缺陷"
        if row.get("observation_note"):
            reason += "；" + row["observation_note"]
        if row.get("defects"):
            reason += "；" + _defects(row["defects"])
        cells.extend([standard, OUTCOMES.get(row.get("outcome"), display(row.get("outcome"))), reason])
        if has_images:
            cells.append(display(row.get("image_ref")))
        rows.append(cells)
    return {"title": label + "检验明细", "rows": [columns, *rows], "column_widths": widths}


def office_sections(report):
    """Word、Excel和PPT复用真实结构化导出，表格行不截断且PPT按18行分页。"""
    metadata = {row["name"]: row.get("value") for row in _values(report, "meta")}
    for key in ("calculated_at", "captured_at"):
        if metadata.get(key):
            metadata[key] = business_time(metadata[key])
    categories = {**CATEGORIES, **({"numeric": "性能", "component": "部件"} if metadata.get("target_type") == "device" else {})}
    info = [[label, OUTCOMES.get(metadata[key], display(metadata[key]))] for key, label in METADATA.items() if metadata.get(key) is not None and metadata.get(key) != ""]
    summary = [[SUMMARY[row["name"]], row.get("value")] for row in _values(report, "summary") if row["name"] in SUMMARY]
    sections = [{"title": "检验信息", "rows": [["项目", "内容"], *info], "column_widths": [22, 78]}, {"title": "样本统计", "rows": [["项目", "数量"], *summary], "column_widths": [70, 30]}]
    outcomes = _values(report, "outcomes")
    from .exports import numeric_chart
    chart = numeric_chart({"component": "JBar", "binding": {"label_field": "name", "value_field": "value"}}, outcomes)
    sections.append({"title": "样本判定分布", "rows": [["判定", "样本数"], *[[row["name"], row["value"]] for row in outcomes]], "chart": chart, "column_widths": [70, 30]})
    metrics = _values(report, "metrics")
    standard = _values(report, "standard")
    references = {_standard_key(row): index for index, row in enumerate(standard, start=1)}
    for category, label in categories.items():
        selected = [row for row in metrics if row.get("category") == category]
        if not selected:
            continue
        sections.append(_metric_section(category, label, selected, categories["component"], references))
    has_components = any(row.get("component_name") for row in standard)
    standard_header = ["编号", "指标", "类别"] + ([categories["component"]] if has_components else []) + ["判定标准"]
    standard_rows = [["标准 " + str(index), display(row.get("name")), categories.get(row.get("category"), display(row.get("category")))] + ([display(row.get("component_name"))] if has_components else []) + [_range(row)] for index, row in enumerate(standard, start=1)]
    sections.append({"title": "标准快照", "rows": [standard_header, *standard_rows], "column_widths": [11, 22, 11, 15, 41] if has_components else [12, 24, 12, 52]})
    snapshot = report["layout"]["inspection_source"]
    names = snapshot.get("reviewer_names", {})
    reviews = _values(report, "reviews")
    sections.append({"title": "复核记录", "rows": [["复核时间", "复核人", "结论", "记录类型", "意见"], *[[business_time(row["created_at"]), names.get(row["created_by"], "已登记人员"), OUTCOMES.get(row["outcome"], row["outcome"]), "偏差记录" if row["kind"] == "deviation" else "复核", row["note"]] for row in reviews]], "column_widths": [23, 15, 12, 14, 36]} if reviews else {"title": "复核记录", "text": "待复核"})
    return sections


def render_html(report):
    """使用流式HTML和可重复表头，浏览器打印会自然形成多页PDF。"""
    sections = office_sections(report)
    body = []
    for section in sections:
        content = ""
        rows = section.get("rows", [])
        if rows:
            header, values = rows[0], rows[1:]
            columns = '<colgroup>' + ''.join('<col style="width:' + str(width) + '%">' for width in section["column_widths"]) + '</colgroup>'
            content += '<table>' + columns + '<thead><tr>' + ''.join('<th scope="col">' + escape(display(value)) + '</th>' for value in header) + '</tr></thead><tbody>'
            content += ''.join('<tr>' + ''.join('<td>' + escape(display(value)) + '</td>' for value in row) + '</tr>' for row in values) + '</tbody></table>'
        if section.get("text"):
            content += '<p>' + escape(section["text"]) + '</p>'
        if section.get("chart"):
            from .cartesian import render_cartesian
            chart = render_cartesian({"component": "JBar", "binding": {"label_field": "name", "value_field": "value"}}, _values(report, "outcomes"), "#173b64", ["#3783d6", "#e36a67", "#d8a43d", "#8b9bb3"])
            content = '<div class="distribution">' + chart + '</div>' + content
        # 短标准与复核表尽量整块分页，长明细仍自然拆页并重复表头。
        section_class = ' class="compact-section"' if not section.get('chart') and len(rows) <= 7 else ''
        body.append('<section' + section_class + '><h2>' + escape(section["title"]) + '</h2>' + content + '</section>')
    title = escape(report["name"])
    status = "已发布" if report.get("status") == "published" else "草稿"
    css = '''html,body{margin:0;font-family:"Microsoft YaHei",Arial,sans-serif;color:#18314e;background:#edf2f7;font-size:12px;line-height:1.6}main{box-sizing:border-box;width:794px;margin:0 auto;background:white;padding:42px 38px}header{border-bottom:3px solid #265d97;padding-bottom:16px;margin-bottom:22px}h1{font-size:25px;margin:0 0 6px;overflow-wrap:anywhere}header p{margin:0;color:#61748b}h2{font-size:16px;margin:20px 0 9px;break-after:avoid}table{width:100%;border-collapse:collapse;table-layout:fixed;margin-bottom:12px}th,td{padding:7px 8px;border:1px solid #dce4ed;text-align:left;vertical-align:top;overflow-wrap:anywhere;white-space:pre-wrap}th{background:#edf3fa;color:#173b64;font-weight:600}thead{display:table-header-group}tr{break-inside:avoid}section{break-inside:auto}.compact-section{break-inside:avoid}.distribution{width:100%;height:195px;break-inside:avoid}.distribution svg{width:100%;height:100%}@page{size:A4;margin:14mm 10mm}@media print{html,body{background:white}main{width:auto;margin:0;padding:0}body{-webkit-print-color-adjust:exact;print-color-adjust:exact}}'''
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\';img-src data:;style-src \'unsafe-inline\'"><title>' + title + '</title><style>' + css + '</style></head><body><main><header><h1>' + title + '</h1><p>' + status + ' · 版本 ' + str(report["revision"]) + '</p></header>' + ''.join(body) + '</main></body></html>'
