"""Application lifecycle, resource authorization, publication and launch tracing."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import datetime
from typing import Callable
from urllib.parse import quote

from sqlalchemy import select

from app.common.authorization import access_policy, require_permission, require_resource
from app.common.deps import AuthContext
from app.common.idempotency import claim_idempotency, finish_idempotency
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.ids import new_id
from app.models import AuthIdentity, DataSource, KnowledgeBase, Plugin, Run, Skill, User, WorkspaceMember
from app.modules.documents.models import DocumentGeneration
from app.modules.knowledge_graph.models import BusinessBot
from app.modules.reports.models import Report
from app.modules.runtime import service as runtime
from app.modules.system.models import Role
from app.modules.system.service_helpers import check_version, commit_change
from app.modules.workflows.models import Workflow, WorkflowRevision
from app.providers.tool.validator import validate_arguments

from .models import ApplicationLaunch, ApplicationVersion, BusinessApplication
from .schemas import (
    ApplicationBinding,
    ApplicationInput,
    ApplicationSpec,
    CloneInput,
    LaunchInput,
)


def _now() -> datetime:
    """统一取当前 UTC naive 时间（库里所有时间列都按 naive UTC 存，保持一致）。"""
    return datetime.utcnow()


@dataclass(frozen=True)
class ResourceAdapter:
    """一类可绑定资源的适配描述（不可变）：模型类、查看/启动所需权限点、中文标签、
    就绪判据 ready() 与前端跳转路径 path()。新增可绑定资源类型时，只需在这里加一项并补测试。
    """
    model: type
    permission: str
    launch_permission: str
    label: str
    ready: Callable[[object], bool]
    path: Callable[[object], str]


RESOURCE_ADAPTERS: dict[str, ResourceAdapter] = {
    "bot": ResourceAdapter(
        BusinessBot, "bots.view", "bots.execute", "Bot 与智能体",
        lambda row: row.status == "published" and row.published_revision is not None,
        lambda row: f"/bots?id={quote(row.id)}",
    ),
    "workflow": ResourceAdapter(
        Workflow, "workflows.view", "workflows.execute", "工作流",
        lambda row: row.status == "published" and row.published_revision is not None,
        lambda row: f"/workflows/{quote(row.id)}",
    ),
    "dashboard": ResourceAdapter(
        Report, "dashboards.view", "dashboards.view", "报表与大屏",
        lambda row: row.status == "published",
        lambda row: f"/report/view/{quote(row.id)}",
    ),
    "plugin": ResourceAdapter(
        Plugin, "plugins.view", "plugins.execute", "Plugin",
        lambda row: row.status == "published" and bool(row.published_version_id),
        lambda row: f"/ai?plugin_id={quote(row.id)}",
    ),
    "skill": ResourceAdapter(
        Skill, "skills.view", "skills.view", "Skill",
        lambda row: row.status == "published" and bool(row.published_version_id),
        lambda row: f"/skills?id={quote(row.id)}",
    ),
    "knowledge": ResourceAdapter(
        KnowledgeBase, "knowledge.view", "knowledge.view", "知识库",
        lambda row: row.status == "active",
        lambda row: f"/knowledge?id={quote(row.id)}",
    ),
    "datasource": ResourceAdapter(
        DataSource, "datasources.view", "datasources.view", "数据源",
        lambda row: row.status == "active",
        lambda row: f"/datasources?id={quote(row.id)}",
    ),
    "document": ResourceAdapter(
        DocumentGeneration, "documents.view", "documents.view", "文档结果",
        lambda row: row.status == "completed" and bool(row.artifact_id),
        lambda row: f"/documents?tab=generations&id={quote(row.id)}",
    ),
}


def _latest_version(ctx: AuthContext, application_id: str) -> ApplicationVersion:
    """取应用的最新版本行（按 version_number 倒序）。异常：ValidationError（应用没有任何版本）。"""
    row = ctx.db.scalar(select(ApplicationVersion).where(
        ApplicationVersion.application_id == application_id,
    ).order_by(ApplicationVersion.version_number.desc()).limit(1))
    if row is None:
        raise ValidationError("应用没有可用版本")
    return row


def _published_version(ctx: AuthContext, row: BusinessApplication) -> ApplicationVersion:
    """取应用当前对外生效的正式版本；未发布或已退役时报错。
    异常：ValidationError（没有可启动的正式版本）。
    """
    version = ctx.db.get(ApplicationVersion, row.published_version_id) if row.published_version_id else None
    if version is None or version.application_id != row.id or version.status != "published":
        raise ValidationError("应用没有可启动的正式版本")
    return version


def _record(ctx: AuthContext, application_id: str, permission: str) -> BusinessApplication:
    """取应用并做三重校验：归属/权限（owned_record）→ 未归档 → 发布受众（仅 view/execute 时校验）。
    受众校验放在这里而不是各接口里，保证"不在受众内"的用户连详情都拿不到。
    异常：NotFoundError（不存在或已归档）、PermissionErrorEx（不在发布受众中）。
    """
    row = owned_record(ctx, BusinessApplication, application_id, permission)
    if row.status == "archived":
        raise NotFoundError("应用不存在或已归档")
    if permission in {"applications.view", "applications.execute"} and not _audience_allows(ctx, row):
        raise PermissionErrorEx("当前账号不在此应用的发布受众中")
    return row


def _audience_allows(ctx: AuthContext, row: BusinessApplication, spec: ApplicationSpec | None = None) -> bool:
    """判断当前账号是否在应用的发布受众内。
    短路顺序：管理员或所有者直接放行 → visibility=workspace 放行 → visibility=private 拒绝 →
    否则按发布版本的 audience（用户 id 集合 / 角色 id 交集）判定；无法读到版本时按拒绝处理。
    """
    policy = access_policy(ctx)
    if policy.administrator or row.owner_user_id == ctx.user_id:
        return True
    if row.visibility == "workspace":
        return True
    if row.visibility == "private":
        return False
    if spec is None:
        try:
            version = _published_version(ctx, row)
            spec = ApplicationSpec.model_validate(version.spec_json)
        except (ValidationError, ValueError):
            return False
    audience = spec.audience
    return ctx.user_id in audience.user_ids or bool({item.id for item in policy.roles} & set(audience.role_ids))


def _resource(ctx: AuthContext, binding: ApplicationBinding, permission: str | None = None):
    """按绑定类型取资源行并校验：工作区一致 + 资源级授权（require_resource）。
    权限取调用方显式传入的 permission，未传则用适配器默认的查看权限。
    异常：NotFoundError（资源不存在或跨工作区）、权限异常。
    """
    adapter = RESOURCE_ADAPTERS[binding.type]
    row = ctx.db.get(adapter.model, binding.id)
    if row is None or getattr(row, "workspace_id", None) != ctx.workspace_id:
        raise NotFoundError("绑定资源不存在或不属于当前工作区", resourceType=binding.type)
    require_resource(ctx, permission or adapter.permission, row)
    return row


def _resource_snapshot(binding: ApplicationBinding, row, adapter: ResourceAdapter) -> dict:
    """把绑定资源整理成启动/发布时冻结的快照（类型、名称、角色、就绪状态、跳转路径）。
    快照会写进启动记录，因此即使资源后来改名或下线，历史启动仍能还原当时的能力集合。
    """
    return {
        "type": binding.type,
        "typeLabel": adapter.label,
        "id": row.id,
        "name": getattr(row, "name", binding.id),
        "role": binding.role,
        "purpose": binding.purpose,
        "required": binding.required,
        "status": getattr(row, "status", "active"),
        "ready": adapter.ready(row),
        **({"revision": row.published_revision} if binding.type == "workflow" else {}),
        "path": adapter.path(row),
        "permission": adapter.launch_permission if binding.role == "entry" else adapter.permission,
    }


def _validate_audience(ctx: AuthContext, spec: ApplicationSpec) -> None:
    """校验发布受众配置合法：角色/用户 id 不能为空或重复，且必须属于当前工作区。
    异常：ValidationError。
    """
    audience = spec.audience
    if audience.role_ids:
        require_permission(ctx, "system.roles.view")
        rows = ctx.db.scalars(select(Role).where(
            Role.workspace_id == ctx.workspace_id, Role.id.in_(audience.role_ids), Role.enabled.is_(True),
        )).all()
        if {row.id for row in rows} != set(audience.role_ids):
            raise NotFoundError("受众角色不存在或未启用")
    if audience.user_ids:
        require_permission(ctx, "system.users.view")
        rows = ctx.db.execute(select(User, WorkspaceMember).join(
            WorkspaceMember, WorkspaceMember.user_id == User.id,
        ).where(
            WorkspaceMember.workspace_id == ctx.workspace_id,
            User.id.in_(audience.user_ids), User.status == "active",
        )).all()
        if {user.id for user, _member in rows} != set(audience.user_ids):
            raise NotFoundError("受众用户不存在、未启用或不属于当前工作区")


def _validate_spec(ctx: AuthContext, spec: ApplicationSpec, *, for_publish: bool) -> list[dict]:
    """校验应用规格：逐项校验绑定资源（存在、权限、就绪），返回资源快照列表。
    参数 for_publish=True 表示这是发布流程，会额外把"必须已发布"的约束施加到工作流/Bot 等资源上；
    未就绪的必填资源直接报错，可选资源则记录为不可用而不阻断。
    异常：ValidationError、NotFoundError、权限异常。
    """
    _validate_audience(ctx, spec)
    snapshots = []
    for binding in spec.bindings:
        adapter = RESOURCE_ADAPTERS[binding.type]
        row = _resource(ctx, binding)
        snapshot = _resource_snapshot(binding, row, adapter)
        if for_publish and binding.required and not snapshot["ready"]:
            raise ValidationError(
                "正式发布只能绑定已发布或可用的必需资源",
                resourceType=binding.type, resourceId=binding.id,
            )
        snapshots.append(snapshot)
    return snapshots


def _workflow_release(ctx: AuthContext, workflow_id: str, spec: ApplicationSpec, version: ApplicationVersion):
    """取入参工作流的正式修订号，供发布时冻结进 spec。异常：ValidationError（未发布）。"""
    query = select(WorkflowRevision).where(
        WorkflowRevision.workspace_id == ctx.workspace_id, WorkflowRevision.workflow_id == workflow_id,
    )
    pinned = spec.workflow_revisions.get(workflow_id)
    if pinned is not None:
        return ctx.db.scalar(query.where(WorkflowRevision.revision == pinned))
    if version.published_at is None:
        return None
    return ctx.db.scalar(query.where(
        WorkflowRevision.reason == "发布版本", WorkflowRevision.created_at <= version.published_at,
    ).order_by(WorkflowRevision.revision.desc()).limit(1))


def _launch_schema(ctx: AuthContext, spec: ApplicationSpec, version: ApplicationVersion) -> dict:
    """Reuse the pinned start fields so application creators do not configure them twice."""
    entry = next((item for item in spec.bindings if item.role == "entry" and item.type == "workflow"), None)
    release = _workflow_release(ctx, entry.id, spec, version) if entry else None
    if release is None:
        return spec.input_schema
    start = next((node for node in release.snapshot.get("graph", {}).get("nodes", []) if node.get("kind") == "start"), {})
    properties, required = {}, []
    for variable in start.get("config", {}).get("variables", []):
        kind = variable.get("type", "text-input")
        item = {"title": variable["label"], "description": variable.get("hint", "")}
        mapped = {"number": "number", "checkbox": "boolean", "json_object": "object", "file": "object", "file-list": "array"}
        if kind != "json":
            item["type"] = mapped.get(kind, "string")
        if variable.get("default") is not None:
            item["default"] = variable["default"]
        if kind == "select":
            item["enum"] = variable.get("options", [])
        if variable.get("maxLength") and item.get("type") == "string":
            item["maxLength"] = variable["maxLength"]
        properties[variable["variable"]] = item
        if variable.get("required") and variable.get("default") is None:
            required.append(variable["variable"])
    return {**spec.input_schema, "type": "object",
            "properties": {**properties, **spec.input_schema.get("properties", {})},
            "required": sorted(set(required) | set(spec.input_schema.get("required", [])))}


def _safe_spec(ctx: AuthContext, row: BusinessApplication, version: ApplicationVersion) -> dict:
    """产出对外可见的规格副本：把受众/绑定的内部标识做脱敏，只保留前端渲染需要的字段。"""
    spec = ApplicationSpec.model_validate(version.spec_json)
    policy = access_policy(ctx)
    bindings = []
    write_nodes = []
    for binding in spec.bindings:
        adapter = RESOURCE_ADAPTERS[binding.type]
        try:
            resource = _resource(ctx, binding)
            snapshot = _resource_snapshot(binding, resource, adapter)
            if binding.type == "workflow" and version.published_at:
                release = _workflow_release(ctx, resource.id, spec, version)
                snapshot["revision"] = release.revision if release else None
                if release and binding.role == "entry":
                    write_nodes = [node.get("title") or node.get("id") for node in release.snapshot.get("graph", {}).get("nodes", [])
                                   if node.get("kind") == "output-table"]
            bindings.append(snapshot)
        except (NotFoundError, PermissionErrorEx):
            bindings.append({
                "type": binding.type, "typeLabel": adapter.label, "id": None,
                "name": "不可访问的资源", "role": binding.role, "purpose": binding.purpose,
                "required": binding.required, "status": "unavailable", "ready": False,
                "path": "", "permission": adapter.permission,
            })
    audience = {
        "mode": spec.audience.mode,
        "roleIds": spec.audience.role_ids if "system.roles.view" in policy.permissions else [],
        "userIds": spec.audience.user_ids if "system.users.view" in policy.permissions else [],
        "roleCount": len(spec.audience.role_ids), "userCount": len(spec.audience.user_ids),
    }
    return {
        "welcomeMessage": spec.welcome_message,
        "instructions": spec.instructions,
        "bindings": bindings,
        "audience": audience,
        "inputSchema": _launch_schema(ctx, spec, version) if any(item["role"] == "entry" and item["id"] for item in bindings) else spec.input_schema,
        "configuredInputSchema": spec.input_schema,
        "outputSchema": spec.output_schema,
        "writeNodes": write_nodes,
    }


def application_view(ctx: AuthContext, row: BusinessApplication, *, detail: bool = False) -> dict:
    """应用视图；detail=True 时才附带规格明细与发布版本信息。"""
    latest = _latest_version(ctx, row.id)
    published = ctx.db.get(ApplicationVersion, row.published_version_id) if row.published_version_id else None
    result = {
        "id": row.id, "code": row.code, "name": row.name, "description": row.description,
        "kind": row.kind, "visibility": row.visibility, "status": row.status,
        "recordVersion": row.version, "latestVersion": latest.version_number,
        "latestVersionStatus": latest.status,
        "publishedVersion": published.version_number if published else None,
        "launchCount": row.launch_count,
        "publishedAt": row.published_at.isoformat() if row.published_at else None,
        "lastLaunchedAt": row.last_launched_at.isoformat() if row.last_launched_at else None,
        "updateTime": row.updated_at.isoformat(),
        "launchable": row.status == "published" and bool(published) and _audience_allows(ctx, row),
    }
    if detail:
        versions = ctx.db.scalars(select(ApplicationVersion).where(
            ApplicationVersion.application_id == row.id,
        ).order_by(ApplicationVersion.version_number.desc())).all()
        result["latestSpec"] = _safe_spec(ctx, row, latest)
        result["publishedSpec"] = _safe_spec(ctx, row, published) if published else None
        result["versions"] = [{
            "id": item.id, "version": item.version_number, "status": item.status,
            "basedOnVersionId": item.based_on_version_id,
            "createTime": item.created_at.isoformat(),
            "publishTime": item.published_at.isoformat() if item.published_at else None,
        } for item in versions]
    return result


def list_applications(
    ctx: AuthContext, *, keyword: str = "", status: str = "", kind: str = "",
    limit: int = 100, offset: int = 0,
) -> dict:
    """列出当前账号可见的应用（keyword/kind/status 筛选 + 分页）。可见性含发布受众过滤。"""
    query = visible_records(ctx, BusinessApplication, "applications.view").where(
        BusinessApplication.status != "archived",
    )
    if keyword.strip():
        term = f"%{keyword.strip()}%"
        query = query.where((BusinessApplication.name.ilike(term)) | (BusinessApplication.code.ilike(term)))
    if status:
        query = query.where(BusinessApplication.status == status)
    if kind:
        query = query.where(BusinessApplication.kind == kind)
    rows = ctx.db.scalars(query.order_by(BusinessApplication.updated_at.desc()).limit(5000)).all()
    rows = [row for row in rows if _audience_allows(ctx, row)]
    total = len(rows)
    return {
        "items": [application_view(ctx, row) for row in rows[offset:offset + limit]],
        "total": total, "limit": limit, "offset": offset,
    }


def resource_catalog(ctx: AuthContext) -> dict:
    """返回当前账号可绑定的各类资源目录（只含可见且满足基本条件的资源）。"""
    require_permission(ctx, "applications.view")
    granted = access_policy(ctx).permissions
    groups = []
    for kind, adapter in RESOURCE_ADAPTERS.items():
        if adapter.permission not in granted:
            continue
        rows = ctx.db.scalars(visible_records(ctx, adapter.model, adapter.permission).limit(500)).all()
        items = [{
            "id": row.id, "name": getattr(row, "name", row.id),
            "status": getattr(row, "status", "active"), "ready": adapter.ready(row),
            "path": adapter.path(row),
        } for row in rows if getattr(row, "status", "active") not in {"archived", "deleted", "failed"}]
        items.sort(key=lambda item: item["name"])
        groups.append({"type": kind, "label": adapter.label, "items": items})
    roles = []
    users = []
    if "system.roles.view" in granted:
        roles = [{"id": row.id, "name": row.name} for row in ctx.db.scalars(select(Role).where(
            Role.workspace_id == ctx.workspace_id, Role.enabled.is_(True),
        ).order_by(Role.name)).all()]
    if "system.users.view" in granted:
        user_rows = ctx.db.execute(
            select(User, AuthIdentity.username_normalized).join(
                WorkspaceMember, WorkspaceMember.user_id == User.id,
            ).outerjoin(
                AuthIdentity,
                (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
            ).where(
                WorkspaceMember.workspace_id == ctx.workspace_id, User.status == "active",
            ).order_by(User.display_name),
        ).all()
        users = [{
            "id": user.id,
            "name": user.display_name,
            "username": username or user.display_name,
        } for user, username in user_rows]
    return {"groups": groups, "roles": roles, "users": users}


def create_application(ctx: AuthContext, payload: ApplicationInput) -> dict:
    """新建应用（同时创建第一版草稿）。
    校验：code 唯一、规格与绑定合法、受众合法。异常：ValidationError、ConflictError（code 重复）。
    副作用：写 business_applications 与 application_versions，并写审计。
    """
    require_permission(ctx, "applications.create")
    if payload.version is not None:
        raise ValidationError("创建应用时不能提交版本号")
    _validate_spec(ctx, payload.spec, for_publish=False)
    if ctx.db.scalar(select(BusinessApplication.id).where(
        BusinessApplication.workspace_id == ctx.workspace_id, BusinessApplication.code == payload.code,
    )):
        raise ConflictError("应用编码已存在")
    row = BusinessApplication(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        code=payload.code, name=payload.name, description=payload.description.strip(),
        kind=payload.kind, visibility=payload.visibility, status="draft",
        launch_count=0, created_at=_now(), updated_at=_now(), version=1,
    )
    ctx.db.add(row)
    ctx.db.flush()
    ctx.db.add(ApplicationVersion(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        application_id=row.id, version_number=1, status="draft",
        spec_json=payload.spec.model_dump(by_alias=True), created_at=_now(), updated_at=_now(), version=1,
    ))
    commit_change(ctx, "application.create", "applications", row.id, {"code": row.code})
    return application_view(ctx, row, detail=True)


def get_application(ctx: AuthContext, application_id: str) -> dict:
    """取应用详情。异常：NotFoundError（不存在/已归档）、PermissionErrorEx（不在受众内）。"""
    return application_view(ctx, _record(ctx, application_id, "applications.view"), detail=True)


def create_version(ctx: AuthContext, application_id: str, payload: ApplicationInput) -> dict:
    """在现有应用上新建一个草稿版本（不发布）。
    不变式：发布版本保持不动，直到 publish_application 显式切换。异常：NotFoundError、ConflictError。
    """
    row = _record(ctx, application_id, "applications.edit")
    if payload.version is None:
        raise ValidationError("修改应用必须提交当前记录版本")
    check_version(row, payload.version)
    _validate_spec(ctx, payload.spec, for_publish=False)
    conflict = ctx.db.scalar(select(BusinessApplication.id).where(
        BusinessApplication.workspace_id == ctx.workspace_id,
        BusinessApplication.code == payload.code, BusinessApplication.id != row.id,
    ))
    if conflict:
        raise ConflictError("应用编码已存在")
    latest = _latest_version(ctx, row.id)
    next_number = latest.version_number + 1
    ctx.db.add(ApplicationVersion(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        application_id=row.id, version_number=next_number, based_on_version_id=latest.id,
        status="draft", spec_json=payload.spec.model_dump(by_alias=True),
        created_at=_now(), updated_at=_now(), version=1,
    ))
    row.code = payload.code
    row.name = payload.name
    row.description = payload.description.strip()
    row.kind = payload.kind
    row.visibility = payload.visibility
    row.status = "published" if row.published_version_id else "draft"
    row.updated_at = _now()
    commit_change(ctx, "application.version.create", "applications", row.id, {"releaseVersion": next_number})
    return application_view(ctx, row, detail=True)


def publish_application(
    ctx: AuthContext, application_id: str, *, record_version: int, release_version: int,
) -> dict:
    """发布应用：把最新草稿置为正式版本，并退役上一个正式版本。
    发布校验顺序：记录乐观锁 → 必须发布"最新草稿"（避免发布到过时版本）→ 校验规格与资源就绪 →
      把工作流修订号回写进 spec（冻结绑定）→ 切状态、退役旧版本、记录发布时间。
    异常：ConflictError（记录版本冲突或不是最新草稿）、ValidationError（资源未就绪）。
    副作用：写应用与版本状态、写变更审计。
    """
    row = _record(ctx, application_id, "applications.publish")
    check_version(row, record_version)
    latest = _latest_version(ctx, row.id)
    if latest.version_number != release_version or latest.status != "draft":
        raise ConflictError("只能发布当前最新草稿", currentReleaseVersion=latest.version_number)
    spec = ApplicationSpec.model_validate(latest.spec_json)
    resources = _validate_spec(ctx, spec, for_publish=True)
    spec.workflow_revisions = {item["id"]: item["revision"] for item in resources
                               if item["type"] == "workflow" and item.get("revision")}
    latest.spec_json = spec.model_dump(by_alias=True)
    previous = ctx.db.get(ApplicationVersion, row.published_version_id) if row.published_version_id else None
    if previous and previous.id != latest.id:
        previous.status = "retired"
        previous.updated_at = _now()
    latest.status = "published"
    latest.published_at = _now()
    latest.updated_at = _now()
    row.published_version_id = latest.id
    row.status = "published"
    row.published_at = _now()
    row.updated_at = _now()
    commit_change(ctx, "application.publish", "applications", row.id, {
        "releaseVersion": release_version,
        "resources": [{"type": item["type"], "id": item["id"]} for item in resources],
    })
    return application_view(ctx, row, detail=True)


def disable_application(ctx: AuthContext, application_id: str, record_version: int) -> dict:
    """停用应用（不再可启动，但保留正式版本与历史启动记录）。
    异常：ValidationError（未发布应用不能停用）、ConflictError（版本冲突）。
    """
    row = _record(ctx, application_id, "applications.publish")
    check_version(row, record_version)
    if not row.published_version_id:
        raise ValidationError("未发布应用不能停用")
    row.status = "disabled"
    row.updated_at = _now()
    commit_change(ctx, "application.disable", "applications", row.id)
    return application_view(ctx, row, detail=True)


def archive_application(ctx: AuthContext, application_id: str, record_version: int) -> None:
    """归档应用（软删除语义，保留历史）。异常：NotFoundError、ConflictError。"""
    row = _record(ctx, application_id, "applications.delete")
    check_version(row, record_version)
    row.status = "archived"
    row.updated_at = _now()
    commit_change(ctx, "application.archive", "applications", row.id, {"retainedHistory": True})


def clone_application(ctx: AuthContext, application_id: str, payload: CloneInput) -> dict:
    """复制一个应用（复制最新版本的规格），返回新应用。
    要点：需要 applications.create 权限；名称留空时自动加" 副本"后缀。异常：NotFoundError、权限异常。
    """
    source = _record(ctx, application_id, "applications.view")
    require_permission(ctx, "applications.create")
    latest = _latest_version(ctx, source.id)
    spec = ApplicationSpec.model_validate(latest.spec_json)
    name = payload.name.strip() or source.name + " 副本"
    return create_application(ctx, ApplicationInput(
        code=payload.code, name=name, description=source.description,
        kind=source.kind, visibility=source.visibility, spec=spec,
    ))


def _launch_resources(ctx: AuthContext, spec: ApplicationSpec) -> list[dict]:
    """组装本次启动的资源快照。
    规则：入口角色资源用 launch_permission，其它用查看权限；必填资源不可用时直接报错，
    可选资源不可用时记成 status=unavailable 的占位项而不是失败——让应用仍能启动。
    异常：NotFoundError / 权限异常（仅当必填资源不可用时向上抛）。
    """
    snapshots = []
    for binding in spec.bindings:
        adapter = RESOURCE_ADAPTERS[binding.type]
        permission = adapter.launch_permission if binding.role == "entry" else adapter.permission
        try:
            row = _resource(ctx, binding, permission)
            snapshot = _resource_snapshot(binding, row, adapter)
            if binding.required and not snapshot["ready"]:
                raise ValidationError("应用所需资源当前不可用", resourceType=binding.type)
            snapshots.append(snapshot)
        except (NotFoundError, PermissionErrorEx) as exc:
            if binding.required:
                raise
            snapshots.append({
                "type": binding.type, "typeLabel": adapter.label, "id": None,
                "name": "可选资源不可用", "role": binding.role, "purpose": binding.purpose,
                "required": False, "status": "unavailable", "ready": False,
                "path": "", "permission": permission,
            })
    return snapshots


def _launch_request_hash(row: BusinessApplication, version: ApplicationVersion,
                         payload: LaunchInput, arguments: dict) -> str:
    """Build a stable identity from the immutable release and validated input."""
    # 用"不可变发布版本 + 已校验入参"生成稳定摘要，作为幂等判定的依据。
    # 把 channel/input/confirmWrites 一起纳入，保证"同键但参数不同"不会被误判为同一次请求。
    #
    body = {
        "applicationId": row.id,
        "applicationVersionId": version.id,
        "channel": payload.channel,
        "input": arguments,
        "confirmWrites": payload.confirm_writes,
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _existing_launch(ctx: AuthContext, row: BusinessApplication, version: ApplicationVersion,
                     idempotency_key: str | None, request_hash: str) -> ApplicationLaunch | None:
    """Find an earlier launch for an explicit retry key before creating a Run.

    The launch table was shipped before idempotency was introduced, so the
    server-owned manifest carries the key and digest without requiring a live
    schema migration.  Missing keys intentionally keep one-shot behavior.
    """
    # 按幂等键查找历史启动记录，命中则直接返回而不重复执行。
    # 要点：幂等键存在启动记录自带的清单字段里（该表早于幂等机制上线，未做 schema 迁移）；
    # 没有传键时返回 None，即保持一次性的旧行为。
    #
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    rows = ctx.db.scalars(select(ApplicationLaunch).where(
        ApplicationLaunch.workspace_id == ctx.workspace_id,
        ApplicationLaunch.owner_user_id == ctx.user_id,
        ApplicationLaunch.application_id == row.id,
        ApplicationLaunch.application_version_id == version.id,
    ).order_by(ApplicationLaunch.created_at.asc(), ApplicationLaunch.id.asc())).all()
    for launch in rows:
        manifest = launch.manifest_json or {}
        if str(manifest.get("idempotencyKey") or "").strip() != key:
            continue
        previous_hash = manifest.get("requestHash")
        if previous_hash and previous_hash != request_hash:
            raise ConflictError("同一幂等键已用于不同的应用启动参数", launchId=launch.id)
        return launch
    return None


def _launch_application_unclaimed(ctx: AuthContext, application_id: str, payload: LaunchInput) -> dict:
    """执行启动的实际工作：创建运行、按资源角色逐步准入并产出启动记录。
    以"未认领"方式运行，便于调用方在同一事务内决定认领/回滚。异常：业务校验与资源执行异常。
    """
    row = _record(ctx, application_id, "applications.execute")
    if row.status != "published":
        raise ValidationError("应用当前未发布或已停用")
    version = _published_version(ctx, row)
    spec = ApplicationSpec.model_validate(version.spec_json)
    if not _audience_allows(ctx, row, spec):
        raise PermissionErrorEx("当前账号不在此应用的发布受众中")
    resources = _launch_resources(ctx, spec)
    arguments = validate_arguments(payload.input, _launch_schema(ctx, spec, version))
    request_hash = _launch_request_hash(row, version, payload, arguments)
    entry = next(item for item in resources if item["role"] == "entry")
    launch_path = entry["path"]
    if not launch_path.startswith("/") or launch_path.startswith("//"):
        raise ValidationError("应用入口不是已登记的站内页面")
    manifest = {
        "applicationId": row.id, "applicationCode": row.code,
        "applicationVersion": version.version_number,
        "entry": entry, "resources": resources,
        "requiredPermissions": sorted({item["permission"] for item in resources if item["required"]}),
        "requestHash": request_hash,
    }
    if payload.idempotency_key:
        manifest["idempotencyKey"] = payload.idempotency_key
    workflow_execution = None
    if entry["type"] == "workflow":
        from app.modules.workflows import execution as workflow_runtime
        from app.modules.workflows.schemas import RunInput
        from app.modules.workflows.service import get_workflow
        workflow = get_workflow(ctx, entry["id"], "workflows.execute")
        release = _workflow_release(ctx, workflow.id, spec, version)
        if release is None:
            raise ValidationError("此应用没有可追溯的工作流正式修订，请创建并发布新的应用版本")
        pinned = release.revision
        entry["revision"] = pinned
        workflow_execution = workflow_runtime.start(ctx, workflow.id, RunInput(
            version=workflow.version, mode="published", inputs=arguments,
            idempotencyKey="app_" + hashlib.sha256(f"{row.id}:{payload.idempotency_key or new_id()}".encode()).hexdigest(),
            confirmWrites=payload.confirm_writes,
        ), published_revision=pinned)
        launch_path = f"/workflows/{quote(workflow.id)}?execution={quote(workflow_execution.id)}"
        run = ctx.db.get(Run, workflow_execution.runId)
    else:
        run = runtime.create_run(
            ctx.db, workspace_id=ctx.workspace_id, conversation_id="", user_id=ctx.user_id,
            context_snapshot={"application": manifest}, model_profile="application",
        )
        run.status = "running"
        run.started_at = _now()
        step = runtime.start_step(
            ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, sequence=1,
            step_type="application_launch", name="解析正式应用入口", input_data=arguments,
        )
        runtime.complete_step(ctx.db, step, {"launchPath": launch_path, "applicationVersion": version.version_number})
        runtime.finalize_run(ctx.db, run, status="completed")
    run.context_snapshot = {**(run.context_snapshot or {}), "application": manifest}
    previous_launch = ctx.db.scalar(select(ApplicationLaunch).where(
        ApplicationLaunch.workspace_id == ctx.workspace_id, ApplicationLaunch.run_id == run.id,
        ApplicationLaunch.application_id == row.id, ApplicationLaunch.owner_user_id == ctx.user_id,
    ))
    if previous_launch:
        return {"id": previous_launch.id, "runId": run.id, "status": run.status,
                "launchPath": previous_launch.launch_path, "manifest": previous_launch.manifest_json}
    launch = ApplicationLaunch(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        application_id=row.id, application_version_id=version.id, run_id=run.id,
        channel=payload.channel, input_json=arguments, manifest_json=manifest,
        launch_path=launch_path, status=run.status,
        created_at=_now(), updated_at=_now(), version=1,
    )
    ctx.db.add(launch)
    row.launch_count += 1
    row.last_launched_at = _now()
    row.updated_at = _now()
    commit_change(ctx, "application.launch", "applications", row.id, {
        "releaseVersion": version.version_number, "runId": run.id, "channel": payload.channel,
    })
    return {
        "id": launch.id, "runId": run.id, "status": launch.status,
        "launchPath": launch_path, "manifest": manifest,
    }


def launch_application(ctx: AuthContext, application_id: str, payload: LaunchInput) -> dict:
    """Launch an immutable release behind an atomic retry claim.

    Validation and resource snapshots are read-only.  The unique claim is
    committed before the unclaimed implementation can create a workflow/run,
    invoke a provider, or append launch history.  A failed owner releases no
    key for accidental replay: callers must choose a fresh key and the failed
    attempt remains auditable.
    """
    # 启动应用到运行：校验 → 幂等命中检查 → 资源准入 → 创建启动记录与运行。
    # 要点：必须存在可启动的正式版本；required 资源不可用直接失败，可选资源降级为 unavailable；
    # 同一幂等键重复提交返回已有的启动记录（不会重复执行副作用）。
    # 异常：ValidationError（无正式版本/入参非法）、NotFoundError、PermissionErrorEx、ConflictError。
    # 副作用：可能真实执行工作流/Bot 等资源（写入型动作需要 confirmWrites），并写启动记录与审计。
    #
    row = _record(ctx, application_id, "applications.execute")
    if row.status != "published":
        raise ValidationError("应用当前未发布或已停用")
    version = _published_version(ctx, row)
    spec = ApplicationSpec.model_validate(version.spec_json)
    if not _audience_allows(ctx, row, spec):
        raise PermissionErrorEx("当前账号不在此应用的发布受众中")
    _launch_resources(ctx, spec)
    arguments = validate_arguments(payload.input, _launch_schema(ctx, spec, version))
    request_hash = _launch_request_hash(row, version, payload, arguments)
    claim_result = claim_idempotency(
        ctx, scope=f"application_launch:{row.id}",
        key=payload.idempotency_key, request_hash=request_hash,
    )
    claim_id = claim_result.claim.id if claim_result else None
    if claim_result and claim_result.reused:
        replay = dict(claim_result.claim.result_json or {})
        replay["reused"] = True
        return replay
    if claim_result:
        # Preserve replay compatibility for launches created before the claim
        # table existed.  The check is after our claim commit, so current
        # requests cannot race one another through this legacy path.
        try:
            previous = _existing_launch(ctx, row, version, payload.idempotency_key, request_hash)
        except AppError as exc:
            finish_idempotency(
                ctx, claim_id, status="failed", error_message=exc.message,
                resource_type="application_launch",
            )
            raise
        except Exception:
            finish_idempotency(
                ctx, claim_id, status="failed", error_message="幂等历史读取失败",
                resource_type="application_launch",
            )
            raise
        if previous is not None:
            try:
                prior_run = ctx.db.get(Run, previous.run_id)
                replay = {
                    "id": previous.id, "runId": previous.run_id,
                    "status": prior_run.status if prior_run else previous.status,
                    "launchPath": previous.launch_path,
                    "manifest": previous.manifest_json,
                    "reused": True,
                }
                finish_idempotency(
                    ctx, claim_id, status="completed", result=replay,
                    resource_type="application_launch", resource_id=previous.id,
                )
                return replay
            except Exception:
                finish_idempotency(
                    ctx, claim_id, status="failed", error_message="幂等历史结果读取失败",
                    resource_type="application_launch", resource_id=previous.id,
                )
                raise
    try:
        result = _launch_application_unclaimed(ctx, application_id, payload)
    except AppError as exc:
        finish_idempotency(
            ctx, claim_id, status="failed", error_message=exc.message,
            resource_type="application_launch",
        )
        raise
    except Exception as exc:
        finish_idempotency(
            ctx, claim_id, status="failed", error_message="应用启动失败",
            resource_type="application_launch",
        )
        raise
    finish_idempotency(
        ctx, claim_id, status="completed", result=result,
        resource_type="application_launch", resource_id=result.get("id"),
    )
    return result


def list_launches(ctx: AuthContext, application_id: str, *, limit: int = 100) -> list[dict]:
    """列出某应用的启动记录（倒序，默认最多 100 条），供追溯谁在何时以什么参数启动过。"""
    row = _record(ctx, application_id, "applications.view")
    rows = ctx.db.scalars(visible_records(ctx, ApplicationLaunch, "applications.view").where(
        ApplicationLaunch.application_id == row.id,
    ).order_by(ApplicationLaunch.created_at.desc()).limit(limit)).all()
    can_view_runs = "runs.view" in access_policy(ctx).permissions
    run_statuses = dict(ctx.db.execute(select(Run.id, Run.status).where(
        Run.workspace_id == ctx.workspace_id, Run.id.in_([item.run_id for item in rows]),
    )).all()) if rows else {}
    return [{
        "id": item.id, "channel": item.channel, "status": run_statuses.get(item.run_id, item.status),
        "launchPath": item.launch_path,
        "runId": item.run_id if can_view_runs else None,
        "applicationVersion": item.manifest_json.get("applicationVersion"),
        "createTime": item.created_at.isoformat(),
    } for item in rows]
