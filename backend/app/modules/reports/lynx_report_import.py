"""Extract concrete Lynx ``report_screen`` records for direct report import.

The source API is used only by the offline extraction script. Application
requests read the checked-in, credential-free JSON catalog and therefore never
depend on the legacy Lynx service or its authorization token. Unlike the old
template importer, this extractor deliberately preserves SQL, result data,
widget configuration, layout and styling.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from app.branding import PRODUCT_NAME
from app.modules.reports.template_lock import SECRET_KEYS, visual_fingerprint


CATALOG_SCHEMA = "test5.lynx-source-reports/v2"
SOURCE_KIND = "lynx-report-screen"
STATIC_COMPONENTS = {
    "JText", "JRichText", "JCurrentTime", "JDragBorder", "JDragDecoration",
    "JDragImg", "JImg", "JImage", "JAmbientGlow", "JDataConstellation",
    "JFlowRibbon", "JSignalRail", "JFocusOrb", "JReplicaWeatherStrip",
    "JReplicaMapOperations", "JSystemStatus",
}


def _canonical_json(value: Any) -> bytes:
    """规范化 JSON 字节（键排序、紧凑分隔、禁止 NaN），供摘要计算使用。

    规范化是摘要可比的前提：任何键序或空白差异都不会改变摘要，``allow_nan=False``
    则保证非有限数不会被静默写成非法 JSON。
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    """对规范化 JSON 计算 SHA-256，用于目录内容与单条记录的完整性校验。"""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _rows_from_payload(payload: Any) -> list[dict]:
    """从来源接口响应中取出记录数组；结构不符或标记失败时抛 ``ValueError``。

    兼容 ``data``/``result``/``rows`` 三种外层包装，并要求每条记录都是对象——
    宁可让离线提取脚本失败，也不产出结构不确定的目录。
    """

    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise ValueError(str(payload.get("message") or "Lynx report API returned failure"))
        payload = payload.get("data", payload.get("result", payload.get("rows", payload)))
    if not isinstance(payload, list):
        raise ValueError("Lynx report API response must contain a data array")
    if any(not isinstance(row, dict) for row in payload):
        raise ValueError("Every Lynx report record must be an object")
    return payload


def _content(row: dict) -> dict:
    """校验并解析一条来源记录的画布内容（兼容字符串 JSON）。

    要求 canvas 与 widgets 类型正确、widgets 非空、每个组件都有唯一标识，任一项不满足即抛
    ``ValueError``（错误信息带来源记录 ID 与组件序号，便于定位是哪条目录数据坏了）。
    """

    value = row.get("content")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"report {row.get('id')} content is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"report {row.get('id')} has no content object")
    content = deepcopy(value)
    canvas = content.get("canvas")
    widgets = content.get("widgets")
    if not isinstance(canvas, dict) or not isinstance(widgets, list):
        raise ValueError(f"report {row.get('id')} content must contain canvas and widgets")
    if not widgets:
        raise ValueError(f"report {row.get('id')} has no widgets")
    identifiers = []
    for index, widget in enumerate(widgets):
        if not isinstance(widget, dict):
            raise ValueError(f"report {row.get('id')} widget {index + 1} is not an object")
        identity = str(widget.get("id") or widget.get("i") or "")
        if not identity:
            raise ValueError(f"report {row.get('id')} widget {index + 1} has no id")
        identifiers.append(identity)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"report {row.get('id')} contains duplicate widget ids")
    return content


def _category(name: str) -> str:
    """按报表名称里的行业关键词归类（设备安全/生产质量/经营分析等），仅用于目录展示。"""

    if any(term in name for term in ("设备", "安全", "生产", "质检", "质量", "车间", "农机")):
        return f"{PRODUCT_NAME} 实际报表 · 生产设备"
    if any(term in name for term in ("财务", "经营")):
        return f"{PRODUCT_NAME} 实际报表 · 财务经营"
    if any(term in name for term in ("销售", "订单", "采购", "电商", "商品")):
        return f"{PRODUCT_NAME} 实际报表 · 供应销售"
    if any(term in name for term in ("仓储", "库存", "物流")):
        return f"{PRODUCT_NAME} 实际报表 · 仓储物流"
    return f"{PRODUCT_NAME} 实际报表 · 综合运营"


