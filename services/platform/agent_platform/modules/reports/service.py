"""Test5结构化模板精细组装：布局编辑、查询绑定、修订、发布与离线导出。"""
from copy import deepcopy
import base64
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path

from sqlalchemy import select, update

from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.files import FileService
from agent_platform.core.models import ResourceReference

from ..data.service import DataService
from ..data.sql_safety import SqlFieldValidationError, ValidationError
from ..manufacturing.service import csv_content
from . import models as m
from .chart_data import CARTESIAN_COMPONENTS, public_series_model
from .inspection_source import InspectionSource, is_inspection_binding, validate_binding
from .presentation import NAMES, PRESENTATION_TYPES, STATIC_TYPES, presentation_model, sanitize_html
from .capabilities import SOURCE_TYPES, source_supported


SUPPORTED_COMPONENTS = {
    "JText": "文本", "JStatsSummary": "指标", "JLine": "折线图", "JArea": "面积图",
    "JBar": "柱状图", "JHorizontalBar": "条形图", "JPie": "饼图", "JRing": "环图",
    "JGauge": "仪表", "JRadar": "雷达图", "JScrollBoard": "数据表", "JDragImg": "装饰图片",
    "text": "文本", "metric": "指标", "line": "折线图", "bar": "柱状图", "table": "数据表",
    "JDragBorder": "科技边框", "JDragDecoration": "装饰线", "JImg": "图片素材", "JCustomIcon": "图标素材", "JCarousel": "图片轮播",
    "JStackBar": "堆叠柱图", "JMultipleBar": "分组柱图", "JPercentBar": "百分比堆叠图", "JMixLineBar": "柱线组合图",
    "JMultipleLine": "多折线图", "JSmoothLine": "平滑折线图", "JStepLine": "阶梯折线图", "JNegativeBar": "正负柱图",
}
SUPPORTED_COMPONENTS.update(NAMES)
ALIASES = {"text": "JText", "metric": "JStatsSummary", "line": "JLine", "bar": "JBar", "table": "JScrollBoard"}
STATIC_COMPONENTS = {"JText", "JDragImg", "JDragBorder", "JDragDecoration", "JImg", "JCustomIcon", "text"}
STATIC_COMPONENTS.update(STATIC_TYPES)


def component_catalog():
    """完整提取源目录，区分目录存在与当前渲染器可用，不用通用表格冒充。"""
    original = json.loads((Path(__file__).parent / "resources" / "component_catalog.json").read_text(encoding="utf-8"))
    items = []
    for group in original["toolbarGroups"]:
        for category in group.get("categories", []):
            for source in category.get("items", []):
                component = source.get("compType", "")
                canonical = ALIASES.get(component, component) if source_supported(source["id"], component, SUPPORTED_COMPONENTS) else None
                if canonical:
                    dependency = None
                elif group["key"] == "material":
                    dependency = {"code": "source_renderer_pending", "source_files": ["JimuWidgetRenderer.vue", "widgetFactory.js"], "next_step": "提取原SVG边框/装饰与素材配置，不以通用卡片代替"}
                elif group["key"] == "map" or "3d" in component.lower() or "3D" in component:
                    dependency = {"code": "specialized_renderer_pending", "source_files": ["JimuChart.vue"], "next_step": "接入实际地图/3D渲染器及本地素材，并验证交互与离线导出"}
                elif group["key"] in ("business", "other", "custom") or component in ("JWeatherForecast", "JIframe", "JForm"):
                    dependency = {"code": "business_behavior_pending", "source_files": ["JimuBusinessWidget.vue", "businessWidgetTypes.js"], "next_step": "提取对应展示分支；数据、表单、外部页面另接授权接口"}
                else:
                    dependency = {"code": "source_renderer_pending", "source_files": ["JimuWidgetRenderer.vue", "JimuChart.vue"], "next_step": "保留原compType对应的真实分支，完成该变体行为验证"}
                items.append({**source, "group": group["key"], "category": category["name"],
                              "canonical_component": canonical, "renderer_supported": canonical is not None,
                              "implementation_dependency": dependency,
                              "source": "Test5/component_catalog.json"})
    groups = [{"id": group["key"], "name": group["label"], "items": sum(item["group"] == group["key"] for item in items), "renderer_supported_items": sum(item["group"] == group["key"] and item["renderer_supported"] for item in items)} for group in original["toolbarGroups"]]
    return {"items": items, "total": len(items), "meta": original["meta"], "groups": original["toolbarGroups"], "group_status": groups, "renderer_extensions": original["rendererExtensions"], "canonical_aliases": ALIASES}


