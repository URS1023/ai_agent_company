"""Build Test5's report component catalogue from the captured Lynx catalogue.

The Lynx source contains three overlapping menu arrays.  The historical Test5
import only copied the primary ``X`` array, which omitted thirteen renderable
component types that exist in the ``r``/``d`` menus.  This importer keeps the
primary menu intact, adds the missing business components as a ninth toolbar
group, and records separate counts for category identifiers and renderable
leaf components.

The generated JSON files are committed runtime resources.  Lynx is only read
by this migration script; Test5 never depends on the reference project at
runtime.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LYNX_CATALOG = ROOT.parent / "lynx-ai-master" / "jimu_components_catalog.json"
FRONTEND_CATALOG = ROOT / "apps" / "analytics" / "src" / "data" / "jimuComponentLibrary.json"
BACKEND_CATALOG = ROOT / "backend" / "app" / "modules" / "reports" / "resources" / "component_catalog.json"

BUSINESS_GROUP_KEY = "business"
BUSINESS_GROUP_LABEL = "业务组件"
BUSINESS_GROUP_ICON = "fa-solid fa-layer-group"
SOURCE_ARRAY_PRIORITY = {"r": 0, "d": 1, "X": 2}
DESIGNER_SIZES = {
    "JCalendar": (640, 420),
    "JMultiViewCalendar": (680, 420),
    "JFilterQuery": (720, 94),
    "JCustomButton": (180, 48),
    "JTabs": (720, 360),
    "JGrid": (720, 360),
    "JCommon": (560, 320),
    "JQuickNav": (640, 160),
    "JGrowCard": (620, 240),
    "JProjectCard": (620, 320),
    "JSimpleCard": (620, 170),
    "JWaitMatter": (620, 240),
    "JDynamicInfo": (620, 310),
}


def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件（用 utf-8-sig 兼容带 BOM 的来源文件）。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compact_item(node: dict[str, Any]) -> dict[str, Any]:
    """把源目录节点压缩成前端设计器需要的字段集，并补齐画布尺寸。

    尺寸优先取 DESIGNER_SIZES 中人工核对过的值，否则按源宽高兜底（最小 80×36），
    保证设计器拖出组件时不会出现 0 尺寸。
    """
    component_type = str(node["compType"])
    width, height = DESIGNER_SIZES.get(
        component_type,
        (max(80, int(node.get("w") or 450)), max(36, int(node.get("h") or 300))),
    )
    return {
        "id": node.get("sourceRecordKey") or str(node.get("id") or ""),
        "sourceId": str(node.get("id") or ""),
        "sourceArray": node.get("sourceArray") or "",
        "sourceRecordKey": node.get("sourceRecordKey") or "",
        "name": node.get("name") or component_type,
        "compType": component_type,
        "icon": node.get("icon") or "",
        "width": width,
        "height": height,
        "path": node.get("path") or "",
        "dataType": node.get("dataType") or 1,
        "chartDataCount": node.get("chartDataCount") or 0,
        "dataMapping": node.get("dataMapping") or [],
        "imageUrl": node.get("imageUrl") or "",
    }


def build_business_group(nodes: list[dict[str, Any]]) -> tuple[dict[str, Any], set[str]]:
    """构造「业务组件」工具栏分组：只收录主菜单 X 数组缺失的可渲染组件类型。

    同名同类型只保留一条（按 r/d/X 优先级与源顺序稳定排序），再按 path 的首段归类；
    返回分组结构与缺失类型集合，供 meta 里单独给出补充条数证据。
    """
    leaves = [node for node in nodes if not node.get("isCategory") and node.get("compType")]
    primary_types = {
        str(node["compType"])
        for node in leaves
        if node.get("sourceArray") == "X"
    }
    missing_types = {str(node["compType"]) for node in leaves} - primary_types
    candidates = [node for node in leaves if str(node["compType"]) in missing_types]
    candidates.sort(key=lambda node: (
        SOURCE_ARRAY_PRIORITY.get(str(node.get("sourceArray")), 99),
        int(node.get("sourceArrayIndex") or 0),
        str(node.get("path") or ""),
        str(node.get("name") or ""),
    ))

    unique_records: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for node in candidates:
        key = (str(node.get("name") or ""), str(node.get("compType") or ""))
        unique_records.setdefault(key, node)

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict(
        (name, []) for name in ("首页", "常用", "布局", "数据", "个性")
    )
    for node in unique_records.values():
        category = str(node.get("path") or "业务").split("/")[0].strip() or "业务"
        grouped.setdefault(category, []).append(compact_item(node))

    categories = [
        {"name": name, "count": len(items), "items": items}
        for name, items in grouped.items()
        if items
    ]
    return ({
        "key": BUSINESS_GROUP_KEY,
        "label": BUSINESS_GROUP_LABEL,
        "icon": BUSINESS_GROUP_ICON,
        "categories": categories,
    }, missing_types)


def source_meta(
    source: dict[str, Any],
    primary_catalog: dict[str, Any],
    business_group: dict[str, Any],
    missing_types: set[str],
) -> dict[str, Any]:
    """生成目录溯源信息（计数、类型名清单、源文件 sha256）。

    刻意把「分类标识符」与「可渲染叶子组件」分开计数：旧版 meta 把类别也算成组件，
    导致数量口径不一致；这里保留旧字段名以兼容读取方，但语义已按实际含义修正。
    """
    nodes = source["nodes"]
    leaves = [node for node in nodes if not node.get("isCategory") and node.get("compType")]
    categories = [node for node in nodes if node.get("isCategory") and node.get("compType")]
    leaf_types = {str(node["compType"]) for node in leaves}
    category_types = {str(node["compType"]) for node in categories}
    primary_leaves = [
        node for node in leaves
        if node.get("sourceArray") == "X"
    ]
    primary_types = {str(node["compType"]) for node in primary_leaves}
    supplemental_records = sum(
        len(category["items"]) for category in business_group["categories"]
    )
    toolbar_items = sum(
        len(category.get("items") or [])
        for group in primary_catalog.get("toolbarGroups") or []
        for category in group.get("categories") or []
    ) + supplemental_records
    return {
        # Backward-compatible names now describe what they actually count.
        "distinctComponentTypes": len(leaf_types),
        "primaryLeafCount": len(primary_leaves),
        "toolbarGroupCount": len(primary_catalog.get("toolbarGroups") or []) + 1,
        "toolbarItemCount": toolbar_items,
        # Explicit evidence avoids conflating category identifiers with widgets.
        "sourceNodeCount": len(nodes),
        "sourceDistinctTypeIdentifiers": len({
            str(node["compType"]) for node in nodes if node.get("compType")
        }),
        "sourceCategoryTypeIdentifiers": len(category_types),
        "sourceCategoryOnlyTypeIdentifiers": len(category_types - leaf_types),
        "sourceCategoryOnlyTypeNames": sorted(category_types - leaf_types),
        "sourceRenderableLeafRecords": len(leaves),
        "sourceRenderableLeafTypes": len(leaf_types),
        "sourceRenderableLeafTypeNames": sorted(leaf_types),
        "primaryRenderableLeafTypes": len(primary_types),
        "primaryRenderableLeafTypeNames": sorted(primary_types),
        "supplementalLeafRecords": supplemental_records,
        "supplementalRenderableLeafTypes": len(missing_types),
        "supplementalRenderableLeafTypeNames": sorted(missing_types),
        "sourceSha256": hashlib.sha256(LYNX_CATALOG.read_bytes()).hexdigest(),
        "sourcePath": "../lynx-ai-master/jimu_components_catalog.json",
    }


def build_catalog(base: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """在已有目录基础上重算 meta 并追加「业务组件」分组；重复执行不会叠加同名分组。"""
    business_group, missing_types = build_business_group(source["nodes"])
    toolbar_groups = [
        group for group in base.get("toolbarGroups") or []
        if group.get("key") != BUSINESS_GROUP_KEY
    ]
    primary_catalog = {**base, "toolbarGroups": toolbar_groups}
    return {
        **base,
        "meta": source_meta(source, primary_catalog, business_group, missing_types),
        "toolbarGroups": [*toolbar_groups, business_group],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    """统一以 UTF-8（不转义中文）+ 两空格缩进 + 结尾换行写回，保证 git diff 稳定。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """按参考项目的组件目录重建前后端两份 catalog 资源（就地覆盖写）。

    前置条件：同级目录存在参考项目 lynx-ai-master/jimu_components_catalog.json；
    副作用：覆盖 apps/analytics/src/data/jimuComponentLibrary.json 与
    backend/app/modules/reports/resources/component_catalog.json（这两份是提交进仓库的运行时资源）。
    诊断信息只打印路径与计数（含 sha256），便于核对来源版本。
    """
    frontend = build_catalog(read_json(FRONTEND_CATALOG), source)
    backend = build_catalog(read_json(BACKEND_CATALOG), source)
    write_json(FRONTEND_CATALOG, frontend)
    write_json(BACKEND_CATALOG, backend)
    print(json.dumps({
        "frontend": str(FRONTEND_CATALOG),
        "backend": str(BACKEND_CATALOG),
        "meta": frontend["meta"],
        "businessCategories": [
            {"name": category["name"], "count": category["count"]}
            for category in frontend["toolbarGroups"][-1]["categories"]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