def _tags(name: str) -> list[str]:
    """生成目录检索标签（固定标记 + 分类 + 名称分词），供模板/报表搜索命中。"""

    tags = [f"{PRODUCT_NAME} 实际报表", "直接导入", "可编辑"]
    for label, terms in (
        ("财务", ("财务", "经营")),
        ("销售", ("销售", "订单", "电商")),
        ("设备", ("设备", "运维", "安全")),
        ("生产", ("生产", "车间", "农机")),
        ("质量", ("质量", "质检")),
        ("采购", ("采购",)),
        ("仓储", ("仓储", "库存")),
    ):
        if any(term in name for term in terms):
            tags.append(label)
    return tags


def _safe_preview(value: Any) -> str:
    """只接受站内相对路径的预览图地址；其它来源一律丢弃（返回空串），避免目录里出现外部外链。"""

    preview = str(value or "").strip()
    if not preview or "\\" in preview:
        return ""
    parsed = urlsplit(preview)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.username or parsed.password or preview.startswith("//"):
        return ""
    return preview


def _secret_paths(value: Any, path: str = "") -> list[str]:
    """递归列出内容中所有凭据字段的路径（如 ``canvas.widgets[0].config.password``）。

    提取脚本据此**拒绝**导出含凭据的目录：只报告路径而不打印值，日志里不会出现密钥内容。
    """

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in SECRET_KEYS:
                found.append(item_path)
            found.extend(_secret_paths(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{path}[{index}]"))
    return found


def _source_ids(value: Any) -> set[str]:
    """递归收集内容中引用的数据源 ID，用于核对目录声明的来源范围与实际绑定是否一致。"""

    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"datasourceid", "data_source_id"} and item not in (None, "", 0):
                found.add(str(item))
            elif lowered in {"datasourceids", "data_source_ids"} and isinstance(item, list):
                found.update(str(entry) for entry in item if entry not in (None, "", 0))
            found.update(_source_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_source_ids(item))
    return found


def _widget_has_sql(widget: dict) -> bool:
    """判断组件是否带 SQL（含 query/config/drilldown 三处），用于统计"是否需要真实数据源"。"""

    candidates = [widget.get("query"), widget.get("config"), widget.get("drilldown")]
    config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
    candidates.append(config.get("drilldown"))
    return any(
        isinstance(candidate, dict)
        and any("sql" in str(key).lower() and value not in (None, "", [], {}) for key, value in candidate.items())
        for candidate in candidates
    )


def _widget_has_data(widget: dict) -> bool:
    """判断组件是否内嵌了查询结果数据（``chartData``/``data`` 等），供目录统计与清理核对。"""

    config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
    return any(
        value not in (None, "", [], {})
        for value in (
            widget.get("data"), widget.get("rawData"),
            config.get("chartData"), config.get("chartDataParsed"), config.get("rawData"),
        )
    )


def _report(row: dict, source_index: int) -> tuple[dict, dict, Counter]:
    """把一条来源记录整理成 ``(报表目录项, 统计信息, 组件计数)``，并拒绝含凭据的内容。

    与模板导入的关键差异：**直接报表导入刻意保留 SQL、查询结果、组件配置、布局与样式**，
    因此这里会显式检查 ``_secret_paths``——保留业务数据可以，保留凭据不行，命中即抛错终止导出。
    返回值同时供导出汇总统计（组件类型计数等）使用。
    """
    source_id = str(row.get("id") or "").strip()
    if not source_id:
        raise ValueError("Lynx report record has no id")
    name = str(row.get("reportName") or "").strip() or f"{PRODUCT_NAME} 报表 {source_id}"
    original = _content(row)
    secret_paths = _secret_paths(original)
    if secret_paths:
        raise ValueError(
            f"report {source_id} contains credential-like fields: {', '.join(secret_paths[:5])}"
        )
    content = deepcopy(original)
    components = Counter(
        str(widget.get("component") or widget.get("type") or "unknown")
        for widget in content["widgets"]
    )
    data_slots = sum(1 for widget in content["widgets"] if _widget_has_data(widget))
    sql_widgets = sum(1 for widget in content["widgets"] if _widget_has_sql(widget))
    source_datasource_ids = _source_ids(content)
    if row.get("datasourceId") not in (None, "", 0):
        source_datasource_ids.add(str(row["datasourceId"]))
    description = str(row.get("description") or "").strip()
    summary = description or f"从{PRODUCT_NAME}实际报表记录直接迁移，保留完整画布、组件、布局、样式、SQL 和真实结果。"
    enabled = bool(row.get("enabled", True)) and bool(content["widgets"])
    report = {
        "name": name,
        "description": summary,
        "sourceThumbnail": _safe_preview(row.get("thumbnail")),
        "componentCount": len(content["widgets"]),
        "dataSlotCount": data_slots,
        "sqlWidgetCount": sql_widgets,
        "category": _category(name),
        "tags": _tags(name),
        "sourceKind": SOURCE_KIND,
        "sourceSystem": PRODUCT_NAME,
        "sourceTable": "report_screen",
        "sourceRecordId": source_id,
        "sourceOrder": source_index,
        "sourceCreatedAt": row.get("createTime"),
        "sourceUpdatedAt": row.get("updateTime"),
        "sourceContentSha256": _sha256(original),
        "sourceDatasourceIds": sorted(source_datasource_ids),
        "width": int(row.get("width") or content["canvas"].get("width") or 1920),
        "height": int(row.get("height") or content["canvas"].get("height") or 1080),
        "backgroundColor": str(row.get("backgroundColor") or content["canvas"].get("backgroundColor") or "#07111f"),
        "backgroundImage": str(row.get("backgroundImage") or content["canvas"].get("backgroundImage") or ""),
        "refreshSeconds": int(row.get("refreshSeconds") or 0),
        "enabled": enabled,
        "content": content,
    }
    report["visualHash"] = visual_fingerprint(content)
    evidence = {
        "sourceId": source_id,
        "name": name,
        "enabled": enabled,
        "componentCount": len(content["widgets"]),
        "dataSlotCount": data_slots,
        "sqlWidgetCount": sql_widgets,
        "sourceDatasourceIds": sorted(source_datasource_ids),
        "sourceContentSha256": report["sourceContentSha256"],
        "visualHash": report["visualHash"],
        "createTime": row.get("createTime"),
        "updateTime": row.get("updateTime"),
    }
    return report, evidence, components


def extract_catalog(payload: Any, *, source_url: str) -> dict:
    """Return a deterministic, credential-free catalog for all API records."""

    rows = _rows_from_payload(payload)
    source_ids = [str(row.get("id") or "").strip() for row in rows]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Lynx report API returned duplicate report ids")
    reports: list[dict] = []
    records: list[dict] = []
    component_counts: Counter = Counter()
    for index, row in enumerate(rows):
        report, evidence, components = _report(row, index)
        reports.append(report)
        records.append(evidence)
        component_counts.update(components)
    parsed = urlsplit(source_url)
    source_descriptor = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else parsed.path
    catalog = {
        "schemaVersion": CATALOG_SCHEMA,
        "sourceSystem": PRODUCT_NAME,
        "sourceEndpoint": source_descriptor,
        "sourceRecordCount": len(rows),
        "sourceRecordsSha256": _sha256(rows),
        "reportCount": len(reports),
        "enabledReportCount": sum(1 for item in reports if item["enabled"]),
        "duplicateNameCount": len(reports) - len({item["name"] for item in reports}),
        "componentCounts": dict(sorted(component_counts.items())),
        "records": records,
        "reports": reports,
    }
    catalog["catalogSha256"] = _sha256({key: value for key, value in catalog.items() if key != "catalogSha256"})
    return catalog
