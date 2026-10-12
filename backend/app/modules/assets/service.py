"""Permission-safe aggregation of real resources and governed business semantics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import String, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.common.authorization import (
    access_policy,
    allowed_owner_ids,
    require_permission,
    require_resource,
    resource_grant_ids,
)
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.models import Artifact, Connector, DataSource, File, KnowledgeBase, Plugin, Skill, WorkspaceMember
from app.modules.alerts.models import AlertRule, Device
from app.modules.analytics.models import QueryTemplate, SourceMetadata
from app.modules.assets.models import AssetAnnotation, AssetRelation, BusinessTerm, MetricDefinition
from app.modules.assets.schemas import AnnotationInput, AssetRef, AssetRelationInput, BusinessTermInput, MetricDefinitionInput
from app.modules.files.models import FileCategory, FileMetadata
from app.modules.knowledge_graph.models import (
    BusinessBot,
    ExtractionJob,
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
    Ontology,
    OntologyConcept,
    OntologyRelationType,
)
from app.modules.quality.models import QualityMetric, QualityTemplate
from app.modules.reports.models import PromptTemplate, Report, ReportTemplate
from app.modules.system.service_helpers import commit_change
from app.modules.workflows.models import Workflow


@dataclass(frozen=True)
class ResourceSpec:
    """一类可聚合资源的登记项（不可变）：键、中文标签、模型类、查看权限点、来源模块、
    打开路径、以及描述/状态/类型/分类/更新时间各自对应的模型字段名（空串表示该类没有该字段）。
    新增可聚合资源只需在 RESOURCE_SPECS 里加一项，_branch 会自动生成对应的联合查询分支。
    """
    key: str
    label: str
    model: type
    permission: str
    source_module: str
    open_path: str
    description_attr: str = "description"
    status_attr: str = "status"
    kind_attr: str = ""
    category_attr: str = ""
    updated_attr: str = "updated_at"


RESOURCE_SPECS = (
    ResourceSpec("datasource", "数据源", DataSource, "datasources.view", "数据分析", "/datasources", description_attr="", kind_attr="provider"),
    ResourceSpec("file", "资料文件", File, "files.view", "文件中心", "/files", description_attr="", kind_attr="mime_type", updated_attr="created_at"),
    ResourceSpec("artifact", "生成结果", Artifact, "files.view", "生成结果", "/files", description_attr="", kind_attr="type", updated_attr="created_at"),
    ResourceSpec("knowledge_base", "知识库", KnowledgeBase, "knowledge.view", "知识管理", "/knowledge", kind_attr="visibility"),
    ResourceSpec("report", "报表与大屏", Report, "dashboards.view", "报表设计", "/dashboards", kind_attr="template_mode"),
    ResourceSpec("report_template", "报表模板", ReportTemplate, "templates.view", "模板中心", "/templates", status_attr="", kind_attr="category", category_attr="category"),
    ResourceSpec("prompt", "提示词", PromptTemplate, "prompts.view", "提示词库", "/prompts", kind_attr="mode"),
    ResourceSpec("query_template", "查询模板", QueryTemplate, "analytics.view", "数据分析", "/analytics", description_attr="", status_attr="", kind_attr="query_mode", category_attr="category"),
    ResourceSpec("workflow", "工作流", Workflow, "workflows.view", "工作流", "/workflows", kind_attr=""),
    ResourceSpec("device", "设备", Device, "devices.view", "设备管理", "/alerts/devices", kind_attr="category", category_attr="category"),
    ResourceSpec("alert_rule", "预警规则", AlertRule, "alerts.view", "设备预警", "/alerts/rules", kind_attr=""),
    ResourceSpec("quality_metric", "质检指标", QualityMetric, "quality.view", "质量检查", "/quality/metrics", kind_attr="category", category_attr="category"),
    ResourceSpec("quality_template", "质检模板", QualityTemplate, "quality.view", "质量检查", "/quality/templates", kind_attr="device_type", category_attr="device_type"),
    ResourceSpec("ontology", "概念模型", Ontology, "ontology.view", "知识建模", "/ontology", kind_attr=""),
    ResourceSpec("extraction", "抽取任务", ExtractionJob, "extraction.view", "知识抽取", "/extraction", kind_attr="mode"),
    ResourceSpec("graph", "知识图谱", KnowledgeGraph, "graphs.view", "知识图谱", "/graphs", kind_attr=""),
    ResourceSpec("bot", "Bot 与智能体", BusinessBot, "bots.view", "Bot 与智能体", "/bots", kind_attr="bot_type"),
    ResourceSpec("skill", "Skill", Skill, "skills.view", "AI 能力", "/skills", kind_attr=""),
    ResourceSpec("plugin", "Plugin", Plugin, "plugins.view", "AI 能力", "/plugins", kind_attr=""),
    ResourceSpec("connector", "连接器", Connector, "connectors.view", "连接器", "/connectors", description_attr="", kind_attr="provider"),
)
SPEC_BY_KEY = {item.key: item for item in RESOURCE_SPECS}


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串；None 原样返回 None。"""
    return value.isoformat() if value else None


