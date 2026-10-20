"""按原目录来源判定能力；同一compType的业务变体不能随类型整组启用。"""
from functools import lru_cache
import json
from pathlib import Path


SOURCE_COMPONENTS = {
    "index.js:X:4:1009386233476579328:胶囊图:JCapsuleChart": "JCapsuleChart",
    "index.js:X:4:1536952329568276481:背景柱形图:JBackgroundBar": "JBackgroundBar",
    "index.js:X:4:1501138710950854657:多色仪表盘:JColorGauge": "JColorGauge",
    "index.js:X:4:200300100100:渐变仪表盘:JAntvGauge": "JAntvGauge",
    "index.js:X:4:1011136904469872896:发展历程:JDevHistory": "JDevHistory",
    "index.js:X:4:1756792729039:日历:JPermanentCalendar": "JPermanentCalendar",
    "index.js:r:1:local-15013142797119490:日历:JCalendar": "JCalendar",
    "index.js:r:1:local-15013142797119491:多视图日历:JMultiViewCalendar": "JMultiViewCalendar",
    "index.js:X:4:1757057327940:卡片滚动(横向):JCardScroll": "JCardScroll",
    "index.js:X:4:1757057327940:卡片滚动(竖向+序号):JCardScroll": "JCardScroll",
    "index.js:X:4:1757057327940:卡片滚动(高亮):JCardScroll": "JCardScroll",
    "index.js:X:4:1756869734251:卡片轮播:JCardCarousel": "JCardCarousel",
}
SOURCE_NAMES = {
    "JCapsuleChart": "胶囊图", "JBackgroundBar": "背景柱形图", "JColorGauge": "多色仪表盘",
    "JAntvGauge": "渐变仪表盘", "JDevHistory": "发展历程", "JPermanentCalendar": "日历",
    "JCalendar": "日历", "JMultiViewCalendar": "多视图日历", "JCardScroll": "卡片滚动", "JCardCarousel": "卡片轮播",
}
SOURCE_TYPES = frozenset(SOURCE_NAMES)


@lru_cache(maxsize=1)
def catalog_sources():
    """以完整source ID保留目录身份，不使用会重复的sourceId或可编辑标题。"""
    original = json.loads((Path(__file__).parent / "resources/component_catalog.json").read_text(encoding="utf-8"))
    return {item["id"]: item["compType"] for group in original["toolbarGroups"]
            for category in group.get("categories", []) for item in category.get("items", [])}


def source_supported(source_id, component, supported):
    """旧组件仍兼容无来源的既有布局；本批组件必须携带已核验的精确来源。"""
    if component not in supported:
        return False
    if source_id is not None:
        if not isinstance(source_id, str) or catalog_sources().get(source_id) != component:
            return False
    if component in SOURCE_TYPES:
        return SOURCE_COMPONENTS.get(source_id) == component
    return True


def source_variant(widget):
    """三种卡片的方向、序号和强调来自源条目，改名不会改变业务表现。"""
    source = widget.get("source_component_id", "")
    return {
        "direction": "vertical" if source == "index.js:X:4:1757057327940:卡片滚动(竖向+序号):JCardScroll" else "horizontal",
        "show_index": source == "index.js:X:4:1757057327940:卡片滚动(竖向+序号):JCardScroll",
        "highlight": source == "index.js:X:4:1757057327940:卡片滚动(高亮):JCardScroll",
    }
