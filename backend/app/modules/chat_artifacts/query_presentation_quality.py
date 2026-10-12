"""查询 PPT 的范围证明和发布前验收，只使用保存回执与实际文件。"""
from __future__ import annotations

from pathlib import Path
import re

from ...errors import ValidationError
from ..analytics.finance_semantics import finance_categories


_CATEGORY_LABELS = {"receivable": "应收", "receipt": "收款", "payable": "应付", "payment": "付款"}


def _qualified_scope_title(title: str, missing: set[str]) -> str:
    """已有明确待补充标注时不重复附加相同类别。"""
    labels = "、".join(_CATEGORY_LABELS[key] for key in sorted(missing)
        if not re.search(re.escape(_CATEGORY_LABELS[key]) + r".{0,5}(?:未验证|未覆盖|待补充|未确认|未提供|待确认)", title))
    return title + f"（{labels}未验证）" if labels else title


def query_financial_scope(queries, title: str) -> tuple[str, list[str], dict]:
    """为不同交付格式统一核对标题财务口径，只读查询对象，不访问数据库。"""
    verified, sources = set(), []
    for query in queries:
        usage = getattr(query, "model_usage_json", None) or {}
        semantic = usage.get("businessSemantics") if isinstance(usage, dict) else None
        valid = (isinstance(semantic, dict) and semantic.get("method") == "authorized_metadata_ast"
                 and semantic.get("status") in {"validated", "context_only"} and semantic.get("monetaryFields"))
        categories = set(semantic.get("actualCategories") or []).intersection(_CATEGORY_LABELS) if valid else set()
        verified.update(categories)
        sources.append({"query_id": query.id, "verified_categories": sorted(categories)})
    claimed = finance_categories(title)
    missing = claimed - verified
    notes = []
    if missing:
        labels = "、".join(_CATEGORY_LABELS[key] for key in sorted(missing))
        notes.append(f"已保存查询尚无经过财务口径验证的{labels}数据，本次交付不提供相关金额结论；未验证不等于金额为零，也不能据此认定数据库不存在该业务。")

    return _qualified_scope_title(title, missing), notes, {
        "method": "saved_authorized_metadata_ast", "verified_categories": sorted(verified),
        "unverified_title_categories": sorted(missing), "sources": sources}


def finance_scope(queries, title: str, filename: str) -> tuple[str, str, list[str], dict]:
    """PPT 同时校验封面及文件名；两者不能通过换名称绕过同一范围规则。"""
    stem = Path(filename).stem
    _, notes, evidence = query_financial_scope(queries, title + " " + stem)
    missing = set(evidence["unverified_title_categories"])
    return _qualified_scope_title(title, missing), _qualified_scope_title(stem, missing) + ".pptx", notes, evidence


def _normalized(text) -> str:
    """忽略排版换行和空格，保留正文、数字及标点的实际内容。"""
    return re.sub(r"\s+", "", str(text or ""))


def _visible_text(slide) -> str:
    """读取真实页面文本及表格，备注不得冒充页面可见内容。"""
    return "\n".join(shape.text if shape.has_text_frame else "\n".join(
        cell.text for row in shape.table.rows for cell in row.cells) if shape.has_table else ""
        for shape in slide.shapes)


def validate_report(deck, spec: dict, scopes: list[dict]) -> dict:
    """验收真实文件中的来源、范围、可见值及原生图表；失败时不得登记工件。"""
    if len(deck.slides) != 1 + len(spec["slides"]):
        raise ValidationError("PPT 实际页数与汇报内容不一致，未发布文件")
    if not scopes or any(not scope.get("source_values_sha256") or not scope.get("query_id")
                         or scope.get("full_data_location") != "speaker_notes" for scope in scopes):
        raise ValidationError("PPT 缺少可核对的保存来源和原始数据位置，未发布文件")

    def require(text, value, page):
        if _normalized(value) not in _normalized(text):
            raise ValidationError(f"PPT 第 {page} 页缺少预期的业务内容，未发布文件")

    require(_visible_text(deck.slides[0]), spec["title"], 1)
    actual_notes = []
    for index, expected in enumerate(spec["slides"], start=1):
        slide, page = deck.slides[index], index + 1
        visible = _visible_text(slide)
        require(visible, expected["title"], page)
        if expected.get("key_message"):
            require(visible, expected["key_message"], page)
        notes = slide.notes_slide.notes_text_frame.text
        if expected.get("notes") and expected["notes"] not in notes:
            raise ValidationError(f"PPT 第 {page} 页未完整保留原始快照备注，未发布文件")
        actual_notes.append(notes)
        for item in expected.get("items") or []:
            if isinstance(item, dict) and item.get("type") == "table":
                expected_rows = [[_normalized(value) for value in row] for row in item.get("rows") or []]
                tables = [[[_normalized(cell.text) for cell in row.cells] for row in shape.table.rows]
                          for shape in slide.shapes if shape.has_table]
                if expected_rows not in tables:
                    raise ValidationError(f"PPT 第 {page} 页明细预览与查询不一致，未发布文件")
            elif isinstance(item, dict):
                for key in ("title", "value", "text", "description"):
                    if item.get(key) is not None:
                        if key == "value" and _normalized(item[key]) not in {
                            _normalized(shape.text) for shape in slide.shapes if shape.has_text_frame
                        }:
                            raise ValidationError(f"PPT 第 {page} 页指标值与查询不一致，未发布文件")
                        require(visible, item[key], page)
            elif isinstance(item, str) and expected["layout"] == "content":
                require(visible, item, page)
        chart = expected.get("chart")
        if chart:
            charts = [shape.chart for shape in slide.shapes if shape.has_chart]
            if len(charts) != 1 or len(charts[0].series) != len(chart["series"]):
                raise ValidationError(f"PPT 第 {page} 页缺少实际业务图表，未发布文件")
            categories = [item.label for item in charts[0].plots[0].categories]
            if categories != chart["categories"]:
                raise ValidationError(f"PPT 第 {page} 页图表分类与查询不一致，未发布文件")
            for expected_series, actual_series in zip(chart["series"], charts[0].series):
                if list(actual_series.values) != expected_series["values"]:
                    raise ValidationError(f"PPT 第 {page} 页图表数值与查询不一致，未发布文件")
    for scope in scopes:
        if not any(scope["source_values_sha256"] in notes and scope["query_id"] in notes for notes in actual_notes):
            raise ValidationError("PPT 未实际保留来源证明与完整快照，未发布文件")
    return {"status": "passed", "validator": "saved_query_presentation.v1", "page_count": len(deck.slides),
            "source_count": len(scopes), "checks": ["source_snapshot_notes", "visible_scope_and_values", "native_chart_values", "page_count"]}