def _version(record, expected: int | None, *, creating: bool = False) -> None:
    """统一乐观锁校验：新建不得带 version，修改必须带且一致。异常：ValidationError / ConflictError。"""
    if creating:
        if expected is not None:
            raise ValidationError("新建记录不能提交 version")
        return
    if expected is None:
        raise ValidationError("修改记录必须提交 version")
    if record.version != expected:
        raise ConflictError("记录已被其他操作修改，请刷新后重试", current_version=record.version)


def _commit(ctx: AuthContext, action: str, resource_type: str, resource_id: str) -> None:
    """提交变更并写审计；把唯一约束冲突与并发写冲突统一翻译成 ConflictError。
    参数 action/resource_type/resource_id 会写进审计记录，因此调用方必须传真实的资源标识。
    """
    try:
        commit_change(ctx, action, resource_type, resource_id)
    except (ConflictError,):
        raise
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("编码或资源关系已经存在") from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("记录已被其他操作修改，请刷新后重试") from exc


def _spec(asset_type: str) -> ResourceSpec:
    """按 asset_type 取登记项。异常：NotFoundError（该资源类型未登记）。"""
    result = SPEC_BY_KEY.get(asset_type)
    if not result:
        raise NotFoundError("资源类型未登记")
    return result


def _status_expression(spec: ResourceSpec):
    """生成"状态"列表达式。特例：报表模板用的是 enabled 布尔列，需要 case 映射成 active/disabled；
    其余类型直接取登记的 status 列，没有状态列的类型统一视为 active。
    """
    if spec.key == "report_template":
        return case((ReportTemplate.enabled.is_(True), literal("active")), else_=literal("disabled"))
    return getattr(spec.model, spec.status_attr) if spec.status_attr else literal("active")


def _branch(ctx: AuthContext, spec: ResourceSpec):
    """为某类资源生成统一的目录查询（列齐 asset_type/asset_id/name/描述/状态/归属/时间/类型/分类）。
    要点：文件类会 left join FileMetadata 与 FileCategory 取治理后的描述与分类，且只保留当前版本；
      权限过滤用 allowed_owner_ids（数据范围）与 resource_grant_ids（资源级授权）取并集；
      文件类额外排除已删除（deleted_at 非空或 status=deleted），其它类型排除 status=deleted。
    返回的 select 会被各函数包装成子查询，列名必须保持一致。
    """
    owners = allowed_owner_ids(ctx, spec.permission)
    model = spec.model
    description = getattr(model, spec.description_attr) if spec.description_attr else literal("")
    kind = getattr(model, spec.kind_attr) if spec.kind_attr else literal("")
    category = getattr(model, spec.category_attr) if spec.category_attr else literal("")
    updated = getattr(model, spec.updated_attr)
    if spec.key == "file":
        description = func.coalesce(FileMetadata.description, literal(""))
        category = func.coalesce(FileCategory.name, literal(""))
        updated = func.coalesce(FileMetadata.updated_at, File.created_at)
    statement = select(
        literal(spec.key).label("asset_type"),
        model.id.label("asset_id"),
        model.name.label("name"),
        cast(description, String).label("native_description"),
        cast(_status_expression(spec), String).label("native_status"),
        model.owner_user_id.label("owner_user_id"),
        model.created_at.label("created_at"),
        updated.label("updated_at"),
        cast(kind, String).label("native_kind"),
        cast(category, String).label("native_category"),
    ).select_from(model)
    if spec.key == "file":
        # Only current logical file revisions belong in the business catalogue.
        # The outer joins keep legacy files visible until file governance adopts them.
        statement = statement.outerjoin(FileMetadata, and_(
            FileMetadata.file_id == File.id,
            FileMetadata.workspace_id == ctx.workspace_id,
        )).outerjoin(FileCategory, and_(
            FileCategory.id == FileMetadata.category_id,
            FileCategory.workspace_id == ctx.workspace_id,
            FileCategory.status == "active",
        ))
    statement = statement.where(model.workspace_id == ctx.workspace_id)
    if owners is not None:
        domain, action = spec.permission.rsplit(".", 1)
        statement = statement.where(or_(
            model.owner_user_id.in_(owners), model.id.in_(resource_grant_ids(ctx, domain, action)),
        ))
    if spec.key == "file":
        statement = statement.where(
            File.deleted_at.is_(None), File.status != "deleted",
            or_(FileMetadata.id.is_(None), FileMetadata.is_current.is_(True)),
        )
    elif spec.status_attr:
        statement = statement.where(getattr(model, spec.status_attr) != "deleted")
    return statement


def _available_specs(ctx: AuthContext) -> list[ResourceSpec]:
    """按当前账号权限过滤出可查看的资源类型列表（权限点不在 policy 里的直接不出现）。"""
    policy = access_policy(ctx)
    return [item for item in RESOURCE_SPECS if item.permission in policy.permissions]


def list_types(ctx: AuthContext) -> list[dict]:
    """Return only source types the actor can actually view, with real scoped counts."""
    # 列出当前账号可查看的资源类型及其在权限范围内的真实数量。
    # 用途：资产目录左侧的类型导航；数量是逐类型 count，因此类型多时会有多条 count 查询。
    # 副作用：只读。权限：assets.view。
    #
    require_permission(ctx, "assets.view")
    result = []
    for spec in _available_specs(ctx):
        count = ctx.db.scalar(select(func.count()).select_from(_branch(ctx, spec).subquery())) or 0
        result.append({"type": spec.key, "label": spec.label, "sourceModule": spec.source_module, "count": count})
    return result


