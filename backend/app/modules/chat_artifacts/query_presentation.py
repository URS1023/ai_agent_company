"""把已保存的查询快照直接绑定到 PPT，避免再次查询和模型重抄数值。"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path

from ...errors import PermissionErrorEx, ValidationError
from ...models import Run
from ...providers.tool.base import ToolResult
from .chart_renderer import load_authorized_query


_ROWS_PER_PAGE = 7
_COLUMNS_PER_PAGE = 5
_MAX_PAGES = 160
_CELL_CHARACTERS = 36


def _json(value) -> str:
    """数值字符串和空值按原始类型保留在备注及来源摘要中。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def presentation_filename(value: str | None, title: str) -> str:
    """生成时确定文件名，扩展名不能把 PPT 冒充其他格式。"""
    from ..files.service import normalize_filename

    if value is not None and not isinstance(value, str):
        raise ValidationError("PPT 导出文件名必须是文本")
    name = normalize_filename(value if value is not None else title)
    extension = Path(name).suffix.lower()
    if extension and extension != ".pptx":
        raise ValidationError("PPT 导出文件名必须使用 .pptx 扩展名")
    return normalize_filename(name if extension else name + ".pptx")


def _source(query, index: int) -> dict:
    """校验实际保存字段，来源分开呈现，不把跨来源、跨单位数据相加。"""
    result = query.result_json
    if not isinstance(result, dict) or not isinstance(result.get("truncated"), bool):
        raise ValidationError("保存的查询缺少明确截断标记，不能确定 PPT 数据范围")
    rows = result.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("保存的查询结果缺少有效行数据")
    if type(result.get("totalCount")) is not int or result["totalCount"] != len(rows):
        raise ValidationError("保存的查询行数与实际数据不一致，请核对查询回执")
    columns = result.get("columns")
    if not isinstance(columns, list) or any(not isinstance(column, dict) for column in columns):
        raise ValidationError("保存的查询字段格式无效")
    names, labels = [], {}
    for column in columns:
        name = column.get("columnName")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValidationError("保存的查询字段缺失或重复，不能准确绑定 PPT 列")
        names.append(name)
        labels[name] = str(column.get("comment") or name)
    for row in rows:
        for name in row:
            if not isinstance(name, str) or not name:
                raise ValidationError("保存的查询包含无效字段名")
            if name not in names:
                names.append(name)
                labels[name] = name
    if not names:
        raise ValidationError("保存的查询缺少字段信息，不能生成可追溯的 PPT")
    label = str(query.query_text or "").strip()
    if query.query_mode == "sql" or not label or len(label) > 52:
        label = f"查询结果 {index + 1}"
    scope = {"query_id": query.id, "datasource_id": query.datasource_id,
             "datasource_version": query.datasource_version,
             "queried_at": query.created_at.isoformat(), "time_zone": "UTC", "returned_rows": len(rows),
             "column_count": len(names), "truncated": result["truncated"],
             "scope": "saved_query_result", "aggregation": "none"}
    try:
        scope["source_values_sha256"] = sha256(_json({"columns": names, "data": rows}).encode("utf-8")).hexdigest()
    except (ValueError, TypeError):
        raise ValidationError("保存的查询包含无法忠实呈现的数值或字段") from None
    return {"query": query, "scope": scope, "names": names, "labels": labels, "rows": rows, "label": label}


def _cell(value, notes: list[str], *, reference: str, maximum: int = _CELL_CHARACTERS) -> str:
    """长文本在页面明确标注备注位置；原值完整留在同页真实备注中。"""
    if value is None:
        text = "未提供（空值）"
    elif isinstance(value, (dict, list, bool)):
        text = _json(value)
    else:
        text = str(value)
    if len(text) <= maximum and "\n" not in text and "\r" not in text:
        return text
    notes.append(f"{reference}：{text}")
    return text[:max(1, maximum - 9)].replace("\n", " ").replace("\r", " ") + f"…[注{len(notes)}]"


