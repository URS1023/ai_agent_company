"""Data-only binding boundary for source-imported report templates."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


# 以下四组键是"仅数据"边界的具体定义：模板导出/锁定/指纹比对时一律剥离它们，
# 因此生成或保存后的真实查询结果、数据源绑定、参数与凭据都不会被当成视觉改动。
# 组件级运行期数据：结果集、绑定、刷新状态与本次取值来源。
WIDGET_RUNTIME_KEYS = {
    "data", "value", "rawData", "datasourceId", "datasourceIds", "query",
    "generatedSql", "sourceType", "queryId", "verifiedAt", "dataStatus", "dataLoading",
    "dataError", "lineage", "metricName",
}
# config 内的运行期绑定：SQL、数据源、HTTP 采集配置、列标签与图表数据。新增绑定字段
# 必须同时登记到这里，否则会被指纹当成视觉属性差异而误判为"改了模板"。
CONFIG_RUNTIME_KEYS = {
    "datasourceId", "datasourceIds", "sql", "query", "generatedSql", "parameters",
    "sourceType", "queryId", "verifiedAt", "dataStatus", "dataLoading", "dataError", "dataType",
    "chartData", "chartDataParsed", "rawData", "datasetId", "fromDatasetId",
    "headers", "body", "apiUrl", "sourceBinding", "dataEntity", "dsName", "dsEnabled",
    "setDimensional", "setChartFilter", "lineage",
    "columnLabels", "snapshotBinding", "snapshotOnly", "truncated",
    "referenceSample",
    "dataMapping", "resultColumns",
}
# 钻取配置里的绑定：只剥离取数相关键，保留跳转目标、字段映射等交互定义。
DRILLDOWN_RUNTIME_KEYS = {
    "datasourceId", "sourceType", "sql", "query", "generatedSql", "parameters", "queryId",
    "verifiedAt", "dataStatus", "dataLoading", "dataError", "headers", "body", "apiUrl",
}
# 凭据键名按小写做包含式匹配（见各处的 key.lower()），命名变体也能被清掉。
SECRET_KEYS = {
    "password", "passwd", "secret", "token", "authorization", "cookie", "apikey",
    "api_key", "accesskey", "access_key", "privatekey", "private_key",
}
# 只有媒体类组件允许保留 config.url；其余组件上的 url 视为数据来源/接口地址，必须剥离。
MEDIA_COMPONENTS = {"JDragImg", "JImg", "JImage", "JVideo", "JIframe", "JIFrame"}
# 画布上的契约与来源指纹键：参与记录但不参与模板视觉指纹，保证"锁定前后""绑定前后"
# 的哈希稳定，同时保留模板来源报表与版本供追溯。
CANVAS_CONTRACT_KEYS = {
    "styleLocked", "bindingContract", "templateSourceReportId",
    "templateSourceReportVersion", "templateVisualHash",
}
BINDING_CONTRACT = "data-only-v1"


def _clear_axis_categories(axis: Any) -> Any:
    """递归清空坐标轴的类目数据，保留轴类型、名称、样式等所有视觉配置。"""

    if isinstance(axis, list):
        return [_clear_axis_categories(item) for item in axis]
    if not isinstance(axis, dict):
        return axis
    result = deepcopy(axis)
    if "data" in result:
        result["data"] = []
    return result


def _clear_dataset_source(dataset: Any) -> Any:
    """递归清空 ECharts dataset 的 source（真实数据行），保留字段定义。"""

    if isinstance(dataset, list):
        return [_clear_dataset_source(item) for item in dataset]
    if not isinstance(dataset, dict):
        return dataset
    result = deepcopy(dataset)
    if "source" in result:
        result["source"] = []
    return result


def _clear_series_data(series: Any) -> Any:
    """递归清空 series / 关系图的取值数组，保留系列名称、类型与样式。"""

    if isinstance(series, list):
        return [_clear_series_data(item) for item in series]
    if not isinstance(series, dict):
        return series
    result = deepcopy(series)
    for key in ("data", "nodes", "links", "edges"):
        if key in result:
            result[key] = []
    return result


def clear_chart_option_data(option: Any) -> Any:
    """Clear ECharts value slots while preserving the full visual schema."""

    if not isinstance(option, dict):
        return deepcopy(option)
    result = deepcopy(option)
    if "dataset" in result:
        result["dataset"] = _clear_dataset_source(result["dataset"])
    for key in ("xAxis", "yAxis", "singleAxis", "parallelAxis"):
        if key in result:
            result[key] = _clear_axis_categories(result[key])
    if isinstance(result.get("legend"), dict) and "data" in result["legend"]:
        result["legend"] = {**result["legend"], "data": []}
    elif isinstance(result.get("legend"), list):
        result["legend"] = [
            ({**item, "data": []} if isinstance(item, dict) and "data" in item else deepcopy(item))
            for item in result["legend"]
        ]
    if "series" in result:
        result["series"] = _clear_series_data(result["series"])
    return result


def _clear_drilldown_bindings(drilldown: Any) -> Any:
    """剥离钻取配置中的取数键与凭据键，保留交互结构本身。"""

    if not isinstance(drilldown, dict):
        return deepcopy(drilldown)
    return {
        key: _clear_drilldown_bindings(value)
        for key, value in drilldown.items()
        if key not in DRILLDOWN_RUNTIME_KEYS and key.lower() not in SECRET_KEYS
    }


def _scrub_secret_values(value: Any) -> Any:
    """按 SECRET_KEYS 递归删除任意层级上的凭据字段（键名忽略大小写）。"""

    if isinstance(value, list):
        return [_scrub_secret_values(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _scrub_secret_values(item)
        for key, item in value.items()
        if key.lower() not in SECRET_KEYS
    }


def clear_template_runtime(content: dict) -> dict:
    """Remove bindings and values without changing any visual property.

    与 ``visual_projection`` 的差别：此函数面向"导出/锁定"，除剥离取数键外还会清空图表
    取值、把 config 归位到未绑定状态（dataType=1、chartData="[]"、dataStatus="unbound"），
    并删除非媒体组件上的 url；它不负责计算哈希。返回深拷贝，调用方可安全继续修改。
    """

    result = _scrub_secret_values(deepcopy(content))
    canvas = result.get("canvas") if isinstance(result.get("canvas"), dict) else {}
    for key in ("datasourceId", "datasourceIds", "lineage", "dataStatus"):
        canvas.pop(key, None)
    for widget in result.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        for key in WIDGET_RUNTIME_KEYS:
            widget.pop(key, None)
        if "drilldown" in widget:
            widget["drilldown"] = _clear_drilldown_bindings(widget["drilldown"])
        if isinstance(widget.get("echartsOption"), dict):
            widget["echartsOption"] = clear_chart_option_data(widget["echartsOption"])
        config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        for key in CONFIG_RUNTIME_KEYS:
            config.pop(key, None)
        for key in list(config):
            if key.lower() in SECRET_KEYS:
                config.pop(key, None)
        if widget.get("component") not in MEDIA_COMPONENTS:
            config.pop("url", None)
        if "drilldown" in config:
            config["drilldown"] = _clear_drilldown_bindings(config["drilldown"])
        if isinstance(config.get("option"), dict):
            config["option"] = clear_chart_option_data(config["option"])
        config.update({
            "dataType": 1,
            "chartData": "[]",
            "chartDataParsed": [],
            "dataStatus": "unbound",
        })
        widget["config"] = config
    return result


def lock_template_content(
    content: dict,
    *,
    source_report_id: str = "",
    source_report_version: int | None = None,
) -> dict:
    """Build a data-empty template whose visual contract cannot be changed by generation."""

    result = clear_template_runtime(content)
    canvas = result.setdefault("canvas", {})
    canvas["styleLocked"] = True
    canvas["bindingContract"] = BINDING_CONTRACT
    if source_report_id:
        canvas["templateSourceReportId"] = source_report_id
    if source_report_version is not None:
        canvas["templateSourceReportVersion"] = int(source_report_version)
    # The hash ignores contract/provenance keys and all runtime data slots, so
    # it is stable before and after a template receives real query results.
    canvas["templateVisualHash"] = template_visual_fingerprint(result)
    return result


def visual_projection(content: dict) -> dict:
    """Return only immutable visual fields from a report payload."""

    result = deepcopy(content)
    canvas = result.get("canvas") if isinstance(result.get("canvas"), dict) else {}
    for key in ("datasourceId", "datasourceIds", "lineage", "dataStatus"):
        canvas.pop(key, None)
    for widget in result.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        for key in WIDGET_RUNTIME_KEYS:
            widget.pop(key, None)
        if "drilldown" in widget:
            widget["drilldown"] = _clear_drilldown_bindings(widget["drilldown"])
        config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        for key in CONFIG_RUNTIME_KEYS:
            config.pop(key, None)
        if "drilldown" in config:
            config["drilldown"] = _clear_drilldown_bindings(config["drilldown"])
        widget["config"] = config
    return result


def _canonical_visual_value(value: Any) -> Any:
    """递归规范化视觉值：整数值的 float 归一为 int（1.0 与 1 视为同一取值）。

    这是指纹稳定的前提——不同写入路径（模型 JSON 与前端 JS）可能把同一数值写成 1 或 1.0，
    不归一就会把"完全相同的视觉"判成改动过模板。
    """

    if isinstance(value, dict):
        return {key: _canonical_visual_value(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [_canonical_visual_value(entry) for entry in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def visual_fingerprint(content: dict) -> str:
    """计算"视觉内容"指纹（SHA-256）供来源校验与改动比对使用。

    只剥离运行期绑定键（``visual_projection``），**不清空图表数据槽**，因此它反映的是
    "除绑定外的一切，含当前图表取值"；模板侧的稳定哈希请用 ``template_visual_fingerprint``。
    ``_canonical_visual_value`` 把整数值的 float 归一为 int，避免 1 与 1.0 被判为差异。
    """

    payload = json.dumps(
        _canonical_visual_value(visual_projection(content)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def template_visual_projection(content: dict) -> dict:
    """Normalize dynamic chart values while retaining every authored visual option."""

    result = visual_projection(content)
    canvas = result.get("canvas") if isinstance(result.get("canvas"), dict) else {}
    for key in CANVAS_CONTRACT_KEYS:
        canvas.pop(key, None)
    for widget in result.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        if isinstance(widget.get("echartsOption"), dict):
            widget["echartsOption"] = clear_chart_option_data(widget["echartsOption"])
        config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        if isinstance(config.get("option"), dict):
            config["option"] = clear_chart_option_data(config["option"])
    return result


def template_visual_fingerprint(content: dict) -> str:
    """Hash template visuals independent of SQL and current query-result values."""

    payload = json.dumps(
        _canonical_visual_value(template_visual_projection(content)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assert_visual_unchanged(template_content: dict, bound_content: dict) -> None:
    """校验"往模板里填数据"没有改动任何视觉属性；不一致时抛 ``ValueError``。

    成功时返回 None；失败时先做一次逐节点比对（最多收集 12 条差异路径）再抛出，
    异常文本含期望/实际哈希和差异路径，便于定位是哪个组件被改写。调用方（service 与
    generation）把该异常转成 400，因此这里只负责判定，不做权限与事务处理。
    """

    expected = visual_fingerprint(template_content)
    actual = visual_fingerprint(bound_content)
    if expected != actual:
        paths: list[str] = []

        def compare(left: Any, right: Any, path: str = "$") -> None:
            """收集差异路径（最多 12 条），失败时用于定位是哪个组件被改写。"""
            if len(paths) >= 12:
                return
            if type(left) is not type(right):
                paths.append(path)
                return
            if isinstance(left, dict):
                for key in sorted(set(left) | set(right)):
                    if key not in left or key not in right:
                        paths.append(f"{path}.{key}")
                    else:
                        compare(left[key], right[key], f"{path}.{key}")
                return
            if isinstance(left, list):
                if len(left) != len(right):
                    paths.append(f"{path}.length")
                for index, (left_item, right_item) in enumerate(zip(left, right)):
                    compare(left_item, right_item, f"{path}[{index}]")
                return
            if _canonical_visual_value(left) != _canonical_visual_value(right):
                paths.append(path)

        compare(visual_projection(template_content), visual_projection(bound_content))
        changed = ", ".join(paths) or "unknown"
        raise ValueError(
            f"template visual fingerprint changed: expected {expected}, got {actual}; paths: {changed}"
        )
