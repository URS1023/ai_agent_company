"""Normalize imperfect model-authored PresentationSpec payloads."""
from __future__ import annotations

import json
import re
from copy import deepcopy


LAYOUT_ALIASES = {
    "cover": "cover", "title_slide": "cover", "title-page": "cover",
    "toc": "agenda", "目录": "agenda", "chapter": "section",
    "title": "statement", "hero": "statement", "key_message": "statement",
    "bullet": "content", "bullets": "content", "list": "content",
    "two-column": "two_column", "columns": "two_column",
    "kpi": "metrics", "data": "chart", "closing_page": "closing",
}

TITLE_FALLBACKS = {
    "agenda": "内容导航",
    "section": "章节过渡",
    "statement": "核心判断",
    "content": "关键内容",
    "two_column": "两类信息需要同步理解",
    "cards": "核心能力与业务模块",
    "metrics": "关键指标概览",
    "comparison": "两种模式的差异",
    "timeline": "实施路径与阶段安排",
    "table": "结构化信息汇总",
    "chart": "数据趋势与核心洞察",
    "quote": "共同原则",
    "image": "图文说明",
    "matrix": "四象限分析",
    "closing": "谢谢",
}

CHART_TYPE_ALIASES = {
    "bar": "bar",
    "horizontal_bar": "bar",
    "horizontalbar": "bar",
    "column": "column",
    "vertical_bar": "column",
    "verticalbar": "column",
    "line": "line",
    "pie": "pie",
    "doughnut": "doughnut",
    "donut": "doughnut",
    "area": "area",
}


def _container_stack(text: str) -> list[str]:
    """扫描文本中未闭合的 ``{``/``[`` 容器（忽略字符串内的括号），返回按嵌套顺序的栈。

    用于在 JSON 被截断时补全尾部括号；进入字符串后按转义规则跳过，避免把正文里的括号
    误当成结构符号。
    """

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char == "]" and stack and stack[-1] == "[":
            stack.pop()
        elif char == "}" and stack and stack[-1] == "{":
            stack.pop()
    return stack


def _repair_json_payload(text: str) -> dict:
    """尽力修复常见模型 JSON 缺陷后解析（最多 8 轮修补）。

    处理：尾部多余括号、被截断导致括号不闭合（按栈补 ``}``/``]``）、数组元素间缺逗号时
    回插方括号、缺属性名引号时补引号。修补仍失败则抛出原始 ``JSONDecodeError``——
    本函数只做语法修补，**不会猜测缺失的业务字段**。
    """

    candidate = text
    for _ in range(8):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            if exc.msg.lower().startswith("extra data"):
                trailing = candidate[exc.pos:].strip()
                if trailing and set(trailing).issubset(set("}]")):
                    candidate = candidate[:exc.pos]
                    continue
            stack = _container_stack(candidate[: exc.pos])
            if exc.pos >= len(candidate) - 1 and stack:
                stripped = candidate.rstrip()
                if stripped.endswith("]") and stack[-1] == "{":
                    insert_at = candidate.rfind("]")
                    candidate = candidate[:insert_at] + "}" + candidate[insert_at:]
                    continue
                if stripped.endswith("}") and stack[-1] == "[":
                    insert_at = candidate.rfind("}")
                    candidate = candidate[:insert_at] + "]" + candidate[insert_at:]
                    continue
                candidate += "".join("}" if value == "{" else "]" for value in reversed(stack))
                continue
            if stack and stack[-1] == "[" and "delimiter" in exc.msg.lower():
                matches = list(re.finditer(r",\s*\"[A-Za-z_][A-Za-z0-9_]*\"\s*:", candidate[: exc.pos + 1]))
                if matches:
                    insert_at = matches[-1].start()
                    candidate = candidate[:insert_at] + "]" + candidate[insert_at:]
                    continue
            if "property name" in exc.msg.lower():
                candidate = candidate[: exc.pos] + '"' + candidate[exc.pos:]
                continue
            raise
    return json.loads(candidate)


def parse_json_value(raw):
    """把模型输出解析成 JSON 值：先剥 Markdown 围栏、截取最外层对象/数组、修正中文引号，
    常规解析失败再走 ``_repair_json_payload``。已是 dict/list 时直接深拷贝返回。

    抛 ``ValueError`` 表示内容不是对象/数组/字符串；解析仍失败则抛 JSON 解析异常，
    由上层转成业务错误（本函数不做静默兜底）。
    """

    if isinstance(raw, (dict, list)):
        return deepcopy(raw)
    if not isinstance(raw, str):
        raise ValueError("JSON 内容必须是对象、数组或字符串")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(
        r'(?<=[\u3400-\u9fff])"(?=[\u3400-\u9fff：；，。])',
        "”",
        text,
    )
    starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
    first = min(starts) if starts else -1
    last = max(text.rfind("}"), text.rfind("]"))
    if first >= 0 and last > first:
        text = text[first:last + 1]
    try:
        return json.loads(text)
    except Exception:
        return _repair_json_payload(text)