def query_presentation_spec(queries: list, title: str) -> tuple[dict, list[dict]]:
    """服务端排版所有返回行列；分页不会合计数值，也不会静默截掉宽表。"""
    sources = [_source(query, index) for index, query in enumerate(queries)]
    page_count = 1 + sum(1 + math.ceil(len(source["rows"]) / _ROWS_PER_PAGE)
                         * math.ceil(len(source["names"]) / _COLUMNS_PER_PAGE) for source in sources)
    if page_count > _MAX_PAGES:
        raise ValidationError(f"完整数据需要 {page_count} 页，超过 {_MAX_PAGES} 页清晰交付上限；请缩小查询范围或导出 Excel，不会省略行列")
    slides, scopes = [], []
    for source in sources:
        query, rows, names, scope = source["query"], source["rows"], source["names"], source["scope"]
        scope_note = "已截断，仅为部分结果" if scope["truncated"] else "已保存查询的返回范围，不代表全库总量"
        provenance = {**scope, "query": query.query_text, "actual_sql": query.generated_sql,
                      "parameters": query.result_json.get("parameters") or {}, "columns": query.result_json.get("columns")}
        slides.append({
            "layout": "content", "title": source["label"],
            "subtitle": "数据范围与阅读说明",
            "items": [
                {"title": f"本次返回 {len(rows)} 行 · {len(names)} 个字段", "text": scope_note},
                {"title": "查询时间（UTC）", "text": scope["queried_at"]},
                {"title": "保留原始口径", "text": "单位沿用原字段；空值表示未提供，不能据此判断正常。各来源独立展示，不做跨单位合计。"},
                {"title": "完整数据", "text": "全部返回行列按页展示；[注] 标记的长字段保存在同页备注。" if rows else "本次查询未返回数据，保留无数据事实。"},
            ], "notes": "数据来源：\n" + _json(provenance),
        })
        table_pages, notes_cell_count = 0, 0
        for offset in range(0, len(rows), _ROWS_PER_PAGE):
            batch = rows[offset:offset + _ROWS_PER_PAGE]
            for column_offset in range(0, len(names), _COLUMNS_PER_PAGE):
                columns = names[column_offset:column_offset + _COLUMNS_PER_PAGE]
                notes = []
                headers = ["原始行号"] + [_cell(source["labels"][name], notes, reference=f"表头 {name}", maximum=24) for name in columns]
                body = []
                for row_number, row in enumerate(batch, start=offset + 1):
                    body.append([str(row_number)] + [
                        _cell(row[name], notes, reference=f"原始行 {row_number} / 字段 {name}")
                        if name in row else "未返回字段" for name in columns
                    ])
                scope_text = f"第 {offset + 1}–{offset + len(batch)} 行 · 字段 {column_offset + 1}–{column_offset + len(columns)} · {scope_note}"
                if notes:
                    scope_text += " · [注] 全文见本页备注"
                note_payload = {"source": provenance, "row_offset": offset, "columns": columns,
                                "rows": [{key: row[key] for key in columns if key in row} for row in batch]}
                slides.append({
                    "layout": "table", "title": source["label"], "subtitle": scope_text,
                    # 客户模板不一定绘制 subtitle，正文同时保留范围说明。
                    "items": [scope_text, {"type": "table", "rows": [headers, *body]}],
                    "notes": "本页原始字段和值（不补零、不合计）：\n" + _json(note_payload)
                             + ("\n长字段完整内容：\n" + "\n".join(f"[注{index + 1}] {value}" for index, value in enumerate(notes)) if notes else ""),
                })
                table_pages += 1
                notes_cell_count += len(notes)
        scope.update({"table_pages": table_pages, "notes_cells": notes_cell_count,
                      "displayed_rows": len(rows), "displayed_columns": len(names)})
        scopes.append(scope)
    return {"title": title, "subtitle": "业务查询快照 · 来源可追溯 · 数值直接绑定",
            "include_cover": True, "include_closing": False, "page_count": "auto",
            "cover_notes": "本文件由已授权的保存查询生成，不重新取数、不使用模型生成的数据。各来源详见对应页备注。",
            "slides": slides}, scopes


