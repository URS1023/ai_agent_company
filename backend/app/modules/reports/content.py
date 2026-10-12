"""Preserve the complete canvas schema while enforcing safe resource boundaries."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.branding import PRODUCT_NAME, public_brand_text
from app.errors import ValidationError

RESOURCE_ROOT = Path(__file__).with_name("resources")
LYNX_TEMPLATE_ID_PREFIX = "lynx-source-template-"
LYNX_REPORT_PREVIEW_ROOT = "/assets/analytics/jimu-screen/report-thumbnails"


@lru_cache(maxsize=1)
def component_catalog() -> dict:
    """读取随包组件目录（含全部智慧云脑 Cloud Brain 组件类型、分类与工具栏）。

    目录描述"平台支持哪些组件"，数量本身不是渲染验收结果；结果进程内缓存，资源文件改动
    需重启才生效。
    """

    return json.loads((RESOURCE_ROOT / "component_catalog.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def component_types() -> frozenset[str]:
    """Return renderable types, never menu/category pseudo component names."""

    catalog = component_catalog()
    found: set[str] = set()
    for group in catalog.get("toolbarGroups") or []:
        for category in group.get("categories") or []:
            for item in category.get("items") or []:
                component = item.get("compType")
                if component:
                    found.add(str(component))
    for item in catalog.get("rendererExtensions") or []:
        component = item.get("compType")
        if component:
            found.add(str(component))
    # SQL-derived spreadsheet reports have a dedicated renderer. It remains an
    # explicit type so arbitrary source component names never become trusted.
    found.update({"JSqlSheet", "JDragImg"})
    return frozenset(found)


@lru_cache(maxsize=1)
def lynx_report_catalog() -> dict:
    """Expose the checked-in direct-report extraction evidence."""

    return json.loads((RESOURCE_ROOT / "lynx_source_reports.json").read_text(encoding="utf-8"))


def _lynx_source_template(source: dict) -> dict:
    """Expose one real Lynx screen as a data-empty, visually identical template."""

    from app.modules.reports.template_lock import (
        lock_template_content,
        template_visual_fingerprint,
        visual_fingerprint,
    )

    source_id = str(source.get("sourceRecordId") or "").strip()
    if not source_id or source.get("sourceKind") != "lynx-report-screen":
        raise RuntimeError("大屏模板来源记录缺少稳定标识或来源类型无效")
    source_content = validate_content(source.get("content") or {})
    if source.get("visualHash") != visual_fingerprint(source_content):
        raise RuntimeError(f"来源报表 {source_id} 的视觉指纹校验失败")
    provenance = f"lynx-report-screen:{source_id}"
    content = validate_content(lock_template_content(
        source_content,
        source_report_id=provenance,
        source_report_version=1,
    ))
    if template_visual_fingerprint(content) != template_visual_fingerprint(source_content):
        raise RuntimeError(f"来源报表 {source_id} 清空数据后视觉结构不一致")

    description = str(source.get("description") or "").strip()
    if description.startswith("[TEMPLATE SHOWCASE]"):
        description = description.removeprefix("[TEMPLATE SHOWCASE]").strip()
    description = public_brand_text(description)
    source_name = str(source.get("name") or f"{PRODUCT_NAME} 大屏 {source_id}")
    source_name = public_brand_text(source_name)
    category = str(source.get("category") or f"{PRODUCT_NAME} 实际报表")
    category = public_brand_text(category)
    tags = [
        public_brand_text(tag).strip()
        for tag in source.get("tags") or []
        if str(tag).strip() and str(tag).strip() not in {"直接导入", "可编辑"}
    ]
    for tag in ("真实报表截图", "视觉锁定", "只填数据"):
        if tag not in tags:
            tags.append(tag)
    component_count = len(content.get("widgets") or [])
    return {
        "id": f"{LYNX_TEMPLATE_ID_PREFIX}{source_id}",
        "code": f"CB-{source_id.zfill(3)}",
        "name": source_name,
        "description": description,
        "summary": description,
        "category": category,
        "tags": tags,
        "content": content,
        "preview": f"{LYNX_REPORT_PREVIEW_ROOT}/lynx-{source_id}.png",
        "enabled": bool(source.get("enabled", True)),
        "version": 1,
        # ``builtin`` is the existing persistence distinction for immutable
        # source templates. The UI identifies these as governed Cloud Brain templates,
        # and each workspace may still delete/recover them independently.
        "builtin": True,
        "componentCount": component_count,
        "componentCountLabel": f"{component_count} 个组件",
        "styleLocked": True,
        "bindingContract": content["canvas"]["bindingContract"],
        "sourceReportId": provenance,
        "sourceReportVersion": 1,
        "sourceKind": "lynx-report-screen",
        "sourceSystem": PRODUCT_NAME,
        "sourceTable": source.get("sourceTable") or "t_report",
        "sourceRecordId": source_id,
        "sourceOrder": source.get("sourceOrder"),
        "createTime": source.get("sourceCreatedAt"),
        "updateTime": source.get("sourceUpdatedAt"),
        "visualHash": content["canvas"]["templateVisualHash"],
        "width": content["canvas"]["width"],
        "height": content["canvas"]["height"],
    }


@lru_cache(maxsize=1)
def builtin_templates() -> tuple[dict, ...]:
    """Return all current Lynx real screens as immutable source templates.

    These are not decorative built-ins. Each item is derived from one
    interface-imported real report, uses that report's verified screenshot,
    and differs only by having runtime data, datasource bindings and SQL
    removed. Workspace users can hide/recover the source templates without
    deleting their real reports.

    目录来自随包的离线提取快照（``resources/lynx_source_reports.json``），运行时不访问外部
    来源服务。启动期自检：模板数量必须与目录声明的 ``reportCount`` 一致且 ID 不重复，
    否则抛 ``RuntimeError``（宁可启动失败，也不静默少给几套模板）。结果进程内缓存。
    """

    catalog = lynx_report_catalog()
    templates = tuple(_lynx_source_template(source) for source in catalog.get("reports") or [])
    expected = int(catalog.get("reportCount") or catalog.get("sourceRecordCount") or 0)
    if len(templates) != expected or len({item["id"] for item in templates}) != len(templates):
        raise RuntimeError("来源报表与模板目录数量或标识不一致")
    return templates


@lru_cache(maxsize=1)
def hifi_templates() -> tuple[dict, ...]:
    """返回参照目录的 20 套高保真模板，并在启动期拒绝不完整或重复的目录。"""

    path = RESOURCE_ROOT / "hifi_templates.json"
    if not path.is_file():
        raise RuntimeError("参考大屏模板目录缺失，不能静默回退到旧模板")
    data = json.loads(path.read_text(encoding="utf-8"))
    templates = tuple(data.get("templates") or [])
    if len(templates) != 20 or len({item.get("id") for item in templates}) != 20:
        raise RuntimeError("高保真大屏模板目录必须包含 20 套唯一模板")
    if any(not str(item.get("id") or "").startswith("hifi-template-") for item in templates):
        raise RuntimeError("高保真大屏模板标识格式无效")
    return templates


def safe_asset_url(value: str) -> bool:
    """判断资源地址是否安全：允许空值、相对路径、白名单图片 data: 与 http(s) 外链。

    拒绝反斜杠路径、``//`` 协议相对地址、带用户名/密码的 URL 以及 ``javascript:`` 等可执行
    协议；判定前先剔除所有控制字符，避免用空白/换行绕过前缀检查。
    """

    compact = "".join(character for character in str(value).strip() if ord(character) > 32)
    if not compact:
        return True
    if "\\" in compact:
        return False
    if compact.startswith("data:image/"):
        return compact.lower().startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/gif;base64,"))
    try:
        parsed = urlsplit(compact)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"", "http", "https"} and not parsed.username and not parsed.password and not compact.startswith("//")


def validate_content(value: dict, *, width: int = 1920, height: int = 1080) -> dict:
    """Validate structural/safety invariants without reducing the component set.

    保存前的结构闸门：内容必须是可 JSON 序列化、无 NaN/无穷、体积 ≤8 MB 的对象；画布尺寸
    320–16384、组件 ≤500 个且 ID 唯一非空、组件类型必须在已迁移目录内、位置尺寸旋转必须是
    有限数值且宽高 >0、所有资源与跳转地址都要通过 ``safe_asset_url``。校验失败抛
    ``ValidationError``（带 component/field 等定位信息）。

    关键约束：**只校验不裁剪**——返回的是补全默认值后的深拷贝，不会因为"组件太多"而丢弃
    任何组件；上限外的内容一律报错，让调用方显式处理。
    """
    if not isinstance(value, dict):
        raise ValidationError("报表内容必须为对象")
    try:
        serialized = json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError("报表含有不可序列化、非有限数或过深的内容") from exc
    if len(serialized.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValidationError("报表配置超过 8 MB，请缩小内嵌数据或改用数据绑定")
    content = deepcopy(value)
    canvas = content.setdefault("canvas", {})
    if not isinstance(canvas, dict):
        raise ValidationError("画布配置必须为对象")
    canvas.setdefault("width", width)
    canvas.setdefault("height", height)
    for key, minimum in (("width", 320), ("height", 200)):
        number = canvas[key]
        if type(number) is not int or not minimum <= number <= 16384:
            raise ValidationError("画布尺寸需要在允许范围内的整数")
    widgets = content.setdefault("widgets", [])
    if not isinstance(widgets, list) or len(widgets) > 500:
        raise ValidationError("一个报表最多支持 500 个组件")
    identifiers = set()
    for widget in widgets:
        if not isinstance(widget, dict):
            raise ValidationError("组件定义必须为对象")
        identity = str(widget.get("id") or widget.get("i") or "")
        if not identity or len(identity) > 160 or identity in identifiers:
            raise ValidationError("每个组件必须有唯一且非空的标识")
        identifiers.add(identity)
        component = widget.get("component")
        if component and component not in component_types():
            raise ValidationError("组件类型不在已迁移目录中", component=component)
        for key in ("x", "y", "w", "h", "rotation"):
            number = widget.get(key)
            if key in widget and (type(number) not in (int, float) or not math.isfinite(number) or abs(number) > 100000):
                raise ValidationError("组件位置、尺寸或旋转参数无效", widget_id=identity, field=key)
        if any(widget.get(key, 1) <= 0 for key in ("w", "h")):
            raise ValidationError("组件尺寸必须大于 0", widget_id=identity)
        config = widget.get("config") or {}
        skin = widget.get("referenceSkin")
        if skin is not None:
            if not isinstance(skin, dict) or skin.get("version") != 1 or not safe_asset_url(skin.get("asset") or ""):
                raise ValidationError("参考组件皮肤或图片地址无效", widget_id=identity)
            for dimension in ("sourceWidth", "sourceHeight"):
                if type(skin.get(dimension)) not in (int, float) or not 0 < skin[dimension] <= 16384:
                    raise ValidationError("参考组件源图尺寸无效", widget_id=identity)
            regions = [skin.get("rect")]
            holes = skin.get("holes", [])
            if not isinstance(holes, list) or len(holes) > 500:
                raise ValidationError("参考组件裁片数量无效", widget_id=identity)
            regions.extend(holes)
            for rect in regions:
                if not isinstance(rect, list) or len(rect) != 4 or any(type(number) not in (int, float) or not math.isfinite(number) for number in rect):
                    raise ValidationError("参考组件裁片坐标无效", widget_id=identity)
                x, y, w, h = rect
                if min(x, y) < 0 or min(w, h) <= 0 or x + w > skin["sourceWidth"] or y + h > skin["sourceHeight"]:
                    raise ValidationError("参考组件裁片超出源图", widget_id=identity)
        if not isinstance(config, dict):
            raise ValidationError("组件 config 必须为对象")
        for drill in (widget.get("drilldown"), config.get("drilldown")):
            if drill is not None and not isinstance(drill, dict):
                raise ValidationError("钻取配置必须为对象")
        for key in ("url", "src", "apiUrl", "imageUrl", "videoUrl"):
            if config.get(key) and not safe_asset_url(config[key]):
                raise ValidationError("组件地址必须是安全的 HTTP(S) 或受管资源地址")
        turn = config.get("turnConfig") or {}
        if not isinstance(turn, dict) or not safe_asset_url(turn.get("url") or ""):
            raise ValidationError("组件跳转地址不安全")
    for key in ("backgroundImage", "heroAsset"):
        if canvas.get(key) and not safe_asset_url(canvas[key]):
            raise ValidationError("背景资源地址不安全")
    return content


def bound_source_ids(content: dict) -> set[str]:
    """收集画布中真实绑定到的数据源 ID（组件 query/config 上的 datasourceId）。

    保存报表时用它计算 ``dataStatus`` 并对这些数据源逐个做权限校验——即"内容里引用到的
    数据源"也必须可访问，避免了只在表单里填了 ID 才校验的漏洞。
    """

    sources = set()
    for widget in content.get("widgets", []):
        config = widget.get("config") or {}
        for value in (widget.get("datasourceId"), config.get("datasourceId"), (config.get("drilldown") or {}).get("datasourceId")):
            if value not in (None, "", 0):
                sources.add(str(value))
    return sources


def template_skeleton(content: dict) -> dict:
    """Return a visually locked, data-empty template safe for customer reuse.

    模板写入路径的统一收口（自定义模板保存、模板包导入、历史恢复都走这里）：先做结构校验，
    再剥离全部运行期绑定、查询结果与凭据并打上视觉锁，最后再次校验结果。因此任何入口提交的
    数据、SQL、参数、凭据都不可能落进模板库，调用方无须自行清理。
    """
    from app.modules.reports.template_lock import lock_template_content

    return validate_content(lock_template_content(validate_content(content)))