def _annotation_view(row: AssetAnnotation | None) -> dict | None:
    """标注行的对外序列化；无标注时返回 None（前端据此显示"未维护"）。"""
    if row is None:
        return None
    return {
        "id": row.id,
        "aliases": row.aliases,
        "category": row.category,
        "businessDomain": row.business_domain,
        "description": row.description,
        "tags": row.tags,
        "stewardUserId": row.steward_user_id,
        "sensitivity": row.sensitivity,
        "freshnessSeconds": row.freshness_seconds,
        "fieldDescriptions": row.field_descriptions,
        "status": row.status,
        "version": row.version,
        "updatedAt": _iso(row.updated_at),
    }


def _freshness(updated_at: datetime | None, seconds: int | None) -> dict:
    """计算数据新鲜度：返回 {state, ageSeconds, thresholdSeconds, observedAt}。
    state 取值：not_configured（未设置阈值）、unknown（没有更新时间）、current（未超阈值）、stale（已过期）。
    时间统一按 naive UTC 比较，并对带时区的历史值做兜底转换；age 取非负，避免时钟回拨出现负数。
    """
    if not seconds:
        return {"state": "not_configured", "ageSeconds": None, "thresholdSeconds": None, "observedAt": _iso(updated_at)}
    if updated_at is None:
        return {"state": "unknown", "ageSeconds": None, "thresholdSeconds": seconds, "observedAt": None}
    current = datetime.utcnow()
    observed = updated_at.replace(tzinfo=None) if updated_at.tzinfo else updated_at
    age = max(0, int((current - observed).total_seconds()))
    return {"state": "current" if age <= seconds else "stale", "ageSeconds": age,
            "thresholdSeconds": seconds, "observedAt": _iso(updated_at)}


def _can_annotate(ctx: AuthContext, annotation: AssetAnnotation | None) -> bool:
    """判断当前账号能否编辑该标注（有 assets.edit 权限，且对该标注资源有资源级授权）。
    不抛异常，只返回布尔值，供视图下发 canAnnotate 给前端控制按钮可用性。
    """
    if "assets.edit" not in access_policy(ctx).permissions:
        return False
    if annotation is None:
        return True
    try:
        require_resource(ctx, "assets.edit", annotation)
        return True
    except PermissionErrorEx:
        return False


def _open_path(spec: ResourceSpec, asset_id: str) -> str:
    """把资源 ID 拼成前端的"打开"链接。多数类型是固定入口，报表/工作流/知识类需要带 id 参数。"""
    if spec.key == "report":
        return f"/report/designer/{asset_id}"
    if spec.key == "workflow":
        return f"/workflows/{asset_id}"
    if spec.key in {"ontology", "extraction", "graph", "bot"}:
        return f"{spec.open_path}?id={asset_id}"
    return spec.open_path


def _catalog_item(ctx: AuthContext, row: dict, annotation: AssetAnnotation | None, *, observed_at=None) -> dict:
    """把目录查询的一行、可选的标注与新鲜度组装成对外的资产条目。
    优先级：生效中的标注描述优先于资源自身的原生描述（业务语义覆盖技术描述）。
    """
    spec = _spec(row["asset_type"])
    annotation_data = _annotation_view(annotation)
    active_annotation = annotation_data if annotation and annotation.status == "active" else None
    description = active_annotation["description"] if active_annotation and active_annotation["description"] else row["native_description"]
    return {
        "type": spec.key,
        "typeLabel": spec.label,
        "id": row["asset_id"],
        "name": row["name"],
        "description": description or "",
        "nativeDescription": row["native_description"] or "",
        "status": row["native_status"] or "unknown",
        "kind": row["native_kind"] or "",
        "ownerUserId": row["owner_user_id"],
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
        "annotation": annotation_data,
        "category": (active_annotation["category"] if active_annotation else "") or row.get("native_category", "") or "",
        "businessDomain": active_annotation["businessDomain"] if active_annotation else "",
        "aliases": active_annotation["aliases"] if active_annotation else [],
        "tags": active_annotation["tags"] if active_annotation else [],
        "sensitivity": active_annotation["sensitivity"] if active_annotation else "internal",
        "freshness": _freshness(observed_at or row["updated_at"], active_annotation["freshnessSeconds"] if active_annotation else None),
        "source": {"module": spec.source_module, "recordType": spec.key, "kind": row["native_kind"] or ""},
        "openPath": _open_path(spec, row["asset_id"]),
        "authorizedActions": ["view"] + (["annotate"] if _can_annotate(ctx, annotation) else []),
    }