def parse_json_array_partial(raw) -> list:
    """从可能被截断的模型输出里**逐个**回收完整数组元素（不要求整体合法）。

    先尝试整体解析数组；失败则用增量解码器从 ``[`` 起逐个读取元素，遇到不完整元素停止。
    用于分批写页场景：宁可拿到前几页并让后续重试，也不要因为最后一页被截断而丢掉全部内容。
    未找到数组起始符时抛 ``ValueError``。
    """

    try:
        value = parse_json_value(raw)
        if isinstance(value, list):
            return value
    except Exception:
        pass
    if not isinstance(raw, str):
        raise ValueError("批次内容必须是数组或 JSON 数组字符串")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(
        r'(?<=[\u3400-\u9fff])"(?=[\u3400-\u9fff：；，。])',
        "”",
        text,
    )
    start = text.find("[")
    if start < 0:
        raise ValueError("未找到 JSON 数组起始符")
    decoder = json.JSONDecoder()
    position = start + 1
    recovered = []
    while position < len(text):
        while position < len(text) and (text[position].isspace() or text[position] == ","):
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        try:
            value, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            recovered.append(value)
        position = end
    if not recovered:
        raise ValueError("批次 JSON 中没有可恢复的页面")
    return recovered


def parse_presentation_spec(raw) -> dict:
    """解析模型返回的演示文稿 spec：字符串走 ``parse_json_value``，已是字典直接使用。

    兼容"外层再包一层 ``presentation_spec``"的写法；解析失败或结果不是对象时抛 ``ValueError``。
    只做解析不做字段校验，字段归一由 ``normalize_presentation_spec`` 负责。
    """

    try:
        value = parse_json_value(raw)
    except Exception as exc:
        raise ValueError(f"presentation_spec JSON 字符串无法解析：{str(exc)[:160]}")
    if not isinstance(value, dict):
        raise ValueError("presentation_spec 必须解析为对象")
    nested = value.get("presentation_spec") if isinstance(value, dict) else None
    return deepcopy(nested) if isinstance(nested, dict) else value


def _item_title(item) -> str:
    """取条目标题（兼容 title/label/name/heading 与嵌套 value 结构）；非字典返回空串。"""

    if not isinstance(item, dict):
        return ""
    return str(item.get("title") or item.get("label") or item.get("name") or "").strip()


