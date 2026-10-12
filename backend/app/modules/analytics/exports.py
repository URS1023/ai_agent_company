"""Download actual query results with escaped HTML and formula-safe spreadsheets."""
from __future__ import annotations

import csv
import html
import io
import json

from app.modules.analytics.queries import historical_result
from app.modules.system.service_helpers import commit_change


def _text(value) -> str:
    """导出用的文本化：None 变空串，容器转 JSON 文本，保证单元格可读且不丢结构。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv_cell(value) -> str:
    """CSV 单元格防公式注入：以 = + - @ 或制表/换行开头的文本前面加单引号。

    这是导出类接口的必要防护：表格软件会把这类内容当公式执行，等于让查询结果里的
    数据在用户本机获得代码执行能力，因此必须保持与 xlsx 分支同样的策略。
    """
    text = _text(value)
    if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def export_query(ctx, query_id: str, format: str) -> tuple[bytes, str, str]:
    """把已保存的查询结果导出为文件（POST /api/v1/analytics/export）。

    权限：``analytics.export`` + 该数据源当前可见（historical_result 会重新校验，
    因此撤销数据源后不能再导出旧结果）。返回 ``(字节流, MIME, 文件名)``。

    安全约束：不接受任何服务器输出路径（文件名由服务端按查询 ID 生成）；
    CSV 防公式注入、XLSX 清除非法字符、HTML 全字段转义，Markdown 转义表格分隔符。
    XLSX 额外带“数据来源”页，写明查询标识、数据源版本、是否截断与实际 SQL，
    让导出文件自带口径说明。副作用：提交一次导出审计记录，不执行新的查询。
    """
    result = historical_result(ctx, query_id, permission="analytics.export")
    names = [column.columnName for column in result.columns]
    title = "数据分析报告"
    query_kind = "HTTP 采集" if getattr(result, "queryType", "") == "http" else "SQL 查询"
    scope = (f'本次返回 {result.totalCount} 行；已截断，仅覆盖部分查询结果。' if result.truncated
             else f'本次返回 {result.totalCount} 行；覆盖本次 {query_kind} 返回范围，不代表整个数据源。')
    if format == "json":
        data = result.model_dump_json(indent=2).encode("utf-8")
        mime, extension = "application/json", "json"
    elif format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        if result.truncated:
            # 截断 CSV 自带说明，文件离开页面后也不能被当成全量；完整 CSV 保持纯表格合同。
            writer.writerow(['# 数据范围', scope])
            writer.writerow(['# 查询标识', _csv_cell(query_id)])
        writer.writerow([_csv_cell(name) for name in names])
        writer.writerows([_csv_cell(row.get(name)) for name in names] for row in result.data)
        data, mime, extension = stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "csv"
    elif format == "xlsx":
        from openpyxl import Workbook
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Font, PatternFill
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "查询结果"
        for row_number, values in enumerate([names] + [[row.get(name) for name in names] for row in result.data], 1):
            for column_number, value in enumerate(values, 1):
                cell = sheet.cell(row_number, column_number)
                if isinstance(value, (str, dict, list)) or (type(value) is int and len(str(abs(value))) > 15):
                    # Excel 数值单元格最多可靠保留 15 位有效数字，长整数标识必须写为文本。
                    cell.value = ILLEGAL_CHARACTERS_RE.sub("", _text(value))
                    cell.data_type = "s"
                else:
                    cell.value = value
                if row_number == 1:
                    cell.font = Font(name='SimSun', color="FFFFFF", bold=True)
                    cell.fill = PatternFill("solid", fgColor="173B5E")
                else:
                    cell.font = Font(name='SimSun')
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        origin = workbook.create_sheet("数据来源")
        for key, value in (("查询标识", query_id), ("数据源标识", result.datasourceId),
                           ("数据源版本", result.datasourceVersion), ("样本行数", result.totalCount),
                           ("已截断", str(result.truncated)), ("查询类型", query_kind), ("数据范围", scope),
                           ("统计说明", result.analysis), ("实际 SQL", result.generatedSql)):
            origin.append([key, value])
            for cell in origin[origin.max_row]:
                cell.font = Font(name='SimSun')
            if isinstance(value, str):
                origin.cell(origin.max_row, 2).data_type = "s"
        output = io.BytesIO()
        workbook.save(output)
        data, mime, extension = output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    elif format == "markdown":
        def markdown_cell(value):
            """Markdown 表格单元格转义：先转义反斜杠与竖线，再把换行压成 <br>。"""
            return _text(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
        fence = chr(96) * 3
        lines = ["# " + title, "", scope, "", result.analysis, "", "查询标识：" + query_id, "", fence + "sql", result.generatedSql, fence, "",
                 "| " + " | ".join(map(markdown_cell, names)) + " |", "| " + " | ".join("---" for _ in names) + " |"]
        lines.extend("| " + " | ".join(markdown_cell(row.get(name)) for name in names) + " |" for row in result.data)
        data, mime, extension = "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8", "md"
    else:
        heading = "".join("<th>" + html.escape(name) + "</th>" for name in names)
        rows = "".join("<tr>" + "".join("<td>" + html.escape(_text(row.get(name))) + "</td>" for name in names) + "</tr>" for row in result.data)
        document = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>数据分析报告</title><style>body{font:15px/1.7 SimSun,'宋体',serif;margin:32px;color:#173b5e}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd6e2;padding:8px;text-align:left}"
            "th{background:#edf3f9}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>" + title + "</h1>"
            "<p>" + html.escape(scope) + "</p><p>" + html.escape(result.analysis).replace("\n", "<br>") + "</p><p>数据源：" + html.escape(result.datasourceId)
            + " · 查询：" + html.escape(query_id) + "</p><pre>" + html.escape(result.generatedSql)
            + "</pre><table><thead><tr>" + heading + "</tr></thead><tbody>" + rows + "</tbody></table></html>"
        )
        data, mime, extension = document.encode("utf-8"), "text/html; charset=utf-8", "html"
    commit_change(ctx, "analytics.export", "analytics", query_id, {"format": format, "rows": result.totalCount})
    return data, mime, f"analysis-{query_id}.{extension}"