def list_catalog(
    ctx: AuthContext,
    *,
    keyword: str = "",
    asset_type: str = "",
    category: str = "",
    business_domain: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Merge visible rows across modules; the query never contains unauthorized branches."""
    require_permission(ctx, "assets.view")
    specs = _available_specs(ctx)
    if asset_type:
        _spec(asset_type)
        specs = [item for item in specs if item.key == asset_type]
    if not specs:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    catalog = union_all(*[_branch(ctx, item) for item in specs]).subquery("authorized_catalog")
    join_condition = and_(
        AssetAnnotation.workspace_id == ctx.workspace_id,
        AssetAnnotation.resource_type == catalog.c.asset_type,
        AssetAnnotation.resource_id == catalog.c.asset_id,
        AssetAnnotation.status == "active",
    )
    statement = select(
        catalog,
        AssetAnnotation.id.label("annotation_id"),
        AssetAnnotation.owner_user_id.label("annotation_owner_user_id"),
        AssetAnnotation.aliases.label("annotation_aliases"),
        AssetAnnotation.category.label("annotation_category"),
        AssetAnnotation.business_domain.label("annotation_business_domain"),
        AssetAnnotation.description.label("annotation_description"),
        AssetAnnotation.tags.label("annotation_tags"),
        AssetAnnotation.steward_user_id.label("annotation_steward_user_id"),
        AssetAnnotation.sensitivity.label("annotation_sensitivity"),
        AssetAnnotation.freshness_seconds.label("annotation_freshness_seconds"),
        AssetAnnotation.field_descriptions.label("annotation_field_descriptions"),
        AssetAnnotation.search_text.label("annotation_search_text"),
        AssetAnnotation.status.label("annotation_status"),
        AssetAnnotation.version.label("annotation_version"),
        AssetAnnotation.updated_at.label("annotation_updated_at"),
    ).outerjoin(AssetAnnotation, join_condition)
    if keyword:
        needle = keyword.strip().lower()
        statement = statement.where(or_(
            func.lower(catalog.c.name).contains(needle),
            func.lower(catalog.c.native_description).contains(needle),
            func.lower(func.coalesce(AssetAnnotation.search_text, "")).contains(needle),
        ))
    if category:
        statement = statement.where(
            func.coalesce(func.nullif(AssetAnnotation.category, ""), catalog.c.native_category) == category,
        )
    if business_domain:
        statement = statement.where(AssetAnnotation.business_domain == business_domain)
    if status:
        statement = statement.where(catalog.c.native_status == status)
    total = ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    rows = ctx.db.execute(statement.order_by(catalog.c.updated_at.desc(), catalog.c.name.asc()).limit(limit).offset(offset)).mappings().all()
    datasource_ids = [row["asset_id"] for row in rows if row["asset_type"] == "datasource"]
    metadata_times = {}
    if datasource_ids:
        metadata_times = {item.datasource_id: item.updated_at for item in ctx.db.scalars(select(SourceMetadata).where(
            SourceMetadata.workspace_id == ctx.workspace_id, SourceMetadata.datasource_id.in_(datasource_ids),
            SourceMetadata.status == "ready",
        )).all()}
    items = []
    for row in rows:
        data = dict(row)
        annotation = None
        if data["annotation_id"]:
            annotation = AssetAnnotation(
                id=data["annotation_id"], workspace_id=ctx.workspace_id,
                owner_user_id=data["annotation_owner_user_id"], resource_type=data["asset_type"],
                resource_id=data["asset_id"], aliases=data["annotation_aliases"] or [],
                category=data["annotation_category"] or "", business_domain=data["annotation_business_domain"] or "",
                description=data["annotation_description"] or "", tags=data["annotation_tags"] or [],
                steward_user_id=data["annotation_steward_user_id"], sensitivity=data["annotation_sensitivity"] or "internal",
                freshness_seconds=data["annotation_freshness_seconds"],
                field_descriptions=data["annotation_field_descriptions"] or {}, status=data["annotation_status"],
                version=data["annotation_version"], updated_at=data["annotation_updated_at"],
            )
        items.append(_catalog_item(ctx, data, annotation, observed_at=metadata_times.get(data["asset_id"])))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _resource_active(spec: ResourceSpec, row) -> None:
    """校验资源未被删除。异常：NotFoundError。"""
    if spec.key == "file" and (row.deleted_at is not None or row.status == "deleted"):
        raise NotFoundError("资源不存在或已移除")
    if spec.status_attr and getattr(row, spec.status_attr, "") == "deleted":
        raise NotFoundError("资源不存在或已移除")


def _resolve_resource(ctx: AuthContext, asset_type: str, asset_id: str):
    """按类型取资源行并做工作区归属校验。异常：NotFoundError。"""
    require_permission(ctx, "assets.view")
    spec = _spec(asset_type)
    if spec.permission not in access_policy(ctx).permissions:
        raise NotFoundError("资源不存在或不在当前数据范围内")
    row = owned_record(ctx, spec.model, asset_id, spec.permission)
    _resource_active(spec, row)
    return spec, row


def _row_data(ctx: AuthContext, spec: ResourceSpec, row) -> dict:
    """把资源行转成用户期望看到的数据（名称/归属/时间/来源模块/打开路径等），供详情页使用。"""
    native_description = getattr(row, spec.description_attr, "") if spec.description_attr else ""
    native_category = getattr(row, spec.category_attr, "") if spec.category_attr else ""
    updated_at = getattr(row, spec.updated_attr)
    if spec.key == "file":
        file_metadata = ctx.db.scalar(select(FileMetadata).where(
            FileMetadata.workspace_id == ctx.workspace_id,
            FileMetadata.file_id == row.id,
        ))
        if file_metadata:
            native_description = file_metadata.description
            updated_at = file_metadata.updated_at or row.created_at
            category = ctx.db.scalar(select(FileCategory).where(
                FileCategory.workspace_id == ctx.workspace_id,
                FileCategory.id == file_metadata.category_id,
                FileCategory.status == "active",
            )) if file_metadata.category_id else None
            native_category = category.name if category else ""
    return {
        "asset_type": spec.key,
        "asset_id": row.id,
        "name": row.name,
        "native_description": native_description,
        "native_status": ("active" if row.enabled else "disabled") if spec.key == "report_template" else getattr(row, spec.status_attr, "active"),
        "owner_user_id": row.owner_user_id,
        "created_at": row.created_at,
        "updated_at": updated_at,
        "native_kind": getattr(row, spec.kind_attr, "") if spec.kind_attr else "",
        "native_category": native_category,
    }


def _count(ctx: AuthContext, model, *conditions) -> int:
    """按给定条件统计模型行数的小工具（详情页统计关联数量）。"""
    return ctx.db.scalar(select(func.count(model.id)).where(*conditions)) or 0


def _safe_details(ctx: AuthContext, spec: ResourceSpec, row, annotation: AssetAnnotation | None) -> tuple[dict, list[dict], dict]:
    """组装资产详情的扩展信息（关联数量、最近活动等），并做权限与脱敏处理。
    返回 (详情补充, 关联项列表, 额外统计)。
    """
    details: dict = {}
    fields: list[dict] = []
    metadata_info = {"status": "not_applicable", "updatedAt": None, "truncated": False}
    if spec.key == "datasource":
        snapshot = ctx.db.scalar(select(SourceMetadata).where(
            SourceMetadata.workspace_id == ctx.workspace_id, SourceMetadata.datasource_id == row.id,
        ))
        details = {"provider": row.provider, "metadataAvailable": bool(snapshot and snapshot.status == "ready")}
        if snapshot:
            metadata_info = {"status": snapshot.status, "updatedAt": _iso(snapshot.updated_at),
                             "sourceVersion": snapshot.source_version, "error": snapshot.error_message, "truncated": False}
            descriptions = annotation.field_descriptions if annotation and annotation.status == "active" else {}
            for table in snapshot.tables_json if snapshot.status == "ready" else []:
                for column in table.get("columns") or []:
                    path = f"{table.get('tableName', '')}.{column.get('columnName', '')}"
                    fields.append({
                        "path": path,
                        "table": table.get("tableName", ""),
                        "name": column.get("columnName", ""),
                        "type": column.get("dataType", ""),
                        "nullable": bool(column.get("nullable", True)),
                        "primaryKey": bool(column.get("primaryKey", False)),
                        "description": descriptions.get(path) or column.get("comment") or "",
                        "nativeDescription": column.get("comment") or "",
                    })
                    if len(fields) >= 5000:
                        metadata_info["truncated"] = True
                        break
                if metadata_info["truncated"]:
                    break
            details["tableCount"] = len(snapshot.tables_json) if snapshot.status == "ready" else 0
            details["fieldCount"] = sum(len(table.get("columns") or []) for table in snapshot.tables_json) if snapshot.status == "ready" else 0
    elif spec.key == "file":
        details = {"mimeType": row.mime_type, "size": row.size, "sourceType": row.source_type}
    elif spec.key == "artifact":
        details = {"artifactType": row.type, "mimeType": row.mime_type, "size": row.size, "status": row.status}
    elif spec.key == "knowledge_base":
        from app.models import KnowledgeDocument
        details = {"visibility": row.visibility, "documentCount": _count(
            ctx, KnowledgeDocument, KnowledgeDocument.workspace_id == ctx.workspace_id,
            KnowledgeDocument.knowledge_base_id == row.id,
        )}
    elif spec.key == "report":
        details = {"canvas": f"{row.width} × {row.height}", "refreshSeconds": row.refresh_seconds,
                   "templateMode": row.template_mode, "componentCount": len((row.content or {}).get("components") or []),
                   "dataSourceCount": len(row.datasource_ids or [])}
    elif spec.key == "report_template":
        details = {"category": row.category, "enabled": row.enabled, "tags": row.tags}
    elif spec.key == "prompt":
        details = {"mode": row.mode, "usageCount": row.usage_count, "builtin": row.builtin}
    elif spec.key == "query_template":
        details = {"category": row.category, "mode": row.query_mode, "usageCount": row.usage_count}
    elif spec.key == "workflow":
        details = {"domains": row.domains, "nodeCount": len((row.graph or {}).get("nodes") or []),
                   "publishedRevision": row.published_revision}
    elif spec.key == "device":
        details = {"code": row.code, "category": row.category, "model": row.model, "manufacturer": row.manufacturer,
                   "location": row.location, "protocol": row.protocol, "domains": row.domains,
                   "hasDataSource": bool(row.datasource_id)}
    elif spec.key == "alert_rule":
        details = {"ruleCount": len(row.rules_json or []), "hasAdvice": bool(row.advice_id)}
    elif spec.key == "quality_metric":
        details = {"category": row.category, "unit": row.unit, "standardMin": row.standard_min,
                   "standardMax": row.standard_max}
    elif spec.key == "quality_template":
        details = {"deviceType": row.device_type, "metricCount": len(row.metric_ids or [])}
    elif spec.key == "ontology":
        details = {"publishedRevision": row.published_revision,
                   "conceptCount": _count(ctx, OntologyConcept, OntologyConcept.ontology_id == row.id, OntologyConcept.status != "deleted"),
                   "relationTypeCount": _count(ctx, OntologyRelationType, OntologyRelationType.ontology_id == row.id, OntologyRelationType.status != "deleted")}
    elif spec.key == "extraction":
        details = {"mode": row.mode, "ontologyRevision": row.ontology_revision,
                   "executionCount": row.execution_count, "hasTargetGraph": bool(row.target_graph_id)}
    elif spec.key == "graph":
        details = {"ontologyRevision": row.ontology_revision, "publishedRevision": row.published_revision,
                   "entityCount": _count(ctx, GraphEntity, GraphEntity.graph_id == row.id, GraphEntity.status != "deleted"),
                   "relationCount": _count(ctx, GraphRelation, GraphRelation.graph_id == row.id, GraphRelation.status != "deleted")}
    elif spec.key == "bot":
        details = {"botType": row.bot_type, "publishedRevision": row.published_revision, "apiEnabled": row.api_enabled,
                   "bindingCount": sum(len(value or []) for value in (
                       row.knowledge_base_ids, row.graph_ids, row.workflow_ids, row.skill_ids, row.tool_ids,
                   ))}
    elif spec.key in {"skill", "plugin"}:
        details = {"published": bool(row.published_version_id), "visibility": row.visibility}
    elif spec.key == "connector":
        details = {"provider": row.provider, "published": bool(row.published_version_id), "visibility": row.visibility}
    return details, fields, metadata_info


def get_asset(ctx: AuthContext, asset_type: str, asset_id: str) -> dict:
    """取单个资产的详情（含标注、新鲜度、关联与可编辑性）。异常：NotFoundError、权限异常。"""
    spec, row = _resolve_resource(ctx, asset_type, asset_id)
    annotation = ctx.db.scalar(select(AssetAnnotation).where(
        AssetAnnotation.workspace_id == ctx.workspace_id,
        AssetAnnotation.resource_type == asset_type,
        AssetAnnotation.resource_id == asset_id,
    ))
    details, fields, metadata_info = _safe_details(ctx, spec, row, annotation)
    observed_at = None
    if spec.key == "datasource" and metadata_info["updatedAt"]:
        observed_at = datetime.fromisoformat(metadata_info["updatedAt"])
    result = _catalog_item(ctx, _row_data(ctx, spec, row), annotation, observed_at=observed_at)
    result.update({"details": details, "fields": fields, "metadata": metadata_info})
    return result


def save_annotation(ctx: AuthContext, asset_type: str, asset_id: str, payload: AnnotationInput) -> dict:
    """新建或更新资产标注（别名/分类/业务域/描述/标签/负责人/敏感级/字段说明）。
    要点：需要 assets.edit 权限；已有标注时走乐观锁；只允许对自己的标注或已授权资源操作。
    异常：NotFoundError、PermissionErrorEx、ConflictError、ValidationError。
    """
    _resolve_resource(ctx, asset_type, asset_id)
    require_permission(ctx, "assets.edit")
    row = ctx.db.scalar(select(AssetAnnotation).where(
        AssetAnnotation.workspace_id == ctx.workspace_id,
        AssetAnnotation.resource_type == asset_type,
        AssetAnnotation.resource_id == asset_id,
    ))
    if row:
        require_resource(ctx, "assets.edit", row)
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
        row = AssetAnnotation(
            workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
            resource_type=asset_type, resource_id=asset_id,
        )
        ctx.db.add(row)
    if payload.steward_user_id:
        member = ctx.db.scalar(select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == ctx.workspace_id,
            WorkspaceMember.user_id == payload.steward_user_id,
        ))
        if member is None:
            raise ValidationError("数据责任人不是当前工作区成员")
    row.aliases = payload.aliases
    row.category = payload.category.strip()
    row.business_domain = payload.business_domain.strip()
    row.description = payload.description.strip()
    row.tags = payload.tags
    row.steward_user_id = payload.steward_user_id
    row.sensitivity = payload.sensitivity
    row.freshness_seconds = payload.freshness_seconds
    row.field_descriptions = payload.field_descriptions
    row.status = payload.status
    row.search_text = " ".join([
        *payload.aliases, *payload.tags, row.category, row.business_domain, row.description,
        *payload.field_descriptions.keys(), *payload.field_descriptions.values(),
    ]).lower()[:100000]
    _commit(ctx, "asset.annotation.save", "assets", row.id)
    ctx.db.refresh(row)
    return get_asset(ctx, asset_type, asset_id)


def remove_annotation(ctx: AuthContext, asset_type: str, asset_id: str, version: int) -> None:
    """归档资产标注（软删除，保留历史用于审计）。异常：NotFoundError、ConflictError。"""
    _resolve_resource(ctx, asset_type, asset_id)
    row = ctx.db.scalar(select(AssetAnnotation).where(
        AssetAnnotation.workspace_id == ctx.workspace_id,
        AssetAnnotation.resource_type == asset_type,
        AssetAnnotation.resource_id == asset_id,
    ))
    if not row:
        raise NotFoundError("该资源尚未登记目录信息")
    require_resource(ctx, "assets.delete", row)
    _version(row, version)
    row.status = "archived"
    row.search_text = ""
    _commit(ctx, "asset.annotation.archive", "assets", row.id)


def _term_view(row: BusinessTerm) -> dict:
    """业务术语行的对外序列化。"""
    return {"id": row.id, "code": row.code, "name": row.name, "definition": row.definition,
            "aliases": row.aliases, "businessDomain": row.business_domain, "tags": row.tags,
            "status": row.status, "version": row.version, "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at)}


def list_terms(ctx: AuthContext, *, keyword: str = "", status: str = "", limit: int = 100, offset: int = 0) -> dict:
    """分页列出业务术语（keyword/status 筛选）。"""
    statement = visible_records(ctx, BusinessTerm, "assets.view")
    if keyword:
        needle = keyword.strip().lower()
        statement = statement.where(or_(
            func.lower(BusinessTerm.code).contains(needle), func.lower(BusinessTerm.name).contains(needle),
            func.lower(BusinessTerm.definition).contains(needle), func.lower(BusinessTerm.business_domain).contains(needle),
            func.lower(cast(BusinessTerm.aliases, String)).contains(needle),
        ))
    if status:
        statement = statement.where(BusinessTerm.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    rows = ctx.db.scalars(statement.order_by(BusinessTerm.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [_term_view(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def save_term(ctx: AuthContext, payload: BusinessTermInput, term_id: str | None = None) -> dict:
    """新建或更新业务术语（名称/定义/同义词/所属域等）。
    要点：术语名唯一；更新走乐观锁。异常：ValidationError、ConflictError。
    """
    permission = "assets.edit" if term_id else "assets.create"
    if term_id:
        row = owned_record(ctx, BusinessTerm, term_id, permission)
        _version(row, payload.version)
    else:
        require_permission(ctx, permission)
        _version(None, payload.version, creating=True)
        row = BusinessTerm(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.code = payload.code.strip()
    row.name = payload.name.strip()
    row.definition = payload.definition.strip()
    row.aliases = payload.aliases
    row.business_domain = payload.business_domain.strip()
    row.tags = payload.tags
    row.status = payload.status
    _commit(ctx, "asset.term.save", "assets", row.id)
    ctx.db.refresh(row)
    return _term_view(row)


def remove_term(ctx: AuthContext, term_id: str, version: int) -> None:
    """归档业务术语。异常：NotFoundError、ConflictError。"""
    row = owned_record(ctx, BusinessTerm, term_id, "assets.delete")
    _version(row, version)
    row.status = "archived"
    _commit(ctx, "asset.term.archive", "assets", row.id)


def _visible_ref(ctx: AuthContext, value: dict) -> dict | None:
    """把指标来源引用转成当前账号可见的形式；不可见的返回 None（详情里显示为已失效引用）。"""
    try:
        spec, row = _resolve_resource(ctx, value["type"], value["id"])
    except (NotFoundError, PermissionErrorEx):
        return None
    return {"type": spec.key, "typeLabel": spec.label, "id": row.id, "name": row.name,
            "field": value.get("field") or "", "openPath": _open_path(spec, row.id)}


def _metric_view(ctx: AuthContext, row: MetricDefinition) -> dict:
    """指标定义行的对外序列化（含口径、来源、维度等）。"""
    refs = [result for value in row.source_refs if (result := _visible_ref(ctx, value))]
    return {"id": row.id, "code": row.code, "name": row.name, "description": row.description,
            "formula": row.formula, "unit": row.unit, "aggregation": row.aggregation,
            "granularity": row.granularity, "dimensions": row.dimensions, "sourceRefs": refs,
            "restrictedSourceCount": len(row.source_refs) - len(refs), "tags": row.tags,
            "status": row.status, "version": row.version, "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at)}


def list_metrics(ctx: AuthContext, *, keyword: str = "", status: str = "", limit: int = 100, offset: int = 0) -> dict:
    """分页列出指标定义（keyword/status 筛选）。"""
    statement = visible_records(ctx, MetricDefinition, "assets.view")
    if keyword:
        needle = keyword.strip().lower()
        statement = statement.where(or_(
            func.lower(MetricDefinition.code).contains(needle), func.lower(MetricDefinition.name).contains(needle),
            func.lower(MetricDefinition.description).contains(needle), func.lower(MetricDefinition.formula).contains(needle),
        ))
    if status:
        statement = statement.where(MetricDefinition.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    rows = ctx.db.scalars(statement.order_by(MetricDefinition.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [_metric_view(ctx, row) for row in rows], "total": total, "limit": limit, "offset": offset}


def get_metric(ctx: AuthContext, metric_id: str, permission: str = "assets.view") -> MetricDefinition:
    """取指标定义并做归属/权限校验。异常：NotFoundError、PermissionErrorEx。"""
    return owned_record(ctx, MetricDefinition, metric_id, permission)


def save_metric(ctx: AuthContext, payload: MetricDefinitionInput, metric_id: str | None = None) -> dict:
    """新建或更新指标定义（口径、单位、维度、来源引用）。
    要点：来源引用会逐个做可见性校验；名称唯一；更新走乐观锁。异常：ValidationError、ConflictError。
    """
    permission = "assets.edit" if metric_id else "assets.create"
    if metric_id:
        row = get_metric(ctx, metric_id, permission)
        _version(row, payload.version)
    else:
        require_permission(ctx, permission)
        _version(None, payload.version, creating=True)
        row = MetricDefinition(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    sources = [item.model_dump() for item in payload.source_refs]
    for value in sources:
        _resolve_resource(ctx, value["type"], value["id"])
    row.code = payload.code.strip()
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.formula = payload.formula.strip()
    row.unit = payload.unit.strip()
    row.aggregation = payload.aggregation
    row.granularity = payload.granularity.strip()
    row.dimensions = payload.dimensions
    row.source_refs = sources
    row.tags = payload.tags
    row.status = payload.status
    _commit(ctx, "asset.metric.save", "assets", row.id)
    ctx.db.refresh(row)
    return _metric_view(ctx, row)


def remove_metric(ctx: AuthContext, metric_id: str, version: int) -> None:
    """归档指标定义。异常：NotFoundError、ConflictError。"""
    row = get_metric(ctx, metric_id, "assets.delete")
    _version(row, version)
    row.status = "archived"
    _commit(ctx, "asset.metric.archive", "assets", row.id)


def _relation_view(ctx: AuthContext, row: AssetRelation) -> dict | None:
    """资产关系行的对外序列化；两端资源当前不可见时返回 None（该关系不展示）。"""
    source = _visible_ref(ctx, {"type": row.source_type, "id": row.source_id})
    target = _visible_ref(ctx, {"type": row.target_type, "id": row.target_id})
    if not source or not target:
        return None
    return {"id": row.id, "source": source, "target": target, "relationKind": row.relation_kind,
            "description": row.description, "status": row.status, "version": row.version,
            "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at)}


def list_relations(ctx: AuthContext, *, status: str = "active", limit: int = 100, offset: int = 0) -> dict:
    """分页列出资产关系（默认只看 active）。
    要点：会过滤掉任一端已不可见的关系，避免目录泄露无权访问的资产名称。
    """
    statement = visible_records(ctx, AssetRelation, "assets.view")
    if status:
        statement = statement.where(AssetRelation.status == status)
    # Both endpoints must be visible before counting or slicing. Paginating
    # first and filtering afterwards would skip accessible edges and leak totals.
    source_conditions, target_conditions = [], []
    for spec in _available_specs(ctx):
        model = spec.model
        visible_ids = visible_records(ctx, model, spec.permission).with_only_columns(model.id)
        if spec.key == "file":
            visible_ids = visible_ids.where(model.deleted_at.is_(None))
        if spec.status_attr:
            visible_ids = visible_ids.where(getattr(model, spec.status_attr) != "deleted")
        source_conditions.append(and_(AssetRelation.source_type == spec.key, AssetRelation.source_id.in_(visible_ids)))
        target_conditions.append(and_(AssetRelation.target_type == spec.key, AssetRelation.target_id.in_(visible_ids)))
    if not source_conditions:
        return {"items": [], "total": 0, "limit": limit, "offset": 0}
    statement = statement.where(or_(*source_conditions), or_(*target_conditions))
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = ctx.db.scalars(statement.order_by(AssetRelation.updated_at.desc(), AssetRelation.id).limit(limit).offset(offset)).all()
    items = [result for row in rows if (result := _relation_view(ctx, row))]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def save_relation(ctx: AuthContext, payload: AssetRelationInput, relation_id: str | None = None) -> dict:
    """新建或更新资产关系（源资产→目标资产 + 关系类型）。
    要点：禁止自环；两端资源必须存在且可见；同类型关系不重复。异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "assets.edit" if relation_id else "assets.create"
    if relation_id:
        row = owned_record(ctx, AssetRelation, relation_id, permission)
        _version(row, payload.version)
    else:
        require_permission(ctx, permission)
        _version(None, payload.version, creating=True)
        row = AssetRelation(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    _resolve_resource(ctx, payload.source.type, payload.source.id)
    _resolve_resource(ctx, payload.target.type, payload.target.id)
    row.source_type = payload.source.type
    row.source_id = payload.source.id
    row.target_type = payload.target.type
    row.target_id = payload.target.id
    row.relation_kind = payload.relation_kind
    row.description = payload.description.strip()
    row.status = payload.status
    _commit(ctx, "asset.relation.save", "assets", row.id)
    ctx.db.refresh(row)
    return _relation_view(ctx, row)


def remove_relation(ctx: AuthContext, relation_id: str, version: int) -> None:
    """归档资产关系。异常：NotFoundError、ConflictError。"""
    row = owned_record(ctx, AssetRelation, relation_id, "assets.delete")
    _version(row, version)
    row.status = "archived"
    _commit(ctx, "asset.relation.archive", "assets", row.id)