def export_query_presentation(ctx, tool_context: dict, args: dict) -> ToolResult:
    """核对查询权限及运行归属，将同一保存结果直接交付为可编辑 PPT。"""
    from ..artifact.renderers import render_presentation
    from ..artifact.service import artifact_reference, create_artifact
    from ..presentation.service import resolve_template
    from pptx import Presentation

    query_ids = args.get("query_ids")
    if (not isinstance(query_ids, list) or not 1 <= len(query_ids) <= 8
            or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in query_ids)
            or len(set(query_ids)) != len(query_ids)):
        raise ValidationError("query_ids 需要 1 至 8 个不重复的已成功查询标识")
    title = args.get("title", "业务查询数据报告")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise ValidationError("PPT 标题长度应为 1 至 120 个字符")
    title = title.strip()
    filename = presentation_filename(args.get("name"), title)
    queries = [load_authorized_query(ctx, tool_context, query_id) for query_id in query_ids]
    run = ctx.db.get(Run, tool_context.get("run_id"))
    if run is None or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        raise PermissionErrorEx("PPT 导出缺少属于当前用户的运行记录")
    selected = set(tool_context.get("selected_datasource_ids") or [])
    if selected and any(query.datasource_id not in selected for query in queries):
        raise PermissionErrorEx("PPT 查询来源超出本次工具的数据源范围")
    mode = args.get("presentation_mode", "report")
    if mode not in {"report", "snapshot"}:
        raise ValidationError("presentation_mode 仅支持 report 汇报或 snapshot 完整快照")
    scope_notes = args.get("scope_notes", [])
    if (not isinstance(scope_notes, list) or len(scope_notes) > 10
            or any(not isinstance(note, str) or not note.strip() or len(note) > 400 for note in scope_notes)):
        raise ValidationError("scope_notes 需要最多 10 条、每条 1 至 400 字的真实范围说明")
    from .query_presentation_quality import finance_scope, validate_report
    title, filename, automatic_notes, financial_scope = finance_scope(queries, title, filename)
    filename = presentation_filename(filename, title)
    scope_notes = list(dict.fromkeys([*automatic_notes, *scope_notes]))
    if mode == "report":
        from .query_presentation_report import report_spec
        spec, scopes = report_spec([_source(query, index) for index, query in enumerate(queries)], title, scope_notes)
    else:
        spec, scopes = query_presentation_spec(queries, title)
        for offset in range(0, len(scope_notes), 5):
            spec["slides"].insert(offset // 5, {"layout": "content", "title": "本次范围与待补充事项", "items": scope_notes[offset:offset + 5]})
    if 1 + len(spec["slides"]) > _MAX_PAGES:
        raise ValidationError(f"当前内容超过 {_MAX_PAGES} 页交付上限，请减少查询或改用 Excel，不会静默删页")
    snapshot = run.context_snapshot or {}
    inputs = snapshot.get("plugin_inputs") or {}
    selected_pages = inputs.get("page_count")
    if selected_pages not in (None, "", "auto") and str(selected_pages) != str(1 + len(spec["slides"])):
        raise ValidationError(f"本次要求 {selected_pages} 页，当前{('汇报' if mode == 'report' else '完整查询快照')}需要 {1 + len(spec['slides'])} 页；请调整汇报内容或查询范围，不会忽略页数要求")
    template = resolve_template(ctx.db, ctx.workspace_id, snapshot.get("template_id") or inputs.get("template_id") or "executive-ivory")
    spec["template_id"] = template["id"]
    spec["aspect_ratio"] = inputs.get("aspect_ratio") or "16:9"
    if template.get("template_path"):
        spec["_template_path"] = template["template_path"]
    evidence = {"kind": "pptx", "producer": "server_renderer.v1", "source_query_ids": query_ids,
                "source_scopes": scopes, "scope": "saved_query_result", "data_binding": "saved_query_snapshot",
                "truncated": any(scope["truncated"] for scope in scopes), "page_count": 1 + len(spec["slides"]),
                "presentation_mode": mode, "scope_notes": scope_notes, "financial_scope": financial_scope,
                "automatic_scope_notes": automatic_notes}
    data = render_presentation(spec)
    deck = Presentation(BytesIO(data))
    if len(deck.slides) != evidence["page_count"]:
        raise ValidationError("PPT 实际页数与完整数据分页不一致，未发布文件，请核对模板")
    if mode == "report":
        evidence["quality_validation"] = validate_report(deck, spec, scopes)
    row = create_artifact(ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, owner_user_id=ctx.user_id,
        type="presentation", name=filename, data=data,
        metadata={"tool": "export_query_presentation", "delivery_evidence": evidence, "font": "宋体",
                  "template_id": template["id"], "template_source": template.get("source")})
    return ToolResult(status="success", data={"artifact_id": row.id, "name": filename, "query_ids": query_ids,
        "page_count": evidence["page_count"], "scopes": scopes, "truncated": evidence["truncated"],
        "presentation_mode": mode, "scope_notes": scope_notes,
        "description": ("使用保存的查询制作汇报；页面展示指标、分组对比及受限明细预览，完整原始行列在备注，不能声称全部明细在页面展示。"
                        if mode == "report" else "直接使用保存的查询结果制作 PPT；各来源独立展示，全部返回行列分页保留，长字段完整留在同页备注。")},
        artifacts=[artifact_reference(row)], metadata={"delivery_evidence": evidence})
