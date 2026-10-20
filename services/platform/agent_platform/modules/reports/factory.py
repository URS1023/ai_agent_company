"""按源目录ID创建真实组件，保持同类型不同素材/变体的原始配置。"""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from uuid import uuid4

from agent_platform.core.errors import DomainError
from .capabilities import SOURCE_TYPES, source_supported


@lru_cache(maxsize=1)
def presets():
    source = json.loads((Path(__file__).parent / "resources" / "component_presets.json").read_text(encoding="utf-8"))
    return {item["source_id"]: item for item in source["items"]}


def component_instance(config, supported):
    """生成的新对象不能带原SQL/headers/凭据或示例业务值，预览种子单独保留。"""
    item = presets().get(config.source_id)
    if item is None:
        raise DomainError(404, "component_source_not_found", "源组件不存在")
    if not source_supported(config.source_id, item["source_type"], supported):
        raise DomainError(422, "component_renderer_not_ready", "该组件暂不可用")
    widget = deepcopy(item["canvas"])
    identifier = config.id or "widget-" + uuid4().hex
    widget.update(id=identifier, i=identifier, x=config.x, y=config.y, orderNum=config.order_num, source_component_id=config.source_id)
    widget["config"]["sourceKind"] = "Test5-canonical"
    # 纯图标等源条目也可能误带通用图表示例，新实例统一清空业务数据。
    widget["config"]["chartData"] = "[]"
    widget["config"]["chartDataParsed"] = []
    if item["source_type"] == "JCustomIcon":
        widget["config"].setdefault("option", {}).setdefault("icon", "fa-solid fa-star")
        widget["config"]["option"].setdefault("iconColor", "#05f8d6")
    if item["source_type"] == "JCarousel":
        widget["config"]["images"] = []
    if item["source_type"] in SOURCE_TYPES:
        # 目录中的固定演示月份不应覆盖实际数据月份，首次绑定由真实事件推导。
        option = widget["config"].setdefault("option", {})
        if item["source_type"] in {"JCalendar", "JMultiViewCalendar", "JPermanentCalendar"}:
            option.setdefault("calendar", {}).pop("month", None)
            option["calendar"]["timezone"] = "Asia/Shanghai"
    if item["source_type"] in ("JDragBorder", "JDragDecoration", "JImg", "JCustomIcon"):
        widget["type"] = "decoration"
    # 即使后续源工厂种子发生变化，也不允许连接和凭据字段进入新报表。
    for key in ("sql", "datasourceId", "apiUrl", "headers", "body", "drilldown", "queryId", "password", "token", "apiKey"):
        widget["config"].pop(key, None)
    return widget