def view(row):
    result = {}
    for c in row.__table__.columns:
        value = getattr(row, c.name)
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result[c.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def builtin_templates():
    """原样保留20套模板结构，样例字段明确标记为未绑定。"""
    source = json.loads((Path(__file__).parent / "resources" / "hifi_templates.json").read_text(encoding="utf-8"))
    return [{"id": item["id"], "name": item["name"], "description": item.get("description", ""),
             "layout": item["content"], "preview": item.get("preview"), "category": item.get("category"),
             "revision": item.get("version", 1), "builtin": True, "source": "Test5/hifi_templates.json",
             "sample_data": True, "status": "published"} for item in source["templates"]]


def validate_layout(layout):
    """在保存时校验画布与组件边界，防止脚本/任意地址进入渲染器。"""
    if not isinstance(layout, dict) or not isinstance(layout.get("widgets"), list) or len(layout["widgets"]) > 200:
        raise DomainError(422, "layout_invalid", "报表必须包含不超过200个组件")
    try:
        encoded = json.dumps(layout, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise DomainError(422, "layout_invalid", "布局只接受有限数值和有效JSON") from exc
    if len(encoded) > 2_000_000:
        raise DomainError(422, "layout_too_large", "报表布局过大")
    canvas = layout.get("canvas", {})
    if not isinstance(canvas, dict):
        raise DomainError(422, "canvas_invalid", "画布配置格式无效")
    for key in ("width", "height"):
        value = canvas.get(key, 1440 if key == "width" else 900)
        if not isinstance(value, (int, float)) or not isfinite(value) or not 100 <= value <= 10000:
            raise DomainError(422, "canvas_size_invalid", "画布尺寸须为100到10000")
    seen = set()
    for widget in layout["widgets"]:
        if not isinstance(widget, dict) or not isinstance(widget.get("id"), str) or not 1 <= len(widget["id"]) <= 100 or widget["id"] in seen:
            raise DomainError(422, "widget_id_invalid", "组件标识不能为空或重复")
        seen.add(widget["id"])
        config = widget.get("config", {})
        if not isinstance(config, dict) or ("option" in config and not isinstance(config["option"], dict)):
            raise DomainError(422, "widget_config_invalid", "组件配置格式无效")
        if "body" in config.get("option", {}) and not isinstance(config["option"]["body"], dict):
            raise DomainError(422, "widget_config_invalid", "文本组件内容配置格式无效")
        if widget.get("component") == "JDragEditor":
            config.setdefault("option", {}).setdefault("body", {})["text"] = sanitize_html(config.get("option", {}).get("body", {}).get("text", ""))
        if widget.get("component") == "JCurrentTime" and config.get("option", {}).get("timezone", "Asia/Shanghai") not in {"Asia/Shanghai", "UTC"}:
            raise DomainError(422, "clock_timezone_invalid", "请选择北京时间或UTC")
        if "column_labels" in config:
            labels = config["column_labels"]
            if not isinstance(labels, dict) or len(labels) > 20 or any(not isinstance(key, str) or not isinstance(value, str) or not 1 <= len(key) <= 200 or len(value) > 200 for key, value in labels.items()):
                raise DomainError(422, "column_labels_invalid", "列名称无效或超过20列")
        if any(key in config for key in ("sql", "datasourceId", "apiUrl", "headers", "password", "token", "apiKey")):
            raise DomainError(422, "widget_connection_not_allowed", "组件请通过已授权查询绑定数据")
        if "images" in config:
            images = config["images"]
            if not isinstance(images, list) or len(images) > 50 or any(not isinstance(item, (str, dict)) for item in images):
                raise DomainError(422, "carousel_images_invalid", "轮播图片列表无效或超过50张")
        widget.pop("_export_image_uri", None)
        component = widget.get("component", widget.get("type", "text"))
        if not source_supported(widget.get("source_component_id"), component, SUPPORTED_COMPONENTS):
            raise DomainError(422, "component_unsupported", "组件类型未登记", {"component": component})
        if component in SOURCE_TYPES:
            from .source_presentations import validate_options
            try:
                validate_options(widget)
            except ValueError as exc:
                raise DomainError(422, "component_options_invalid", str(exc), {"widget_id": widget["id"]}) from exc
        for key in ("x", "y", "w", "h"):
            value = widget.get(key, 0 if key in ("x", "y") else 100)
            if not isinstance(value, (int, float)) or not isfinite(value) or value < 0 or value > 10000:
                raise DomainError(422, "widget_bounds_invalid", "组件位置或尺寸无效")
        for key, low, high in (("rotation", -36000, 36000), ("orderNum", 0, 10000)):
            value = widget.get(key, 0)
            if not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise DomainError(422, "widget_bounds_invalid", "组件旋转或层级无效")
        if "fontSize" in config:
            value = config["fontSize"]
            if not isinstance(value, (int, float)) or not isfinite(value) or not 1 <= value <= 10000:
                raise DomainError(422, "widget_config_invalid", "组件字号无效")
        binding = widget.get("binding")
        if binding is not None:
            validate_binding(binding)
        if binding:
            for key in ("label_field", "value_field", "description_field", "time_field", "unit_field", "total_field"):
                if key in binding and (not isinstance(binding[key], str) or not 1 <= len(binding[key]) <= 200):
                    raise DomainError(422, "binding_invalid", "请选择有效的分类与数值字段")
            for key in ("value_fields", "secondary_value_fields"):
                if key in binding:
                    fields = binding[key]
                    if not isinstance(fields, list) or len(fields) > 8 or (key == "value_fields" and not fields) or any(not isinstance(field, str) or not 1 <= len(field) <= 200 for field in fields) or len(set(fields)) != len(fields):
                        raise DomainError(422, "binding_invalid", "请选择不超过8个不同数值字段")
            if "fields" in binding and (not isinstance(binding["fields"], list) or not 1 <= len(binding["fields"]) <= 20 or any(not isinstance(field, str) or not 1 <= len(field) <= 200 for field in binding["fields"]) or len(set(binding["fields"])) != len(binding["fields"])):
                raise DomainError(422, "binding_invalid", "请选择不超过20个不同列字段")
        provenance = widget.get("data_provenance")
        if provenance is not None:
            if not isinstance(provenance, dict) or ("query_id" in provenance and (not isinstance(provenance["query_id"], str) or not 1 <= len(provenance["query_id"]) <= 100)):
                raise DomainError(422, "data_provenance_invalid", "组件数据来源标识无效")
        skin = widget.get("referenceSkin")
        if skin is not None:
            if not isinstance(skin, dict):
                raise DomainError(422, "reference_skin_invalid", "参考皮肤格式无效")
            for key in ("sourceWidth", "sourceHeight", "fontSize", "valueFontSize"):
                value = skin.get(key, 1)
                if not isinstance(value, (int, float)) or not isfinite(value) or not 0 < value <= 10000:
                    raise DomainError(422, "reference_skin_invalid", "参考皮肤尺寸无效")
            rectangles = [skin["rect"]] if "rect" in skin else []
            holes = skin.get("holes", [])
            if not isinstance(holes, list) or len(holes) > 200:
                raise DomainError(422, "reference_skin_invalid", "参考遮罩范围无效")
            for rect in rectangles + holes:
                if not isinstance(rect, list) or len(rect) != 4 or any(not isinstance(v, (int, float)) or not isfinite(v) or abs(v) > 10000 for v in rect):
                    raise DomainError(422, "reference_skin_invalid", "参考图形范围无效")
    return deepcopy(layout)


class ReportService:
    def __init__(self, session, actor, settings=None):
        self.session, self.actor, self.settings = session, actor, settings

    def get(self, row_id, model=m.Report):
        row = self.session.scalar(select(model).where(model.id == row_id, model.workspace_id == self.actor.workspace_id, model.archived.is_(False)))
        if row is None:
            raise DomainError(404, "report_not_found", "报表不存在或不在当前工作区")
        self._authorize_layout(row.layout)
        return row

    def _authorize_layout(self, layout):
        """已保存的报表数据同样执行当前字段授权。"""
        InspectionSource(self.session, self.actor, self.settings).validate(layout)
        data_service = DataService(self.session, self.actor, self.settings)
        checked = set()
        for widget in layout.get("widgets", []):
            query_id = (widget.get("data_provenance") or {}).get("query_id")
            if query_id and query_id not in checked:
                try:
                    data_service.get_query(query_id)
                except ValidationError as exc:
                    if isinstance(exc, SqlFieldValidationError) or exc.fields.get("table"):
                        raise DomainError(403, "report_evidence_access_denied", "当前账号无法访问报表的原始数据") from exc
                    raise
                checked.add(query_id)
        for file_id in self._image_file_ids(layout):
            FileService(self.session, self.actor, self.settings).get(file_id)

    @staticmethod
    def _image_file_ids(layout):
        result = set()
        for widget in layout.get("widgets", []):
            if widget.get("component") not in ("JImg", "JCustomIcon", "JCarousel", "JDragImg"):
                continue
            config = widget.get("config", {})
            candidates = [config.get("file_id")]
            images = config.get("images", [])
            if isinstance(images, list):
                candidates.extend(item.get("file_id") for item in images if isinstance(item, dict))
            for file_id in candidates:
                if file_id is not None:
                    if not isinstance(file_id, str) or not 1 <= len(file_id) <= 100:
                        raise DomainError(422, "image_file_id_invalid", "图片文件标识无效")
                    result.add(file_id)
        return result

    def _register_assets(self, row):
        source_type = "report_template" if isinstance(row, m.ReportTemplate) else "report"
        for file_id in self._image_file_ids(row.layout):
            metadata = FileService(self.session, self.actor, self.settings).get(file_id)
            if metadata["size"] > 2 * 1024 * 1024:
                raise DomainError(422, "image_file_too_large", "报表图片不能超过2MB")
            existing = self.session.scalar(select(ResourceReference.id).where(ResourceReference.workspace_id == self.actor.workspace_id, ResourceReference.source_type == source_type, ResourceReference.source_id == row.id, ResourceReference.target_type == "file", ResourceReference.target_id == file_id))
            if existing is None:
                self.session.add(ResourceReference(workspace_id=self.actor.workspace_id, source_type=source_type, source_id=row.id, target_type="file", target_id=file_id))
        self.session.flush()

    def _authorized_images(self, layout):
        """只从当前授权文件读取有限位图，既用于预览也用于离线导出。"""
        images = {}
        total_bytes = 0
        files = FileService(self.session, self.actor, self.settings)
        for file_id in self._image_file_ids(layout):
            metadata = files.get(file_id)
            if metadata["size"] > 2 * 1024 * 1024:
                raise DomainError(422, "image_file_too_large", "报表图片不能超过2MB")
            with files.path(file_id).open("rb") as stream:
                content = stream.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise DomainError(422, "image_file_too_large", "报表图片不能超过2MB")
            mime = "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if content.startswith(b"\xff\xd8\xff") else "image/webp" if content.startswith(b"RIFF") and content[8:12] == b"WEBP" else None
            if mime is None:
                raise DomainError(422, "image_content_invalid", "图片内容不是可读取的PNG、JPEG或WebP")
            images[file_id] = "data:" + mime + ";base64," + base64.b64encode(content).decode()
            total_bytes += len(images[file_id])
            if total_bytes > 5 * 1024 * 1024:
                raise DomainError(422, "image_assets_too_large", "图片总量过大，请缩小图片或拆分报表")
        return images

    def image_assets(self, row_id):
        row = self.get(row_id)
        images = self._authorized_images(row.layout)
        return {"items": [{"file_id": key, "data_uri": value} for key, value in images.items()], "total": len(images)}

    def _embed_authorized_images(self, report):
        """导出不请求签名URL或任意文件路径，不向保存布局写入图片内容。"""
        result = deepcopy(report)
        images = self._authorized_images(result["layout"])
        for widget in result["layout"]["widgets"]:
            config = widget.get("config", {})
            if config.get("file_id") in images:
                widget["_export_image_uri"] = images[config["file_id"]]
            for item in config.get("images", []):
                if isinstance(item, dict) and item.get("file_id") in images:
                    item["_export_image_uri"] = images[item["file_id"]]
        return result

    def list(self, *, q="", limit=50, offset=0, model=m.Report):
        clauses = [model.workspace_id == self.actor.workspace_id, model.archived.is_(False)]
        if q:
            clauses.append(model.name.contains(q, autoescape=True))
        rows = self.session.scalars(select(model).where(*clauses).order_by(model.updated_at.desc(), model.id).execution_options(yield_per=200))
        items, total = [], 0
        start, size = max(offset, 0), min(max(limit, 1), 200)
        # 先按当前源授权计数和分页；不能泄露隐藏报告的数量或让一条撤权记录阻塞整个目录。
        for row in rows:
            try:
                self._authorize_layout(row.layout)
            except DomainError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            if start <= total < start + size:
                items.append(view(row))
            total += 1
        return {"items": items, "total": total}

    def templates(self, q=""):
        builtin = [t for t in builtin_templates() if q.casefold() in t["name"].casefold()]
        custom, offset = [], 0
        while True:
            page = self.list(q=q, limit=200, offset=offset, model=m.ReportTemplate)
            custom.extend(page["items"])
            offset += len(page["items"])
            if offset >= page["total"]:
                break
        return {"items": builtin + custom, "total": len(builtin) + len(custom)}

    def template(self, row_id):
        for template in builtin_templates():
            if template["id"] == row_id:
                return template
        return view(self.get(row_id, m.ReportTemplate))

    def _snapshot(self, row):
        self.session.add(m.ReportRevision(report_id=row.id, number=row.revision, snapshot=view(row), created_by=self.actor.user_id))
        self.session.flush()

    def _derive_series_models(self, layout):
        """仅为已核验查询或检验证据生成模型，客户端模型不能充当业务数据。"""
        cache = {}
        service = DataService(self.session, self.actor, self.settings)
        for widget in layout["widgets"]:
            widget.pop("series_model", None)
            widget.pop("presentation_model", None)
            component = ALIASES.get(widget.get("component"), widget.get("component"))
            if component in STATIC_TYPES:
                widget["presentation_model"] = presentation_model(widget, [])
                continue
            if component not in CARTESIAN_COMPONENTS | PRESENTATION_TYPES:
                continue
            derive = public_series_model if component in CARTESIAN_COMPONENTS else presentation_model
            provenance = widget.get("data_provenance") or {}
            binding = widget.get("binding") or {}
            if is_inspection_binding(binding):
                canonical = {**widget, "component": ALIASES.get(widget.get("component"), widget.get("component"))}
                widget["series_model" if component in CARTESIAN_COMPONENTS else "presentation_model"] = derive(canonical, widget.get("data", []))
                continue
            if provenance.get("type") != "query" or not binding.get("query_id") or not provenance.get("query_id"):
                continue
            for query_id in (provenance["query_id"], binding["query_id"]):
                if query_id not in cache:
                    cache[query_id] = service.get_query(query_id)
            evidence, original = cache[provenance["query_id"]], cache[binding["query_id"]]
            if widget.get("data") != evidence.result["rows"] or evidence.datasource_id != original.datasource_id or evidence.sql != original.sql or evidence.parameters != original.parameters:
                continue
            canonical = {**widget, "component": ALIASES.get(widget.get("component"), widget.get("component"))}
            widget["series_model" if component in CARTESIAN_COMPONENTS else "presentation_model"] = derive(canonical, evidence.result["rows"])

    def create(self, data, *, model=m.Report, _trusted_layout=False):
        layout = data.get("layout")
        if data.get("template_id"):
            template = self.template(data["template_id"])
            layout = layout or template["layout"]
        if layout is None:
            layout = {"canvas": {"width": 1440, "height": 900, "backgroundColor": "#ffffff"}, "widgets": []}
        layout = validate_layout(deepcopy(layout))
        if layout.get("kind") == "inspection_report" and not _trusted_layout:
            raise DomainError(422, "inspection_generation_required", "请从检验运行生成正式报告")
        if not _trusted_layout:
            InspectionSource(self.session, self.actor, self.settings).prepare(layout)
        for widget in layout.get("widgets", []):
            if widget.get("visualRole") == "dashboard-title":
                widget.setdefault("config", {}).update(chartDataParsed=[{"name": "报表标题", "value": data["name"]}], referenceSample=False)
        values = {"workspace_id": self.actor.workspace_id, "name": data["name"], "description": data.get("description", ""), "layout": layout}
        if model is m.Report:
            values["template_id"] = data.get("template_id")
        self._authorize_layout(values["layout"])
        self._derive_series_models(values["layout"])
        row = model(**values)
        self.session.add(row)
        self.session.flush()
        if model is m.Report:
            self._snapshot(row)
        self._register_assets(row)
        RecordStore(self.session, self.actor).audit("create", model.__tablename__, row.id)
        return view(row)

    def patch(self, row_id, data, *, model=m.Report, _trusted_layout=False):
        row = self.get(row_id, model)
        revision = data["revision"]
        changes = {k: v for k, v in data.items() if k != "revision" and v is not None}
        if "layout" in changes:
            if row.layout.get("kind") == "inspection_report" and not _trusted_layout:
                raise DomainError(422, "inspection_report_layout_read_only", "检验报告正文请通过刷新来源更新")
            changes["layout"] = validate_layout(deepcopy(changes["layout"]))
            if changes["layout"].get("kind") == "inspection_report" and not _trusted_layout:
                raise DomainError(422, "inspection_generation_required", "请从检验运行生成正式报告")
            if not _trusted_layout:
                InspectionSource(self.session, self.actor, self.settings).prepare(changes["layout"], previous=row.layout)
            self._authorize_layout(changes["layout"])
            self._derive_series_models(changes["layout"])
        if "status" not in changes:
            changes["status"] = "draft"
        response = self.session.execute(update(model).where(model.id == row.id, model.workspace_id == self.actor.workspace_id, model.revision == revision).values(**changes, revision=revision + 1, updated_at=m.now()))
        if response.rowcount != 1:
            raise DomainError(409, "revision_conflict", "报表已更新，请刷新后重试")
        self.session.flush()
        self.session.refresh(row)
        if model is m.Report:
            self._snapshot(row)
        self._register_assets(row)
        RecordStore(self.session, self.actor).audit("update", model.__tablename__, row.id)
        return view(row)

    def revisions(self, row_id):
        row = self.get(row_id)
        revisions = self.session.scalars(select(m.ReportRevision).where(m.ReportRevision.report_id == row.id).order_by(m.ReportRevision.number.desc())).all()
        for revision in revisions:
            self._authorize_layout(revision.snapshot["layout"])
        return {"items": [view(r) for r in revisions], "total": len(revisions)}

    def restore(self, row_id, revision, target_revision):
        self.get(row_id)
        target = self.session.scalar(select(m.ReportRevision).where(m.ReportRevision.report_id == row_id, m.ReportRevision.number == target_revision))
        if target is None:
            raise DomainError(404, "report_revision_not_found", "目标版本不存在")
        self._authorize_layout(target.snapshot["layout"])
        return self.patch(row_id, {"revision": revision, "name": target.snapshot["name"], "description": target.snapshot["description"], "layout": target.snapshot["layout"]}, _trusted_layout=True)

    def refresh(self, row_id, revision):
        row = self.get(row_id)
        if revision != row.revision:
            raise DomainError(409, "revision_conflict", "报表已更新，请刷新后重试")
        layout, query_cache = deepcopy(row.layout), {}
        inspection_bound = any(is_inspection_binding(widget.get("binding")) for widget in layout["widgets"])
        if inspection_bound:
            InspectionSource(self.session, self.actor, self.settings).prepare(layout, previous=row.layout, refresh=True)
        data_service = DataService(self.session, self.actor, self.settings)
        for widget in layout["widgets"]:
            if widget.get("component") == "JText" and any(text in widget.get("componentName", "") for text in ("统计时间", "刷新时间", "生成时间")):
                widget.setdefault("config", {}).update(chartDataParsed=[{"name": "刷新时间", "value": m.now().isoformat(timespec="seconds")}], referenceSample=False)
            binding = widget.get("binding")
            if not binding:
                continue
            if is_inspection_binding(binding):
                continue
            self.actor.require("m06.read")
            query_id = binding["query_id"]
            if query_id not in query_cache:
                source_query = data_service.get_query(query_id)
                query_cache[query_id] = data_service.execute_query({"datasource_id": source_query.datasource_id, "sql": source_query.sql, "parameters": source_query.parameters, "max_rows": 10000})
            query = query_cache[query_id]
            widget["data"] = deepcopy(query["rows"])
            widget["data_provenance"] = {"type": "query", "query_id": query["id"], "datasource_id": query["datasource_id"], "truncated": query["truncated"], "refreshed_at": m.now().isoformat()}
            config = widget.setdefault("config", {})
            config.update(dataStatus="bound", referenceSample=False, chartDataParsed=deepcopy(query["rows"]))
        if not query_cache and not inspection_bound:
            raise DomainError(422, "report_bindings_missing", "报表尚未绑定数据查询")
        return self.patch(row_id, {"revision": revision, "layout": layout}, _trusted_layout=True)

    def publish(self, row_id, revision, *, model=m.Report):
        row = self.get(row_id, model)
        if model is m.Report:
            self.validate_data(row.layout)
            return self.patch(row_id, {"revision": revision, "status": "published", "published_revision": revision + 1})
        return self.patch(row_id, {"revision": revision, "status": "published"}, model=model)

    def validate_data(self, layout):
        """发布时不允许模板示例值冒充已完成数据绑定。"""
        InspectionSource(self.session, self.actor, self.settings).validate(layout, current=True)
        cache = {}
        service = DataService(self.session, self.actor, self.settings)
        for widget in layout["widgets"]:
            if is_inspection_binding(widget.get("binding")):
                continue
            # 静态资格取决于真实组件语义，不能改type字段绕过图表取数校验。
            if widget.get("component") in STATIC_COMPONENTS or (widget.get("component") == "JCarousel" and widget.get("config", {}).get("images")):
                continue
            provenance = widget.get("data_provenance") or {}
            if provenance.get("type") != "query" or not widget.get("binding") or provenance.get("truncated"):
                raise DomainError(422, "report_data_not_ready", "组件尚未完成真实数据绑定", {"widget_id": widget["id"]})
            for key in (provenance["query_id"], widget["binding"]["query_id"]):
                if key not in cache:
                    cache[key] = service.get_query(key)
            evidence = cache[provenance["query_id"]]
            binding = cache[widget["binding"]["query_id"]]
            # 截断状态以已执行查询证据为准，不能信任客户端可编辑的来源标记。
            if evidence.result.get("truncated"):
                raise DomainError(422, "report_data_not_ready", "查询结果不完整，请缩小范围后刷新", {"widget_id": widget["id"]})
            if evidence.datasource_id != binding.datasource_id or evidence.sql != binding.sql or evidence.parameters != binding.parameters or widget.get("data") != evidence.result["rows"]:
                raise DomainError(422, "report_data_evidence_mismatch", "组件数据与已保存的查询证据不一致", {"widget_id": widget["id"]})
            if ALIASES.get(widget.get("component"), widget.get("component")) in CARTESIAN_COMPONENTS:
                canonical = {**widget, "component": ALIASES.get(widget.get("component"), widget.get("component"))}
                model = public_series_model(canonical, evidence.result["rows"])
                if model["status"] != "ready":
                    raise DomainError(422, "chart_data_not_ready", model.get("message", "当前图表没有可展示的数据"), {"widget_id": widget["id"]})
            elif widget.get("component") in PRESENTATION_TYPES:
                model = presentation_model(widget, evidence.result["rows"])
                if model["status"] != "ready":
                    raise DomainError(422, "presentation_data_not_ready", model.get("message", "当前组件没有可展示的数据"), {"widget_id": widget["id"]})

    def drilldown(self, row_id, widget_id, filters):
        report = self.get(row_id)
        widget = next((w for w in report.layout["widgets"] if w["id"] == widget_id), None)
        if widget is None or not widget.get("binding"):
            raise DomainError(404, "widget_binding_missing", "该组件没有可钻取的数据")
        if is_inspection_binding(widget["binding"]):
            rows = widget["data"]
            columns = list(dict.fromkeys(key for item in rows for key in item))
            if not set(filters).issubset(columns):
                raise DomainError(422, "drilldown_field_invalid", "筛选字段不在组件数据中")
            selected = [item for item in rows if all(str(item.get(key)) == str(value) for key, value in filters.items())]
            return {"columns": columns, "rows": deepcopy(selected), "total": len(selected), "truncated": False}
        query = DataService(self.session, self.actor, self.settings).get_query(widget["binding"]["query_id"])
        if not set(filters).issubset(query.result["columns"]):
            raise DomainError(422, "drilldown_field_invalid", "筛选字段不在组件数据中")
        rows = [r for r in query.result["rows"] if all(str(r.get(k)) == str(v) for k, v in filters.items())]
        return {"columns": query.result["columns"], "rows": rows, "total": len(rows), "truncated": query.result["truncated"]}

    def generate(self, data):
        template = self.template(data["template_id"])
        layout = deepcopy(template["layout"])
        known = {w["id"] for w in layout["widgets"]}
        if not set(data.get("bindings", {})).issubset(known):
            raise DomainError(422, "widget_not_found", "绑定引用了模板外组件")
        for widget in layout["widgets"]:
            if widget["id"] in data.get("bindings", {}):
                binding = data["bindings"][widget["id"]]
                widget["binding"] = {"query_id": binding} if isinstance(binding, str) else {key: value for key, value in binding.items() if value is not None}
        report = self.create({"name": data["name"], "template_id": template["id"], "layout": layout})
        return self.refresh(report["id"], report["revision"]) if data.get("bindings") else report

    def generate_inspection(self, data):
        """从指定运行构建只读正式文档；不接受客户端事实或自定义来源快照。"""
        source = InspectionSource(self.session, self.actor, self.settings)
        snapshot = source.capture(data["inspection_id"], data["run_id"], data["inspection_revision"])
        layout = json.loads((Path(__file__).parent / "resources" / "inspection_report_v1.json").read_text(encoding="utf-8"))
        for widget in layout["widgets"]:
            widget["binding"].update(inspection_id=data["inspection_id"], run_id=data["run_id"])
        layout = validate_layout(layout)
        # 首次捕获已检查调用者传入的任务版本；后续正文由同一快照生成。
        source.prepare(layout, previous={"inspection_source": snapshot})
        return self.create({"name": data["name"], "layout": layout}, _trusted_layout=True)

    def export(self, row_id, format, revision=None):
        row = self.get(row_id)
        data = view(row)
        if revision is not None:
            old = self.session.scalar(select(m.ReportRevision).where(m.ReportRevision.report_id == row.id, m.ReportRevision.number == revision))
            if old is None:
                raise DomainError(404, "report_revision_not_found", "报表版本不存在")
            data = old.snapshot
        self._authorize_layout(data["layout"])
        # 导出前同样校验当前权限，历史快照不能绕过字段权限撤销。
        for widget in data["layout"]["widgets"]:
            provenance = widget.get("data_provenance") or {}
            if provenance.get("query_id"):
                evidence = DataService(self.session, self.actor, self.settings).get_query(provenance["query_id"])
                if widget.get("data") != evidence.result["rows"]:
                    raise DomainError(422, "report_data_evidence_mismatch", "组件数据与来源证据不一致，请刷新数据后导出", {"widget_id": widget["id"]})
        RecordStore(self.session, self.actor).audit("export", "report", row.id, {"format": format, "revision": data["revision"]})
        if format == "json":
            return json.dumps(data, ensure_ascii=False, indent=2), "application/json", "json"
        if format == "csv":
            rows = []
            for widget in data["layout"]["widgets"]:
                items = widget_rows(widget)
                if widget.get("component") in PRESENTATION_TYPES:
                    from .presentation import office_section
                    section = office_section(widget, items)
                    selected = section.get("rows", [])
                    items = [dict(zip(selected[0], values)) for values in selected[1:]] if selected else []
                for item in items:
                    rows.append({"widget_id": widget["id"], "widget": widget.get("componentName", widget.get("name", "")), **item})
            return csv_content(rows), "text/csv; charset=utf-8", "csv"
        if format in ("docx", "xlsx", "pptx"):
            from .exports import numeric_chart, office_export
            if data["layout"].get("kind") == "inspection_report":
                from .inspection_document import office_sections
                return office_export(format, data["name"], office_sections(data))
            sections = []
            for widget in data["layout"]["widgets"]:
                if widget.get("visible") is False or widget.get("type") == "decoration":
                    continue
                values = widget_rows(widget)
                from .presentation import office_section
                projected_section = office_section(widget, values)
                if projected_section is not None:
                    sections.append(projected_section)
                    continue
                fields = list(dict.fromkeys(k for row in values for k in row))
                section = {"title": widget.get("componentName", widget.get("name", widget["id"]))}
                if fields:
                    section["rows"] = [fields] + [[row.get(key, "") for key in fields] for row in values]
                elif widget.get("component") in STATIC_COMPONENTS - {"JText", "text"}:
                    continue
                else:
                    section["text"] = str(widget.get("config", {}).get("option", {}).get("body", {}).get("text", "暂无数据"))
                if widget.get("component") not in ("JText", "JDragImg", "text"):
                    section["chart"] = numeric_chart(widget, values)
                sections.append(section)
            return office_export(format, data["name"], sections)
        if format in ("pdf", "png"):
            from .pdf import render_pdf
            if data["layout"].get("kind") == "inspection_report":
                if format == "png":
                    raise DomainError(422, "inspection_document_png_unsupported", "多页检验报告请导出PDF或Office文档")
                return render_pdf(render_html(data), format="pdf", width=794, height=1123), "application/pdf", "pdf"
            canvas = data["layout"].get("canvas", {})
            return render_pdf(render_html(self._embed_authorized_images(data)), format=format, width=canvas.get("width", 1440), height=canvas.get("height", 900)), "application/pdf" if format == "pdf" else "image/png", format
        return render_html(self._embed_authorized_images(data)), "text/html; charset=utf-8", "html"


def widget_rows(widget):
    value = widget.get("data", widget.get("config", {}).get("chartDataParsed", []))
    return [v if isinstance(v, dict) else {"value": v} for v in value] if isinstance(value, list) else [{"value": value}]


def render_html(report):
    """导出沿用绝对画布与组件类型，具体渲染与业务事务分离。"""
    from .rendering import render_html as render_canvas
    return render_canvas(report)