def _plain_text(value) -> str:
    """Turn a model-authored scalar/list into audience-facing text.

    Some model providers wrap card and metric content in ``value`` objects,
    for example ``{"value": {"label": "库存", "data": "降低 20%"}}``.
    Stringifying that object leaks Python/JSON syntax into the slide. Keep the
    semantic fields and discard the transport wrapper instead.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, list):
        return "；".join(
            text for text in (_plain_text(item) for item in value) if text
        )
    if isinstance(value, dict):
        title = _plain_text(
            value.get("title") or value.get("label") or value.get("name")
            or value.get("point")
        )
        detail = _plain_text(
            value.get("description") or value.get("data") or value.get("text")
            or value.get("content") or value.get("summary")
        )
        if title and detail and title != detail:
            return f"{title}：{detail}"
        return detail or title
    return str(value).strip()


def _normalize_narrative_item(item: dict) -> dict:
    """Normalize card/metric/step dialects before layout selection."""
    normalized = deepcopy(item)
    item_type = str(normalized.get("type") or "").strip().lower().replace("-", "_")
    nested_value = normalized.get("value")

    if isinstance(nested_value, dict):
        nested_title = _plain_text(
            nested_value.get("title") or nested_value.get("label")
            or nested_value.get("name") or nested_value.get("point")
        )
        nested_data = _plain_text(
            nested_value.get("data") or nested_value.get("description")
            or nested_value.get("text") or nested_value.get("content")
            or nested_value.get("summary")
        )
        if nested_title and not (
            normalized.get("title") or normalized.get("label") or normalized.get("name")
        ):
            normalized["title"] = nested_title
        if item_type == "metric":
            metric_value = nested_value.get("value")
            if metric_value is None or metric_value == "":
                metric_value = nested_value.get("number")
            if metric_value is None or metric_value == "":
                metric_value = nested_value.get("data")
            normalized["value"] = _plain_text(metric_value)
            if normalized["value"] == "":
                normalized["value"] = "—"
            nested_description = _plain_text(
                nested_value.get("description") or nested_value.get("text")
                or nested_value.get("content") or nested_value.get("summary")
            )
            if nested_description and not normalized.get("description"):
                normalized["description"] = nested_description
        else:
            normalized.pop("value", None)
            if nested_data and not normalized.get("description"):
                normalized["description"] = nested_data

    normalized["title"] = (
        normalized.get("title") or normalized.get("标题")
        or normalized.get("name") or normalized.get("label")
        or normalized.get("point") or normalized.get("heading") or ""
    )
    if not normalized.get("description"):
        direct_description = (
            normalized.get("explanation") or normalized.get("解释")
            or normalized.get("根因解释") or normalized.get("detail")
            or normalized.get("summary") or normalized.get("text")
            or normalized.get("content") or normalized.get("body")
            or normalized.get("data")
        )
        if direct_description not in (None, ""):
            normalized["description"] = _plain_text(direct_description)
    return normalized


def _generic_panel_title(value) -> bool:
    """判断标题是否为"通用面板名"（如核心指标/内容/说明），这类标题不承载业务信息，
    因此归一化时会尝试用正文内容替换或丢弃，避免页面出现无意义的占位标题。"""

    normalized = re.sub(r"[\s_-]", "", str(value or "")).casefold()
    return normalized in {
        "", "方案a", "方案b", "左侧", "右侧", "左侧要点", "右侧要点",
        "patha", "pathb", "optiona", "optionb",
    }


def _comparison_titles_from_slide_title(value: str) -> tuple[str, str] | None:
    """Recover semantic panel labels from common comparison titles."""
    title = str(value or "").strip(" ：:—-\t")
    if not title:
        return None

    match = re.match(
        r"^(.+?)\s*(?:vs\.?)\s*(.+?)\s*(?:对比|比较|差异)?$",
        title,
        flags=re.I,
    )
    if match:
        left, right = (part.strip(" ：:—-") for part in match.groups())
        if left and right:
            return left, right

    trimmed = re.sub(r"\s*(?:对比|比较|差异)\s*$", "", title)
    if "与" in trimmed:
        left, right = (part.strip(" ：:—-") for part in trimmed.split("与", 1))
        if left and right:
            return left, right
    if trimmed.endswith("前后") and len(trimmed) > 2:
        stem = trimmed[:-2]
        return f"{stem}前", f"{stem}后"
    return None


_CHINESE_COUNT_WORDS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _reconcile_large_count_claim(title: str, layout: str, items: list) -> str:
    """Remove a contradictory ``X 大`` claim without dropping valid content.

    Models sometimes title a page ``五大核心痛点`` and then provide eight
    complete items.  Keeping all useful content is preferable to truncating it,
    but the audience must not see an internally inconsistent heading.  This is
    intentionally limited to narrative list/card pages so table row counts and
    unrelated numbers are never rewritten.
    """
    if layout not in {"content", "agenda", "cards", "metrics"} or not items:
        return title
    match = re.search(r"([一二三四五六七八九十]|\d{1,2})\s*大(?=核心)?", title)
    if not match:
        return title
    token = match.group(1)
    claimed = _CHINESE_COUNT_WORDS.get(token)
    if claimed is None:
        try:
            claimed = int(token)
        except ValueError:
            return title
    if claimed == len(items):
        return title
    following = title[match.end():]
    replacement = "" if following.startswith("核心") else "核心"
    return (title[:match.start()] + replacement + following).strip()


def _normalize_layout(value) -> str:
    """把模型给的版式名归一为受支持版式（别名表 + 小写/下划线化）；未知时返回 content。"""

    layout = str(value or "").strip().lower().replace(" ", "_")
    return LAYOUT_ALIASES.get(layout, layout)


def _is_cover(title: str, layout: str) -> bool:
    """判断这一页是否是封面（按版式标签或标题关键词），用于避免正文批次写入封面页。"""

    normalized = re.sub(r"[\s：:·_-]", "", title).lower()
    return layout == "cover" or normalized in {"封面", "cover", "titleslide"}


def _normalize_chart_item(raw: dict) -> dict:
    """Convert common Chart.js/ECharts/model aliases to the renderer contract."""
    chart = deepcopy(raw)
    raw_item_type = str(chart.get("type") or "").strip().lower().replace("-", "_")
    chart_type = chart.get("chart_type") or chart.get("chartType")
    if not chart_type and raw_item_type not in {"", "chart", "data"}:
        chart_type = raw_item_type
    normalized_chart_type = str(chart_type or "column").strip().lower().replace("-", "_")
    normalized_chart_type = CHART_TYPE_ALIASES.get(normalized_chart_type, "column")

    categories = chart.get("categories")
    if categories is None:
        categories = chart.get("labels")
    if categories is None:
        axis = chart.get("x_axis") or chart.get("xAxis")
        categories = axis.get("data") if isinstance(axis, dict) else axis
    if not isinstance(categories, list):
        categories = []

    raw_series = chart.get("series")
    if raw_series is None:
        raw_series = chart.get("datasets")
    if isinstance(raw_series, dict):
        raw_series = [
            {"name": name, "values": values}
            for name, values in raw_series.items()
        ]
    if not isinstance(raw_series, list):
        raw_series = []

    series = []
    for index, raw_row in enumerate(raw_series, start=1):
        if isinstance(raw_row, dict):
            row = deepcopy(raw_row)
            values = row.get("values")
            if values is None:
                values = row.get("data")
            if not isinstance(values, list):
                values = []
            series.append({
                "name": str(
                    row.get("name") or row.get("label") or row.get("title")
                    or f"系列 {index}"
                ),
                "values": values,
            })
        elif isinstance(raw_row, list):
            series.append({"name": f"系列 {index}", "values": raw_row})

    normalized = {
        **chart,
        "type": "chart",
        "chart_type": normalized_chart_type,
        "categories": categories,
        "series": series,
    }
    normalized.pop("chartType", None)
    normalized.pop("datasets", None)
    normalized.pop("labels", None)
    return normalized


def _normalize_table_item(raw: dict) -> dict:
    """Preserve top-level and item-level table aliases as a row matrix."""
    table = deepcopy(raw)
    raw_headers = table.get("headers")
    if raw_headers is None:
        raw_headers = table.get("columns")
    raw_rows = table.get("rows")
    if raw_rows is None:
        raw_rows = table.get("data")
    if not isinstance(raw_rows, list):
        raw_rows = []

    header_labels: list = []
    field_keys: list = []
    if isinstance(raw_headers, list):
        for column in raw_headers:
            if isinstance(column, dict):
                key = column.get("key") or column.get("field") or column.get("name")
                label = column.get("title") or column.get("label") or key
                field_keys.append(key)
                header_labels.append(label)
            else:
                field_keys.append(column)
                header_labels.append(column)

    if raw_rows and all(isinstance(row, dict) for row in raw_rows):
        if not field_keys:
            field_keys = list(raw_rows[0].keys())
            header_labels = list(field_keys)
        usable = [
            (key, label)
            for key, label in zip(field_keys, header_labels)
            if key not in (None, "")
        ][:8]
        field_keys = [key for key, _label in usable]
        header_labels = [label for _key, label in usable]
        rows = [header_labels] + [
            [row.get(key, "") for key in field_keys]
            for row in raw_rows
        ]
    else:
        rows = [list(row) if isinstance(row, (list, tuple)) else [row] for row in raw_rows]
        if header_labels:
            header_labels = header_labels[:8]
            if not rows or [str(value) for value in rows[0]] != [str(value) for value in header_labels]:
                rows.insert(0, header_labels)

    normalized = {**table, "type": "table", "rows": rows}
    normalized.pop("headers", None)
    normalized.pop("columns", None)
    normalized.pop("data", None)
    return normalized


def _chart_payload_as_table(raw: dict) -> dict:
    """Recover model payloads that explicitly label a series matrix as a table."""
    categories = raw.get("categories") or raw.get("labels") or []
    headers = list(categories) if isinstance(categories, list) else []
    raw_series = raw.get("series") or raw.get("datasets") or []
    rows = []
    for index, series_item in enumerate(raw_series if isinstance(raw_series, list) else [], start=1):
        if isinstance(series_item, dict):
            name = series_item.get("name") or series_item.get("label") or f"系列 {index}"
            values = series_item.get("values")
            if values is None:
                values = series_item.get("data")
        else:
            name = f"系列 {index}"
            values = series_item
        values = values if isinstance(values, list) else []
        if len(headers) == len(values) + 1:
            rows.append([name, *values])
        elif len(headers) == 2:
            rows.append([name, "；".join(str(value) for value in values)])
        else:
            rows.append([name, *values])
    if not headers and rows:
        headers = ["类别"] + [f"内容 {index}" for index in range(1, len(rows[0]))]
    return {
        "type": "table",
        "rows": [headers, *rows] if headers else rows,
        "insight": raw.get("insight") or raw.get("key_insight"),
        "source": raw.get("source") or raw.get("data_source"),
    }


def normalize_slide(raw, index: int) -> dict | None:
    """把模型给的任意单页结构归一成标准页面：版式判定、条目安全化、标题兜底。

    ``index`` 用于生成缺省标题/编号。返回 None 表示该输入应被**丢弃**（典型是封面页，
    正文批次不允许写入封面）。归一过程中清理通用占位标题、把 ``value`` 包装对象展平为正文、
    补齐缺失字段；不会新增模型没有提供的事实性内容。
    """

    if isinstance(raw, str):
        raw = {"title": raw, "layout": "statement", "statement": raw}
    if not isinstance(raw, dict):
        return None
    slide = deepcopy(raw)
    layout = _normalize_layout(
        slide.get("layout") or slide.get("page_role") or slide.get("type")
    )
    title = str(
        slide.get("title") or slide.get("heading") or slide.get("name") or ""
    ).strip()
    if _is_cover(title, layout):
        return None

    items = (
        slide.get("items") or slide.get("content") or slide.get("cards")
        or slide.get("metrics") or []
    )
    if isinstance(items, dict):
        items = [items]
    elif isinstance(items, str):
        items = [line.strip("•- ") for line in items.splitlines() if line.strip()]
    elif not isinstance(items, list):
        items = []

    expanded_timeline = False
    expanded_items = []
    for item in items:
        if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "timeline":
            steps = item.get("steps")
            if steps is None and isinstance(item.get("value"), list):
                steps = item.get("value")
            if steps is None and isinstance(item.get("data"), list):
                steps = item.get("data")
            if isinstance(steps, list) and steps:
                expanded_items.extend(
                    _normalize_narrative_item(step)
                    if isinstance(step, dict) else step
                    for step in steps
                )
                expanded_timeline = True
                continue
        expanded_items.append(item)
    if expanded_timeline:
        items = expanded_items
        layout = "timeline"

    if layout == "metrics":
        for key in ("metric", "metric1", "metric2", "metric3", "metric4", "metric5", "metric6"):
            candidate = slide.get(key)
            if isinstance(candidate, dict) and candidate not in items:
                items.append(candidate)

    if slide.get("left") is None and slide.get("left_items") is not None:
        slide["left"] = slide.get("left_items")
    if slide.get("right") is None and slide.get("right_items") is not None:
        slide["right"] = slide.get("right_items")

    direct_comparison = (
        slide.get("comparison")
        if isinstance(slide.get("comparison"), dict)
        else None
    )
    if direct_comparison:
        for side in ("left", "right"):
            panel = direct_comparison.get(side)
            if isinstance(panel, dict):
                slide[f"{side}_title"] = (
                    slide.get(f"{side}_title")
                    or panel.get("title")
                    or panel.get("label")
                    or ("方案 A" if side == "left" else "方案 B")
                )
                panel = panel.get("items") or panel.get("content") or panel.get("values") or []
            if slide.get(side) is None and panel is not None:
                slide[side] = panel
        layout = "comparison"

    direct_table = slide.get("table") if isinstance(slide.get("table"), dict) else None
    if not direct_table and slide.get("rows") is not None and (
        layout == "table" or slide.get("headers") or slide.get("columns")
    ):
        direct_table = {
            "rows": slide.get("rows"),
            "headers": slide.get("headers") or slide.get("columns"),
            "source": slide.get("source"),
            "insight": slide.get("insight") or slide.get("key_message"),
        }

    direct_chart = slide.get("chart") if isinstance(slide.get("chart"), dict) else None
    if not direct_chart and slide.get("categories") and slide.get("series"):
        direct_chart = {
            "type": "chart",
            "chart_type": slide.get("chart_type") or "column",
            "categories": slide.get("categories"),
            "series": slide.get("series"),
            "insight": slide.get("insight") or slide.get("key_message"),
            "source": slide.get("source"),
        }
    if direct_chart:
        requested_chart_type = str(
            direct_chart.get("chart_type") or direct_chart.get("chartType") or ""
        ).strip().lower()
        if requested_chart_type == "table":
            items = [_chart_payload_as_table(direct_chart)]
            layout = "table"
        else:
            normalized_chart = _normalize_chart_item(direct_chart)
            categories = normalized_chart.get("categories") or []
            series = normalized_chart.get("series") or []
            complete_chart = (
                len(categories) >= 2
                and bool(series)
                and all(
                    len(row.get("values") or []) == len(categories)
                    for row in series if isinstance(row, dict)
                )
            )
            # Models occasionally provide an auxiliary chart together with a
            # complete table or comparison.  Respect the explicit, usable page
            # layout instead of replacing it with a malformed chart and then
            # asking the model to rewrite the same page forever.
            has_panels = bool(slide.get("left")) and bool(slide.get("right"))
            has_table = bool(
                direct_table
                and isinstance(direct_table.get("rows"), list)
                and len(direct_table.get("rows") or []) >= 2
            )
            has_narrative = len(items) >= 3
            usable_explicit_layout = (
                layout in {"comparison", "two_column"} and has_panels
            ) or (
                layout == "table" and has_table
            ) or (
                layout in {"content", "cards", "metrics", "timeline", "matrix"}
                and has_narrative
            )
            if complete_chart and not usable_explicit_layout:
                items = [normalized_chart]
                layout = "chart"
            elif not complete_chart and not usable_explicit_layout:
                # Keep the invalid chart only when there is no truthful usable
                # alternative.  The final quality check can then request a
                # focused repair rather than silently inventing data.
                items = [normalized_chart]
                layout = "chart"
            elif not complete_chart and has_narrative and layout not in {
                "comparison", "two_column", "table",
            }:
                layout = "cards" if 3 <= len(items) <= 4 else "content"
    if direct_table and layout == "table":
        items = [_normalize_table_item(direct_table)]
        layout = "table"

    if (
        layout == "comparison"
        and slide.get("left") is None
        and slide.get("right") is None
        and items
        and all(isinstance(item, dict) for item in items)
    ):
        paired = [
            item for item in items
            if item.get("描述1") is not None or item.get("描述2") is not None
        ]
        if paired:
            first = paired[0]
            slide["left_title"] = str(
                slide.get("left_title") or first.get("标题1") or first.get("标题") or "方案 A"
            )
            slide["right_title"] = str(
                slide.get("right_title") or first.get("标题2") or "方案 B"
            )
            slide["left"] = [str(item.get("描述1") or "") for item in paired if item.get("描述1")]
            slide["right"] = [str(item.get("描述2") or "") for item in paired if item.get("描述2")]
            items = []
        elif len(items) >= 3 and all(
            item.get("value") is not None or item.get("数值") is not None
            for item in items
        ):
            metric_items = []
            for item in items:
                detail = "；".join(
                    str(value).strip()
                    for value in (
                        item.get("target") or item.get("目标"),
                        item.get("achievement") or item.get("达成情况"),
                        item.get("explanation") or item.get("解释"),
                    )
                    if value not in (None, "")
                )
                metric_value = item.get("value")
                if metric_value is None or metric_value == "":
                    metric_value = item.get("数值")
                if metric_value is None or metric_value == "":
                    metric_value = "—"
                metric_items.append({
                    **item,
                    "type": "metric",
                    "title": item.get("title") or item.get("标题") or item.get("label") or "指标",
                    "value": metric_value,
                    "description": item.get("description") or detail,
                })
            items = metric_items
            layout = "metrics"

    if (
        layout == "table"
        and items
        and all(isinstance(item, dict) for item in items)
        and not any(str(item.get("type") or "").lower() == "table" for item in items)
    ):
        columns = []
        for item in items:
            for key in item:
                if key not in columns and key != "type":
                    columns.append(key)
        columns = columns[:6]
        rows = [columns] + [[item.get(key, "") for key in columns] for item in items]
        items = [{"type": "table", "rows": rows}]

    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            normalized_items.append(item)
            continue
        item_type = str(item.get("type") or "").strip().lower().replace("-", "_")
        if item_type == "chart" or (
            (item.get("categories") is not None or item.get("labels") is not None)
            and (item.get("series") is not None or item.get("datasets") is not None)
        ):
            normalized_items.append(_normalize_chart_item(item))
            continue
        if item_type == "table" or (
            item.get("rows") is not None
            and (item.get("headers") is not None or item.get("columns") is not None)
        ):
            normalized_items.append(_normalize_table_item(item))
            continue
        normalized = _normalize_narrative_item(item)
        if not normalized.get("description"):
            direct_description = (
                normalized.get("explanation") or normalized.get("解释")
                or normalized.get("根因解释") or normalized.get("detail")
                or normalized.get("summary") or normalized.get("text")
                or normalized.get("content") or normalized.get("body")
            )
            if direct_description:
                normalized["description"] = direct_description
            elif isinstance(normalized.get("details"), list):
                detail_lines = []
                for detail in normalized.get("details") or []:
                    if isinstance(detail, dict):
                        text = str(detail.get("text") or detail.get("title") or "").strip()
                        qualifier = str(
                            detail.get("impact") or detail.get("recommendation")
                            or detail.get("description") or ""
                        ).strip()
                        if text or qualifier:
                            detail_lines.append("：".join(value for value in (text, qualifier) if value))
                    elif detail not in (None, ""):
                        detail_lines.append(str(detail))
                if detail_lines:
                    normalized["description"] = "；".join(detail_lines)
            else:
                excluded = {
                    "type", "title", "标题", "name", "label", "value", "数值",
                    "description", "data", "text", "content", "target", "目标",
                    "achievement", "达成情况",
                }
                details = [
                    f"{key}：{value}" for key, value in normalized.items()
                    if key not in excluded and value not in (None, "")
                ]
                if details:
                    normalized["description"] = "；".join(details)
        normalized_items.append(normalized)
    items = normalized_items

    def flatten_panel(value):
        """把面板型条目（列表/字典）摊平成条目列表，保证后续版式拿到统一形状。"""
        if not isinstance(value, list):
            return value
        flattened = []
        for entry in value:
            nested_entries = None
            if isinstance(entry, dict):
                for key in ("details", "items", "content", "values"):
                    if isinstance(entry.get(key), list):
                        nested_entries = entry.get(key)
                        break
            if not isinstance(entry, dict) or nested_entries is None:
                flattened.append(
                    _normalize_narrative_item(entry)
                    if isinstance(entry, dict) else entry
                )
                continue
            group_title = str(entry.get("title") or entry.get("标题") or "").strip()
            for detail in nested_entries:
                if isinstance(detail, dict):
                    text = str(
                        detail.get("title") or detail.get("point")
                        or detail.get("label") or detail.get("text") or ""
                    ).strip()
                    qualifier = str(
                        detail.get("impact") or detail.get("recommendation")
                        or detail.get("description") or detail.get("text")
                        or detail.get("content") or ""
                    ).strip()
                    if qualifier == text:
                        qualifier = ""
                    body = "｜".join(value for value in (text, qualifier) if value)
                else:
                    body = str(detail or "").strip()
                if body:
                    flattened.append(f"{group_title}：{body}" if group_title else body)
        return flattened

    if slide.get("left") is not None:
        slide["left"] = flatten_panel(slide.get("left"))
    if slide.get("right") is not None:
        slide["right"] = flatten_panel(slide.get("right"))

    left_panel = slide.get("left") if isinstance(slide.get("left"), list) else []
    right_panel = slide.get("right") if isinstance(slide.get("right"), list) else []
    panel_items = [*left_panel, *right_panel]
    title_has_comparison_cue = bool(re.search(
        r"(?:对比|比较|差异|取舍|方案|路径|选择|前后|\bvs\b)",
        title,
        flags=re.I,
    ))
    has_panel_titles = bool(slide.get("left_title") or slide.get("right_title"))
    merge_balanced_cards = bool(panel_items) and (
        layout not in {"comparison", "two_column", "chart", "table"}
        or (
            layout == "comparison"
            and not has_panel_titles
            and not title_has_comparison_cue
        )
    )
    if merge_balanced_cards and len(panel_items) >= 3:
        items = panel_items
        slide.pop("left", None)
        slide.pop("right", None)
        layout = "cards" if len(panel_items) <= 4 else "content"

    if layout == "comparison" and panel_items:
        derived_titles = _comparison_titles_from_slide_title(title)
        if derived_titles:
            if _generic_panel_title(slide.get("left_title")):
                slide["left_title"] = derived_titles[0]
            if _generic_panel_title(slide.get("right_title")):
                slide["right_title"] = derived_titles[1]

    item_types = {
        str(item.get("type") or "").lower()
        for item in items if isinstance(item, dict)
    }
    if "chart" in item_types:
        layout = "chart"
    elif "table" in item_types:
        layout = "table"
    elif "metric" in item_types:
        layout = "metrics"
    elif "card" in item_types:
        layout = "cards"
    elif "quote" in item_types:
        layout = "quote"
    elif "timeline" in item_types:
        layout = "timeline"

    nested_comparison = next(
        (
            item for item in items
            if isinstance(item, dict) and (item.get("left") is not None or item.get("right") is not None)
        ),
        None,
    )
    if nested_comparison:
        slide["left"] = slide.get("left") or nested_comparison.get("left") or []
        slide["right"] = slide.get("right") or nested_comparison.get("right") or []
        slide["left_title"] = slide.get("left_title") or nested_comparison.get("left_title") or nested_comparison.get("title") or "方案 A"
        slide["right_title"] = slide.get("right_title") or nested_comparison.get("right_title") or "方案 B"
        layout = "comparison"
    elif slide.get("left") is not None or slide.get("right") is not None:
        layout = "comparison" if layout != "two_column" else layout

    if not layout:
        if title in {"目录", "议程"}:
            layout = "agenda"
        elif slide.get("statement") or slide.get("key_message"):
            layout = "statement" if not items else "content"
        elif 3 <= len(items) <= 4 and all(_item_title(item) for item in items if isinstance(item, dict)):
            layout = "cards"
        else:
            layout = "content"
    if title in {"目录", "议程"}:
        layout = "agenda"
    if layout in {"content", "agenda", "cards", "metrics"} and not items and (
        slide.get("statement") or slide.get("key_message")
    ):
        layout = "statement"
    if layout == "timeline" and "card" in item_types:
        layout = "cards"

    if not title:
        title = str(
            slide.get("key_message")
            or slide.get("statement")
            or (_item_title(items[0]) if items else "")
            or TITLE_FALLBACKS.get(layout)
            or f"第 {index} 页"
        ).strip()
    title = _reconcile_large_count_claim(title, layout, items)
    slide["title"] = title[:180]
    slide["layout"] = layout if layout in TITLE_FALLBACKS else "content"
    slide["items"] = items
    slide.pop("content", None)
    return slide


def repair_sparse_slides(slides: list[dict]) -> list[dict]:
    """把"内容撑不起所选版式"的页面降级成更合适的版式，返回修正后的页列表。

    规则：content/agenda/cards/metrics 条目 <3 → statement；timeline <4 → cards/statement；
    comparison 任一侧 <2 → 合并两侧为 cards/statement；chart 缺 categories/series、
    table 缺行或行数 <3 → 退回 content/statement。降级只改版式与 statement 文本，
    不删除任何已有内容，也不编造数据。
    """

    repaired = []
    for slide in slides:
        item_count = len(slide.get("items") or [])
        layout = slide.get("layout")
        if layout in {"content", "agenda", "cards", "metrics"} and item_count < 3:
            slide["layout"] = "statement"
            slide["statement"] = str(
                slide.get("statement") or slide.get("key_message") or slide.get("title")
            )
        elif layout == "timeline" and item_count < 4:
            slide["layout"] = "cards" if item_count >= 3 else "statement"
            if item_count < 3:
                slide["statement"] = str(slide.get("key_message") or slide.get("title"))
        elif layout == "comparison":
            left = slide.get("left") or []
            right = slide.get("right") or []
            if isinstance(left, dict):
                left = left.get("items") or left.get("content") or []
            if isinstance(right, dict):
                right = right.get("items") or right.get("content") or []
            if len(left) < 2 or len(right) < 2:
                combined = list(left) + list(right) + list(slide.get("items") or [])
                slide["items"] = combined
                slide["layout"] = "cards" if len(combined) >= 3 else "statement"
                if len(combined) < 3:
                    slide["statement"] = str(slide.get("key_message") or slide.get("title"))
        elif layout == "chart":
            chart = next(
                (item for item in slide.get("items") or [] if isinstance(item, dict) and item.get("type") == "chart"),
                None,
            )
            if not chart or not chart.get("categories") or not chart.get("series"):
                slide["layout"] = "statement"
                slide["statement"] = str(slide.get("key_message") or slide.get("title"))
        elif layout == "table":
            table = next(
                (item for item in slide.get("items") or [] if isinstance(item, dict) and item.get("type") == "table"),
                None,
            )
            if not table or len(table.get("rows") or []) < 3:
                slide["layout"] = "content" if item_count >= 3 else "statement"
                if item_count < 3:
                    slide["statement"] = str(slide.get("key_message") or slide.get("title"))
        repaired.append(slide)
    return repaired


def normalize_presentation_spec(
    raw,
    *,
    title_fallback: str = "",
    template_id: str | None = None,
    aspect_ratio: str | None = None,
    page_count=None,
) -> dict:
    """归一化整份演示文稿 spec，是草稿收尾与渲染入口之间的标准形状（幂等）。

    步骤：解析 → 逐页 ``normalize_slide``（丢弃封面等不合规页）→ ``repair_sparse_slides``
    版式降级 → 标题兜底（spec.title → name → ``title_fallback`` → 首页标题 → "演示文稿"）→
    写回 ``template_id``/``aspect_ratio``。封面与结尾默认启用，显式布尔开关必须保留；
    ``page_count`` 表示含已启用封尾的总页数，``"auto"`` 与空值表示由内容量决定页数。
    返回归一后的 spec（会覆盖调用方传入字段，不保留 ``pages`` 别名）。
    """
    spec = parse_presentation_spec(raw)
    raw_slides = spec.get("slides") or spec.get("pages") or []
    slides = []
    for index, raw_slide in enumerate(raw_slides, start=1):
        slide = normalize_slide(raw_slide, index)
        if slide:
            slides.append(slide)
    slides = repair_sparse_slides(slides)
    title = str(
        spec.get("title")
        or spec.get("name")
        or title_fallback
        or (slides[0]["title"] if slides else "演示文稿")
    ).strip()
    spec["title"] = title[:200]
    spec["slides"] = slides
    for option in ("include_cover", "include_closing"):
        if option in spec and type(spec[option]) is not bool:
            raise ValueError(f"{option} 必须是布尔值")
        spec.setdefault(option, True)
    spec.pop("pages", None)
    if template_id:
        spec["template_id"] = template_id
    if aspect_ratio:
        spec["aspect_ratio"] = aspect_ratio
    if page_count not in (None, "", "auto"):
        spec["page_count"] = int(page_count)
    return spec
