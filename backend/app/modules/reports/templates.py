"""Source-imported visuals and governed Cloud Brain workspace templates."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime

from sqlalchemy import func, select

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.modules.reports.content import builtin_templates, hifi_templates, safe_asset_url, template_skeleton
from app.modules.reports.models import (
    ReportBuiltinTemplateState,
    ReportTemplate,
    ReportTemplateRevision,
    ReportTemplateUsage,
)
from app.modules.reports.schemas import (
    TemplateFromReportInput, TemplateInput, TemplatePackageInput,
    TemplatePublishInput, TemplateRestoreInput,
)
from app.modules.reports.template_lock import lock_template_content, template_visual_fingerprint
from app.modules.system.service_helpers import check_version, commit_change, flush_changes


PACKAGE_VERSION = "report-template-package-v1"


def _builtin_rows() -> tuple[dict, ...]:
    """返回当前可选的内置模板；保留旧来源用于已存在报表的兼容读取。"""

    return (*hifi_templates(), *builtin_templates())


def _snapshot(row: ReportTemplate) -> dict:
    """生成模板修订快照（只含可恢复字段），供 ``ReportTemplateRevision`` 持久化。"""

    return {
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "tags": deepcopy(row.tags or []),
        "content": deepcopy(row.content or {}),
        "preview": row.preview,
        "enabled": bool(row.enabled),
        "version": row.version,
    }


def _add_revision(ctx, row: ReportTemplate, reason: str) -> None:
    """追加一条模板修订（版本号取当前 ``row.version``），只增不改。"""

    ctx.db.add(ReportTemplateRevision(
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        template_id=row.id,
        revision=row.version,
        snapshot=_snapshot(row),
        reason=reason,
    ))


def _usage_stats(ctx) -> dict[str, dict]:
    """按模板聚合真实使用次数与最近使用时间；受当前数据范围约束（只统计可见记录）。"""

    statement = visible_records(ctx, ReportTemplateUsage, "templates.view").with_only_columns(
        ReportTemplateUsage.template_id,
        func.count(ReportTemplateUsage.id),
        func.max(ReportTemplateUsage.created_at),
    ).group_by(ReportTemplateUsage.template_id)
    return {
        str(template_id): {"usageCount": int(count), "lastUsedAt": last_used.isoformat() if last_used else None}
        for template_id, count, last_used in ctx.db.execute(statement).all()
    }


def _revision_counts(ctx) -> dict[str, int]:
    """统计每个自定义模板的修订条数（列表展示用，避免逐行查询）。"""

    rows = ctx.db.execute(select(
        ReportTemplateRevision.template_id, func.count(ReportTemplateRevision.id),
    ).where(
        ReportTemplateRevision.workspace_id == ctx.workspace_id,
    ).group_by(ReportTemplateRevision.template_id)).all()
    return {str(template_id): int(count) for template_id, count in rows}


def template_view(row: ReportTemplate, *, usage: dict | None = None, revision_count: int | None = None) -> dict:
    """自定义模板对外视图；``deleted`` 与 ``lifecycleStatus`` 由 ``enabled`` 派生（软删除）。

    ``visualHash`` 优先取画布内已落库的 ``templateVisualHash``，缺失时才现算，
    保证历史模板的指纹与创建时一致（数据被清空不改变指纹）。
    """

    usage = usage or {}
    canvas = (row.content or {}).get("canvas") or {}
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "summary": row.description,
        "category": row.category,
        "tags": row.tags,
        "content": row.content,
        "preview": row.preview,
        "enabled": row.enabled,
        "version": row.version,
        "builtin": False,
        "componentCount": len((row.content or {}).get("widgets", [])),
        "createTime": row.created_at.isoformat(),
        "updateTime": row.updated_at.isoformat(),
        "usageCount": int(usage.get("usageCount") or 0),
        "lastUsedAt": usage.get("lastUsedAt"),
        "revisionCount": int(revision_count or 1),
        "publishedRevision": row.version if row.enabled else None,
        "lifecycleStatus": "published" if row.enabled else "disabled",
        "deleted": not bool(row.enabled),
        "styleLocked": bool(canvas.get("styleLocked")),
        "bindingContract": canvas.get("bindingContract") or "",
        "sourceReportId": canvas.get("templateSourceReportId") or None,
        "sourceReportVersion": canvas.get("templateSourceReportVersion"),
        "visualHash": canvas.get("templateVisualHash") or template_visual_fingerprint(row.content or {}),
    }


def _builtin_view(row: dict, usage: dict | None = None, *, hidden: bool = False) -> dict:
    """在只读的内置模板条目上叠加使用统计与回收站状态；``hidden`` 即本工作区已删除。"""

    result = deepcopy(row)
    usage = usage or {}
    result.update({
        "usageCount": int(usage.get("usageCount") or 0),
        "lastUsedAt": usage.get("lastUsedAt"),
        "revisionCount": 1,
        "publishedRevision": 1 if result.get("enabled", True) else None,
        "lifecycleStatus": "published" if result.get("enabled", True) else "disabled",
        "deleted": hidden,
    })
    return result


def _hidden_builtin_ids(ctx) -> set[str]:
    """当前工作区被移入回收站的内置模板 ID 集合（内置模板只读，删除状态在这里记录）。"""

    return set(ctx.db.scalars(select(ReportBuiltinTemplateState.template_id).where(
        ReportBuiltinTemplateState.workspace_id == ctx.workspace_id,
        ReportBuiltinTemplateState.hidden.is_(True),
    )).all())


def list_templates(ctx, keyword: str = "", *, include_deleted: bool = False) -> list[dict]:
    """列出 20 套高保真模板与工作区自定义模板；需要 ``templates.view``。

    旧版接口来源模板继续由 ``get_template`` 支持已有报表，但不再进入新建模板列表。
    ``include_deleted=False``（默认）时回收站中的条目不出现在列表里；``keyword`` 只
    在名称/描述/标签上做大小写不敏感的包含匹配。
    """

    require_permission(ctx, "templates.view")
    usage = _usage_stats(ctx)
    revision_counts = _revision_counts(ctx)
    hidden_ids = _hidden_builtin_ids(ctx)
    result = [
        _builtin_view(row, usage.get(str(row["id"])), hidden=row["id"] in hidden_ids)
        for row in hifi_templates()
        if include_deleted or row["id"] not in hidden_ids
    ]
    # 旧来源模板从新建列表下线，但在回收站中保留入口，确保历史报表与恢复操作不失效。
    if include_deleted:
        result.extend([
            _builtin_view(row, usage.get(str(row["id"])), hidden=row["id"] in hidden_ids)
            for row in builtin_templates()
        ])
    custom = [template_view(
        row,
        usage=usage.get(row.id),
        revision_count=revision_counts.get(row.id),
    ) for row in ctx.db.scalars(
        visible_records(ctx, ReportTemplate, "templates.view").order_by(ReportTemplate.updated_at.desc()),
    ).all()]
    result.extend(custom if include_deleted else [row for row in custom if not row["deleted"]])
    keyword = keyword.lower().strip()
    return [row for row in result if not keyword or keyword in " ".join(
        [row["name"], row.get("description", ""), *row.get("tags", [])],
    ).lower()]


def get_template(ctx, template_id: str, *, include_disabled: bool = False) -> dict:
    """按 ID 读取内置或自定义模板；需要 ``templates.view``。

    **不允许默认回退**：ID 缺失或查不到一律抛 ``ValidationError``，绝不用"第一套模板"顶替。
    ``include_disabled=True`` 供服务端内部路径（编辑/导出/恢复/沿用既有模板的报表保存）
    使用；对外调用保持默认值，已删除或已停用的模板按不可用处理。
    """

    require_permission(ctx, "templates.view")
    for row in _builtin_rows():
        if row["id"] == template_id:
            hidden = template_id in _hidden_builtin_ids(ctx)
            if hidden and not include_disabled:
                raise ValidationError("模板已从当前工作区删除，可在回收站恢复")
            if not row.get("enabled", True) and not include_disabled:
                raise ValidationError("模板源记录已删除、停用或没有可用视觉配置")
            return _builtin_view(row, hidden=hidden)
    for row in builtin_templates():
        if row["id"] == template_id:
            hidden = template_id in _hidden_builtin_ids(ctx)
            if hidden and not include_disabled:
                raise ValidationError("模板已从当前工作区删除，可在回收站恢复")
            if not row.get("enabled", True) and not include_disabled:
                raise ValidationError("模板源记录已删除、停用或没有可用视觉配置")
            return _builtin_view(row, hidden=hidden)
    row = owned_record(ctx, ReportTemplate, template_id, "templates.view")
    if not row.enabled and not include_disabled:
        raise ValidationError("模板已停用")
    return template_view(row)


def save_template(ctx, payload: TemplateInput, template_id: str | None = None) -> dict:
    """新建（需 ``templates.create``）或按版本更新（需 ``templates.edit``）自定义模板。

    保存语义：内容一律经 ``template_skeleton`` 归零——清空数据源、SQL、参数、结果与凭据，
    只留视觉骨架；每次保存都追加快照修订（内容未变也算一次）并写审计。内置/接口导入模板
    视觉只读，尝试覆盖直接抛权限错误；更新必须提交正确 ``version``，否则 409。
    """

    if not payload.name.strip() or not safe_asset_url(payload.preview):
        raise ValidationError("请输入模板名称，并使用安全的预览图片地址")
    if template_id:
        if any(row["id"] == template_id for row in _builtin_rows()):
            raise PermissionErrorEx("接口导入模板视觉只读，不能直接覆盖")
        row = owned_record(ctx, ReportTemplate, template_id, "templates.edit")
        check_version(row, payload.version)
        reason = "编辑模板"
    else:
        require_permission(ctx, "templates.create")
        row = ReportTemplate(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
        reason = "新建模板"
    row.name, row.description, row.category = payload.name.strip(), payload.description, payload.category
    row.tags, row.preview, row.enabled = payload.tags, payload.preview, payload.enabled
    row.content = template_skeleton(payload.content)
    flush_changes(ctx)
    _add_revision(ctx, row, reason)
    commit_change(ctx, "reports.template.save", "templates", row.id, {"revision": row.version})
    revision_count = ctx.db.scalar(select(func.count(ReportTemplateRevision.id)).where(
        ReportTemplateRevision.workspace_id == ctx.workspace_id,
        ReportTemplateRevision.template_id == row.id,
    ))
    return template_view(row, revision_count=revision_count)


def create_from_report(ctx, report_id: str, payload: TemplateFromReportInput) -> dict:
    """Promote one accessible report to a locked, data-empty template.

    The report remains untouched.  Only its approved runtime bindings and
    results are removed; every visual component and authored property remains
    represented by the stable visual fingerprint.

    可视作"另存为模板"：需要 ``templates.create`` 且调用方能读取源报表。创建前用
    ``template_visual_fingerprint`` 复核"清空数据后视觉未变"，不一致直接拒绝——这是
    模板可信度的自证步骤，避免清数据时误删视觉属性。``preview`` 为空时沿用报表缩略图。
    """

    require_permission(ctx, "templates.create")
    from app.modules.reports.service import get_report

    report = get_report(ctx, report_id)
    preview = report.thumbnail if payload.preview is None else payload.preview
    if not safe_asset_url(preview or ""):
        raise ValidationError("模板预览图片地址不安全")
    content = lock_template_content(
        report.content or {},
        source_report_id=report.id,
        source_report_version=report.version,
    )
    source_visual_hash = template_visual_fingerprint(report.content or {})
    if template_visual_fingerprint(content) != source_visual_hash:
        raise ValidationError("清空业务数据后模板视觉结构不一致，已拒绝创建")
    name = payload.name.strip() or f"{report.name}模板"
    description = payload.description or report.description or f"基于《{report.name}》创建的锁定视觉模板"
    template = save_template(ctx, TemplateInput(
        name=name,
        description=description,
        category=payload.category,
        tags=payload.tags,
        content=content,
        preview=preview or "",
        enabled=payload.enabled,
    ))
    commit_change(ctx, "reports.template.from_report", "templates", template["id"], {
        "sourceReportId": report.id,
        "sourceReportVersion": report.version,
        "visualHash": source_visual_hash,
        "componentCount": len((report.content or {}).get("widgets") or []),
    })
    return template


def remove_template(ctx, template_id: str) -> None:
    """把模板移入回收站（软删除），内置与自定义模板都可恢复；需要 ``templates.delete``。

    内置模板本身只读，删除只写工作区级 ``ReportBuiltinTemplateState.hidden``；自定义模板
    置 ``enabled=False`` 并追加一条"停用模板"修订。已用该模板创建的报表保持原有布局与
    模板来源，不受影响。重复删除抛 ``ValidationError``。
    """

    builtin = next((row for row in _builtin_rows() if row["id"] == template_id), None)
    if builtin:
        require_permission(ctx, "templates.delete")
        state = ctx.db.scalar(select(ReportBuiltinTemplateState).where(
            ReportBuiltinTemplateState.workspace_id == ctx.workspace_id,
            ReportBuiltinTemplateState.template_id == template_id,
        ))
        if state and state.hidden:
            raise ValidationError("模板已经在回收站中")
        if state is None:
            state = ReportBuiltinTemplateState(
                workspace_id=ctx.workspace_id,
                owner_user_id=ctx.user_id,
                template_id=template_id,
                hidden=True,
            )
            ctx.db.add(state)
        else:
            state.hidden = True
            state.owner_user_id = ctx.user_id
        flush_changes(ctx)
        commit_change(ctx, "reports.template.delete", "templates", template_id, {
            "sourceTemplate": True,
            "sourceKind": builtin.get("sourceKind"),
        })
        return
    row = owned_record(ctx, ReportTemplate, template_id, "templates.delete")
    if not row.enabled:
        raise ValidationError("模板已经停用")
    # Existing reports retain their full layout and template ID lineage.
    row.enabled = False
    flush_changes(ctx)
    _add_revision(ctx, row, "停用模板")
    commit_change(ctx, "reports.template.disable", "templates", row.id, {"revision": row.version})


def recover_template(ctx, template_id: str) -> dict:
    """Restore a soft-deleted custom or immutable source template.

    从回收站恢复，需要 ``templates.edit``；不重写模板内容（自定义模板只翻回 enabled 并追加
    修订，内置模板只清除 hidden 标记）。未被删除时抛 ``ValidationError``。
    """

    builtin = next((row for row in _builtin_rows() if row["id"] == template_id), None)
    if builtin:
        require_permission(ctx, "templates.edit")
        state = ctx.db.scalar(select(ReportBuiltinTemplateState).where(
            ReportBuiltinTemplateState.workspace_id == ctx.workspace_id,
            ReportBuiltinTemplateState.template_id == template_id,
        ))
        if state is None or not state.hidden:
            raise ValidationError("模板未被删除")
        state.hidden = False
        state.owner_user_id = ctx.user_id
        flush_changes(ctx)
        commit_change(ctx, "reports.template.recover", "templates", template_id, {"sourceTemplate": True})
        return _builtin_view(builtin, hidden=False)
    row = owned_record(ctx, ReportTemplate, template_id, "templates.edit")
    if row.enabled:
        raise ValidationError("模板未被删除")
    row.enabled = True
    flush_changes(ctx)
    _add_revision(ctx, row, "从回收站恢复")
    commit_change(ctx, "reports.template.recover", "templates", row.id, {"revision": row.version})
    return template_view(row)


def publish_template(ctx, template_id: str, payload: TemplatePublishInput) -> dict:
    """按版本重新发布已停用的自定义模板；需要 ``templates.edit``。

    内置模板由来源目录管理状态，直接拒绝；``version`` 必须与当前版本一致（防并发）；
    已处于可用状态时抛 ``ValidationError``。发布同样产生新修订以便追溯。
    """

    if any(row["id"] == template_id for row in _builtin_rows()):
        raise PermissionErrorEx("接口导入模板由来源目录管理，不能修改状态")
    row = owned_record(ctx, ReportTemplate, template_id, "templates.edit")
    check_version(row, payload.version)
    if row.enabled:
        raise ValidationError("模板已经处于可用状态")
    row.enabled = True
    flush_changes(ctx)
    _add_revision(ctx, row, "重新发布模板")
    commit_change(ctx, "reports.template.publish", "templates", row.id, {"revision": row.version})
    return template_view(row)


def revisions(ctx, template_id: str) -> list[dict]:
    """倒序返回模板修订历史；内置模板返回一条"来源版本"且 ``restorable=False``。

    内置模板不可恢复为自定义版本，因此这里显式给出不可恢复标记，前端据此隐藏恢复入口。
    """

    template = get_template(ctx, template_id, include_disabled=True)
    if template.get("builtin"):
        snapshot = {
            key: deepcopy(template.get(key))
            for key in ("name", "description", "category", "tags", "content", "preview", "enabled", "version")
        }
        return [{
            "revision": 1,
            "reason": "智慧云脑 Cloud Brain 接口来源版本",
            "createdBy": "system",
            "createTime": None,
            "snapshot": snapshot,
            "restorable": False,
        }]
    rows = ctx.db.scalars(select(ReportTemplateRevision).where(
        ReportTemplateRevision.workspace_id == ctx.workspace_id,
        ReportTemplateRevision.template_id == template_id,
    ).order_by(ReportTemplateRevision.revision.desc())).all()
    return [{
        "revision": row.revision,
        "reason": row.reason,
        "createdBy": row.owner_user_id,
        "createTime": row.created_at.isoformat(),
        "snapshot": row.snapshot,
        "restorable": True,
    } for row in rows]


def restore_template(ctx, template_id: str, payload: TemplateRestoreInput) -> dict:
    """恢复到指定历史修订并生成**新**版本；需要 ``templates.edit``。

    历史快照本身不变；恢复内容同样经 ``template_skeleton`` 归零，因此恢复出来的模板依旧
    不含数据与凭据。需要提交当前 ``version``；内置模板不允许恢复。
    """

    if any(row["id"] == template_id for row in _builtin_rows()):
        raise PermissionErrorEx("接口导入模板不能恢复为自定义版本")
    row = owned_record(ctx, ReportTemplate, template_id, "templates.edit")
    check_version(row, payload.version)
    revision = ctx.db.scalar(select(ReportTemplateRevision).where(
        ReportTemplateRevision.workspace_id == ctx.workspace_id,
        ReportTemplateRevision.template_id == template_id,
        ReportTemplateRevision.revision == payload.revision,
    ))
    if revision is None:
        raise NotFoundError("模板历史修订不存在")
    snapshot = revision.snapshot
    row.name = str(snapshot.get("name") or "").strip()
    row.description = str(snapshot.get("description") or "")
    row.category = str(snapshot.get("category") or "自定义")
    row.tags = list(snapshot.get("tags") or [])
    row.content = template_skeleton(snapshot.get("content") or {})
    row.preview = str(snapshot.get("preview") or "")
    row.enabled = bool(snapshot.get("enabled", True))
    if not row.name or not safe_asset_url(row.preview):
        raise ValidationError("历史模板名称或预览地址无效")
    # Restoring the current snapshot is still an explicit, auditable revision.
    row.version += 1
    flush_changes(ctx)
    _add_revision(ctx, row, f"恢复到 v{payload.revision}")
    commit_change(ctx, "reports.template.restore", "templates", row.id, {
        "revision": row.version, "restored_revision": payload.revision,
    })
    return template_view(row)


def _checksum(value: dict) -> str:
    """模板包载荷的 SHA-256；序列化必须排序键、紧凑分隔且 ``allow_nan=False``，
    否则导出端与导入端算出的摘要会因浮点/键序差异对不上。"""

    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def export_template(ctx, template_id: str) -> dict:
    """导出无业务数据的可校验模板包；需要 ``templates.view``。

    内置、自定义与已停用模板都可导出；``content`` 经 ``template_skeleton`` 归零，包内不含
    SQL、数据源、查询结果或凭据，``checksum`` 覆盖除自身外的全部载荷字段。
    """

    template = get_template(ctx, template_id, include_disabled=True)
    portable = {
        "name": template["name"],
        "description": template.get("description") or template.get("summary") or "",
        "category": template.get("category") or "自定义",
        "tags": list(template.get("tags") or []),
        "content": template_skeleton(template.get("content") or {}),
        "preview": template.get("preview") or "",
        "enabled": bool(template.get("enabled", True)),
        "version": int(template.get("version") or 1),
    }
    return {
        "schemaVersion": PACKAGE_VERSION,
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "source": {
            "id": template["id"],
            "builtin": bool(template.get("builtin")),
            "version": int(template.get("version") or 1),
            "styleLocked": bool(template.get("styleLocked") or portable["content"].get("canvas", {}).get("styleLocked")),
        },
        "template": portable,
        "checksum": _checksum(portable),
    }


def import_template(ctx, payload: TemplatePackageInput) -> dict:
    """校验模板包并原子导入为新模板；需要 ``templates.create``。

    先比对 SHA-256（不一致即整包拒绝，不做部分导入），再限制载荷不超过 8 MB，最后以
    ``version=None`` 走 ``save_template`` 重新归零内容——因此导入包即使被手工塞入数据或
    凭据也不会落库。
    """

    require_permission(ctx, "templates.create")
    portable = payload.template.model_dump(mode="json", by_alias=True)
    if _checksum(portable) != payload.checksum:
        raise ValidationError("模板包校验失败，文件内容可能不完整或已被修改")
    if len(json.dumps(portable, ensure_ascii=False).encode("utf-8")) > 8 * 1024 * 1024:
        raise ValidationError("模板包超过 8 MB")
    create_payload = payload.template.model_copy(update={"version": None})
    return save_template(ctx, create_payload)


def record_usage(ctx, template_id: str, report_id: str, *, source: str = "report_create") -> None:
    """记录一次真实的"用该模板创建报表"事件（只追加）。

    ``detail`` 只保存模板名称等非敏感上下文；不记录任何数据源、SQL 或凭据。
    由服务端在真正创建报表后调用，前端列表计数不构成使用记录。
    """

    template = get_template(ctx, template_id)
    ctx.db.add(ReportTemplateUsage(
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        template_id=template_id,
        template_version=int(template.get("version") or 1),
        report_id=report_id,
        source=source,
        detail={"templateName": template.get("name") or ""},
    ))


def usage_history(ctx, template_id: str, limit: int = 100) -> list[dict]:
    """返回模板的真实使用记录（倒序，最多 ``limit`` 条）；需 ``templates.view``。

    受当前数据范围约束：只能看到自己有权限的使用事件。
    """

    get_template(ctx, template_id, include_disabled=True)
    rows = ctx.db.scalars(visible_records(ctx, ReportTemplateUsage, "templates.view").where(
        ReportTemplateUsage.template_id == template_id,
    ).order_by(ReportTemplateUsage.created_at.desc()).limit(limit)).all()
    return [{
        "id": row.id,
        "templateVersion": row.template_version,
        "reportId": row.report_id,
        "source": row.source,
        "detail": row.detail,
        "createdBy": row.owner_user_id,
        "createTime": row.created_at.isoformat(),
    } for row in rows]
