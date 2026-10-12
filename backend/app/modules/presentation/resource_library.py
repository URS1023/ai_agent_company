# -*- coding: utf-8 -*-
"""Lazy inventory for retained PPT reference assets and optional adapters."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ...config import settings


def _read_json(path: Path, default):
    """读 JSON 索引；文件缺失或损坏时返回 ``default``（资源库属于可选增强，不能因此报错）。"""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _count_files(path: Path, pattern: str = "*") -> int:
    """递归统计目录下匹配文件数；目录不存在返回 0（资源库可选，缺目录不算错误）。"""

    if not path.exists():
        return 0
    return sum(1 for item in path.rglob(pattern) if item.is_file())


@lru_cache(maxsize=1)
def resource_inventory() -> dict:
    """汇总本机可用的 PPT 参考资源清单（母版/样式/版式/图表/品牌/图标库等）。

    只做目录探测与索引文件读取，**不加载素材内容**，因此首次调用后缓存（``lru_cache``）即可；
    缺失的可选适配器只汇报为不可用/计数为 0，不影响接口可用性。
    """
    master_root = Path(settings.PRESENTATION_PPT_MASTER_ROOT).resolve()
    aippt_root = Path(settings.PRESENTATION_AIPPT_ROOT).resolve()
    templates = master_root / "templates"
    styles_index = _read_json(
        templates / "styles" / "styles_index.json", {}
    )
    layouts_index = _read_json(
        templates / "layouts" / "layouts_index.json", {}
    )
    charts_index = _read_json(
        templates / "charts" / "charts_index.json", {}
    )
    chart_entries = charts_index.get("charts", charts_index)
    brand_root = templates / "brands"
    icon_root = templates / "icons"
    docs_screenshot_root = (
        master_root.parents[1] / "docs" / "assets" / "screenshots"
    )
    external_manifest = _read_json(
        Path(__file__).resolve().parent
        / "resources"
        / "external_services.json",
        {"services": []},
    )

    icon_libraries = {}
    for name in (
        "chunk-filled",
        "tabler-filled",
        "tabler-outline",
        "phosphor-duotone",
        "simple-icons",
    ):
        icon_libraries[name] = _count_files(icon_root / name, "*.svg")

    brands = []
    if brand_root.exists():
        for child in sorted(brand_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            brands.append({
                "id": child.name,
                "asset_count": _count_files(child / "images"),
                "restricted": True,
                "activation": "explicit_user_or_authorized_customer_only",
            })

    audio_files = _count_files(master_root, "*.wav")
    return {
        "schema": "presentation_resource_inventory.v1",
        "retention_mode": "full_source_retained_lazy_runtime",
        "roots": {
            "ppt_master_available": master_root.exists(),
            "aippt_available": aippt_root.exists(),
        },
        "styles": {
            "count": len(styles_index),
            "items": [
                {
                    "id": key,
                    "summary": value.get("summary", ""),
                    "keywords": value.get("keywords", []),
                }
                for key, value in styles_index.items()
            ],
        },
        "layouts": {
            "count": len(layouts_index),
            "items": [
                {
                    "id": key,
                    "summary": value.get("summary", ""),
                    "canvas_format": value.get("canvas_format"),
                    "page_types": value.get("page_types", []),
                }
                for key, value in layouts_index.items()
            ],
        },
        "charts": {
            "count": len(chart_entries),
            "items": [
                {"id": key, "summary": value.get("summary", "")}
                for key, value in chart_entries.items()
            ],
        },
        "brands": {
            "count": len(brands),
            "items": brands,
            "policy": "retain_with_trademark_and_authorization_boundary",
        },
        "icons": {
            "count": sum(icon_libraries.values()),
            "libraries": icon_libraries,
            "loading": "lazy_copy_selected_assets_only",
            "notices_retained": (
                icon_root / "THIRD_PARTY_NOTICES.md"
            ).exists(),
        },
        "audio": {
            "count": audio_files,
            "loading": "optional_explicit_delivery_only",
        },
        "examples": {
            "screenshot_count": _count_files(docs_screenshot_root),
            "usage": "preview_and_layout_reference_not_factual_source",
        },
        "external_services": {
            "enabled": settings.PRESENTATION_EXTERNAL_SERVICES_ENABLED,
            "items": external_manifest.get("services", []),
            "credential_policy": "secret_reference_only",
        },
        "licenses": {
            "ppt_master": "MIT",
            "aippt": "GPL-3.0",
            "icons": "mixed; see retained THIRD_PARTY_NOTICES.md",
        },
    }


def search_icons(
    library: str, query: str, limit: int = 40
) -> list[dict]:
    """在指定图标库内按文件名子串检索 SVG 图标（``limit`` 夹在 1–100），返回图标元数据。

    只允许白名单图标库（未知库返回空结果，不做路径拼接探测）；``license_notice_required`` 与
    ``trademark_review_required`` 标明该库的合规要求（chunk-filled 需署名、simple-icons 需
    商标审核），调用方必须在界面上如实提示，不能只取图标而丢掉这些约束。
    """
    allowed = {
        "chunk-filled",
        "tabler-filled",
        "tabler-outline",
        "phosphor-duotone",
        "simple-icons",
    }
    if library not in allowed:
        return []
    needle = query.strip().lower()
    if not needle:
        return []
    root = (
        Path(settings.PRESENTATION_PPT_MASTER_ROOT).resolve()
        / "templates"
        / "icons"
        / library
    )
    if not root.exists():
        return []
    results = []
    for path in root.glob("*.svg"):
        if needle not in path.stem.lower():
            continue
        results.append({
            "library": library,
            "name": path.stem,
            "license_notice_required": library == "chunk-filled",
            "trademark_review_required": library == "simple-icons",
        })
        if len(results) >= max(1, min(limit, 100)):
            break
    return results
