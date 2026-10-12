"""Complete report CRUD and immutable revisions independent of AI conversation."""
from __future__ import annotations

import json
from copy import deepcopy

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.modules.analytics.sources import get_source
from app.modules.analytics.snapshot_access import SnapshotAccess, SnapshotScopeError
from app.modules.reports.content import bound_source_ids, safe_asset_url, validate_content
from app.modules.reports.models import Report, ReportRevision
from app.modules.reports.schemas import ReportInput, ReportView, RestoreInput
from app.modules.reports.template_lock import assert_visual_unchanged, visual_fingerprint
from app.modules.reports.wire import content_for_wire
from app.modules.system.service_helpers import check_version, commit_change, flush_changes


def _authorize_content(access, report_id, content, source_ids):
    """真实快照按当前字段权限检查；已登记的原始仓库素材单独证明，不授权其查询。"""
    from app.modules.reports.source_reports import CatalogSnapshotProof
    if not hasattr(access, 'catalog_proof'):
        access.catalog_proof = CatalogSnapshotProof(access.ctx)
    return access.content(content, source_ids, trusted_widget=access.catalog_proof.checker(report_id))


def report_view(row: Report) -> ReportView:
    """把 ORM 行转换成对外视图；``content`` 序列化为 JSON 字符串以兼容既有设计器契约。

    ``enabled`` 只是"未被归档/删除"的派生状态，不等同于已发布；``version`` 既作乐观锁
    也作修订号，调用方必须原样回传。
    """

    description = row.description
    if getattr(row, '_catalog_reference', False):
        from app.modules.reports.source_reports import REFERENCE_NOTICE
        if not description.startswith(REFERENCE_NOTICE):
            description = REFERENCE_NOTICE + '\n' + description
    return ReportView(
        id=row.id, reportName=row.name, description=description,
        datasourceId=(row.datasource_ids or [None])[0], datasourceIds=row.datasource_ids,
        content=json.dumps(content_for_wire(row.content), ensure_ascii=False), width=row.width, height=row.height,
        backgroundColor=row.background_color, backgroundImage=row.background_image,
        thumbnail=row.thumbnail, refreshSeconds=row.refresh_seconds,
        enabled=row.status not in {"archived", "deleted"}, status=row.status,
        templateMode=row.template_mode, templateId=row.template_id, version=row.version,
        createTime=row.created_at.isoformat(), updateTime=row.updated_at.isoformat(), lineage=row.lineage,
    )


def get_report(ctx, report_id: str, permission: str = "dashboards.view") -> Report:
    """按当前操作范围读取报表，并重新校验其引用的每个数据源权限。

    参数 ``permission`` 决定可见性范围（查看/编辑/删除各自独立）。已软删除（status ==
    "deleted"）的报表按不存在处理，抛 ``NotFoundError``；任一被引用数据源已不可访问时
    同样抛错——仅有报表权限不足以看到其它数据源的快照内容。
    """

    row = owned_record(ctx, Report, report_id, permission)
    if row.status == "deleted":
        raise NotFoundError("报表已删除")
    # A stored screen may contain verified data; resource permission alone must
    # not reveal snapshots of a source that the actor can no longer access.
    row._catalog_reference = _authorize_content(SnapshotAccess(ctx), row.id, row.content, row.datasource_ids)
    return row


def list_reports(ctx, keyword: str = "") -> list[ReportView]:
    """列出当前范围内未删除的报表，``keyword`` 按名称做转义后的包含匹配。

    列表与详情的准入口径必须一致：行内任一数据源不在调用方可见范围内就整条不返回，
    避免"列表看不到、详情能打开"的越权读取路径。
    """

    query = visible_records(ctx, Report, "dashboards.view").where(Report.status != "deleted")
    if keyword:
        query = query.where(Report.name.contains(keyword, autoescape=True))
    from app.models import DataSource
    from app.common.authorization import access_policy
    accessible = set()
    if "datasources.view" in access_policy(ctx).permissions:
        accessible = set(ctx.db.scalars(visible_records(ctx, DataSource, "datasources.view").with_only_columns(DataSource.id)).all())
    access = SnapshotAccess(ctx)
    result = []
    for row in ctx.db.scalars(query.order_by(Report.updated_at.desc())).all():
        if not set(row.datasource_ids).issubset(accessible):
            continue
        try:
            row._catalog_reference = _authorize_content(access, row.id, row.content, row.datasource_ids)
        except (SnapshotScopeError, NotFoundError, PermissionErrorEx):
            continue
        result.append(report_view(row))
    return result


def add_revision(ctx, row: Report, reason: str) -> None:
    """追加一条不可变修订快照；本表只增不改，回滚历史依靠新建修订而非改写旧行。

    快照取自 ``report_view``，因此自包含画布、组件、模板与来源，恢复时无需再读其它表。
    """

    ctx.db.add(ReportRevision(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                              report_id=row.id, revision=row.version,
                              snapshot=report_view(row).model_dump(mode="json"), reason=reason))


def save_report(ctx, payload: ReportInput, report_id: str | None = None, *,
                lineage: dict | None = None, revision_reason: str | None = None) -> ReportView:
    """Save the normal report contract; trusted callers may add server-owned lineage.

    新建与更新共用这一条路径，保存语义如下：

    - 权限：新建需 ``dashboards.create``，更新需 ``dashboards.edit``，把状态改为
      ``published`` 额外需 ``dashboards.publish``。更新前用 ``check_version`` 校验
      payload.version，不一致抛 409，绝不静默覆盖并发修改。
    - 草稿与已发布：``published`` 只改变可见状态，不冻结内容，后续保存仍走同一路径；
      ``enabled`` 仅为兼容旧列表，映射到 draft/archived，不代表对外发布。
    - 模板："必须显式选择模板、禁止默认回退"的落点在这里：``manual`` 必须带
      template_id，``none`` 不允许带 template_id，未指定模板时也不会自动取第一套；
      ``auto`` 只在内容由生成流程产出时可用。
    - 视觉锁：模板声明 styleLocked 且本次不是用户编辑布局时，用
      ``assert_visual_unchanged`` 比对视觉指纹，改动模板视觉属性按 400 拒绝。
    - 数据源：显式提交的与画布中实际绑定的数据源都会逐个做权限校验，再回填
      ``datasource_ids``（主数据源置首）。
    - 副作用：每次显式保存都递增版本并追加一条修订快照（内容未变也算一次），随后
      ``commit_change`` 记录审计事件；新建且用了模板时额外写入模板使用记录。

    ``lineage``/``revision_reason`` 仅供可信的服务端调用方使用，用于补充来源追溯信息。
    """

    if not payload.report_name.strip():
        raise ValidationError("报表名称不能为空白")
    if report_id:
        row = get_report(ctx, report_id, "dashboards.edit")
        check_version(row, payload.version)
    else:
        require_permission(ctx, "dashboards.create")
        row = Report(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                     name=payload.report_name.strip(), description="", datasource_ids=[], content={},
                     width=1920, height=1080, background_color="#07111f", background_image="",
                     thumbnail="", refresh_seconds=60, status="draft", template_mode="none", lineage={})
        ctx.db.add(row)
    if payload.id not in {None, report_id}:
        raise ValidationError("报表标识与请求路径不一致")
    if payload.enabled is not None and payload.status is None:
        row.status = "draft" if payload.enabled and row.status == "archived" else ("archived" if not payload.enabled else row.status)
    if payload.status and payload.status != row.status:
        # 发布是独立权限点：能编辑不等于能对外发布。
        if payload.status == "published":
            require_permission(ctx, "dashboards.publish")
        row.status = payload.status
    previous_template_id = row.template_id
    mode = payload.template_mode or row.template_mode
    template_id = payload.template_id if "template_id" in payload.model_fields_set else row.template_id
    template_id = template_id or None
    # 显式模式的互斥校验：手动必须选模板，无模板必须不带模板，二者都不做默认回退。
    if mode == "manual" and not template_id:
        raise ValidationError("手动模式必须明确选择模板")
    if mode == "none" and template_id:
        raise ValidationError("无模板模式不能附带模板标识")
    selected_template = None
    if template_id:
        from app.modules.reports.templates import get_template
        # Disabling a template prevents new selection, but an existing report
        # must keep using its persisted lineage.  We still load that disabled
        # template here so a style-locked source keeps its visual hash
        # protection on every later save.
        existing_lineage = bool(report_id and template_id == row.template_id)
        selected_template = get_template(ctx, template_id, include_disabled=existing_lineage)
        # 参考目录升级不能让旧报表被新布局覆盖；旧实例继续以已保存的版本作为视觉基准。
        if (existing_lineage and selected_template.get("sourceKind") == "hifi-reference"
                and int(row.lineage.get("templateVersion") or 1) != int(selected_template.get("version") or 1)):
            selected_template = {
                **selected_template, "content": deepcopy(row.content),
                "version": int(row.lineage.get("templateVersion") or 1),
            }
    if not report_id and mode == "auto" and payload.content is None:
        raise ValidationError("自动适配需要生成需求，请使用 AI 生成操作；不会自动选择第一套模板")
    row.name, row.description = payload.report_name.strip(), payload.description
    row.template_mode, row.template_id = mode, template_id
    for key in ("width", "height", "background_color", "background_image", "thumbnail", "refresh_seconds"):
        value = getattr(payload, key)
        if value is not None:
            setattr(row, key, value)
    if not safe_asset_url(row.background_image) or not safe_asset_url(row.thumbnail):
        raise ValidationError("背景或预览图片地址不安全")
    if payload.content is not None:
        content = payload.content
    elif not report_id and template_id:
        content = deepcopy(selected_template["content"])
    else:
        content = row.content or {"canvas": {"width": row.width, "height": row.height}, "widgets": []}
    editable_layout = payload.edit_template_layout or bool(
        report_id and row.lineage.get("layoutCustomized") and template_id == previous_template_id
    )
    if selected_template and selected_template.get("sourceKind") == "hifi-reference":
        editable_layout = False
    if selected_template and not editable_layout and (
        selected_template.get("styleLocked")
        or selected_template.get("content", {}).get("canvas", {}).get("styleLocked")
    ):
        try:
            assert_visual_unchanged(selected_template["content"], content)
        except ValueError as exc:
            raise ValidationError("该模板仅允许填充数据源、只读 SQL、参数和查询结果，不能修改任何模板视觉属性") from exc
    if editable_layout:
        content = deepcopy(content)
        content.setdefault("canvas", {})["styleLocked"] = False
    row.content = validate_content(content, width=row.width, height=row.height)
    row.width, row.height = row.content["canvas"]["width"], row.content["canvas"]["height"]
    if selected_template and not report_id:
        template_canvas = row.content.get("canvas") or {}
        # Metadata shown in cards and previews must describe the copied canvas,
        # not the generic defaults from a newly allocated report row.
        row.background_color = str(template_canvas.get("backgroundColor") or row.background_color)
        row.background_image = str(template_canvas.get("backgroundImage") or "")
        if payload.thumbnail is None:
            row.thumbnail = str(selected_template.get("preview") or "")
        if not safe_asset_url(row.background_image) or not safe_asset_url(row.thumbnail):
            raise ValidationError("模板背景或预览图片地址不安全")
    source_ids = set(row.datasource_ids)
    if "datasource_ids" in payload.model_fields_set or "datasource_id" in payload.model_fields_set or not report_id:
        source_ids = set(payload.datasource_ids)
        if payload.datasource_id:
            source_ids.add(payload.datasource_id)
    source_ids |= bound_source_ids(row.content)
    for source_id in source_ids:
        get_source(ctx, source_id, provider="any")
    # 保存与修订恢复使用相同的留存来源校验，不允许重新保存已撤权的内嵌数据。
    row._catalog_reference = _authorize_content(SnapshotAccess(ctx), row.id, row.content, source_ids)
    primary = payload.datasource_id or (row.datasource_ids[0] if row.datasource_ids else None)
    row.datasource_ids = ([primary] if primary in source_ids else []) + sorted(source_ids - {primary})
    row.lineage = {
        **row.lineage,
        "entry": row.lineage.get("entry") or "report_module",
        "dataStatus": "bound" if bound_source_ids(row.content) else "unbound",
        **({
            "templateVisualHash": visual_fingerprint(selected_template["content"]),
            "templateVersion": int(selected_template.get("version") or 1),
            "templateBindingContract": selected_template.get("bindingContract") or (
                selected_template.get("content", {}).get("canvas", {}).get("bindingContract") or ""
            ),
            "templateSourceReportId": selected_template.get("sourceReportId") or (
                selected_template.get("content", {}).get("canvas", {}).get("templateSourceReportId")
            ),
        } if selected_template else {}),
        **deepcopy(lineage or {}),
        "layoutCustomized": editable_layout,
        **({"layoutCustomized": True, "preserveSourceWidgets": True} if editable_layout else {}),
    }
    # An explicit save is a new revision even when its content is unchanged.
    # Keep the version captured at load time as the SQL UPDATE precondition.
    if report_id:
        row.version += 1
    flush_changes(ctx)
    add_revision(ctx, row, revision_reason or ("编辑保存" if report_id else "新建报表"))
    if not report_id and template_id:
        from app.modules.reports.templates import record_usage
        record_usage(ctx, template_id, row.id)
    commit_change(ctx, "reports.save", "dashboards", row.id)
    return report_view(row)


def remove_report(ctx, report_id: str) -> None:
    """把报表移入回收站：``status="deleted"`` 软删除，并保留一条修订说明删除动作。

    需要 ``dashboards.delete``。不删除源数据库记录、源文件或其他报表；删除后详情接口
    按 404 处理，但历史修订仍在（当前没有对外恢复入口，恢复依赖数据库操作）。
    """

    row = get_report(ctx, report_id, "dashboards.delete")
    row.status = "deleted"
    flush_changes(ctx)
    add_revision(ctx, row, "删除（保留历史）")
    commit_change(ctx, "reports.delete", "dashboards", report_id)


def revisions(ctx, report_id: str) -> list[dict]:
    """倒序返回报表的全部修订记录，并逐条标注当前是否仍可访问。

    ``available=False`` 表示该快照引用了调用方已失去权限的数据源，此时 ``snapshot``
    必须为 None——不能因为它在历史里就绕过数据源授权。
    """

    get_report(ctx, report_id)
    rows = ctx.db.scalars(select(ReportRevision).where(
        ReportRevision.workspace_id == ctx.workspace_id, ReportRevision.report_id == report_id,
    ).order_by(ReportRevision.revision.desc())).all()
    result = []
    access = SnapshotAccess(ctx)
    for row in rows:
        accessible = True
        try:
            reference = _authorize_content(access, report_id, row.snapshot.get('content') or {}, row.snapshot.get('datasourceIds') or [])
        except (NotFoundError, PermissionErrorEx):
            accessible = False
        snapshot = deepcopy(row.snapshot) if accessible else None
        if snapshot is not None and "content" in snapshot:
            snapshot["content"] = content_for_wire(snapshot["content"])
        if accessible and reference:
            from app.modules.reports.source_reports import REFERENCE_NOTICE
            description = snapshot.get('description') or ''
            if not description.startswith(REFERENCE_NOTICE):
                snapshot['description'] = REFERENCE_NOTICE + '\n' + description
        result.append({"revision": row.revision, "reason": row.reason, "createdBy": row.owner_user_id,
                       "createTime": row.created_at.isoformat(), "available": accessible,
                       "snapshot": snapshot})
    return result


def restore_report(ctx, report_id: str, payload: RestoreInput) -> ReportView:
    """把历史修订恢复为报表当前内容，结果始终是一个**新草稿**。

    需要 ``dashboards.edit``，且必须提交当前版本（``payload.version``）防止覆盖并发修改。
    实现上把历史快照裁剪成 ``ReportInput`` 允许的字段后重新走 ``save_report``，因此视觉锁、
    数据源权限、模板引用都会再校验一遍；旧修订不被改写，恢复本身又产生一条新修订。
    """

    row = get_report(ctx, report_id, "dashboards.edit")
    check_version(row, payload.version)
    revision = ctx.db.scalar(select(ReportRevision).where(
        ReportRevision.workspace_id == ctx.workspace_id, ReportRevision.report_id == report_id,
        ReportRevision.revision == payload.revision,
    ))
    if not revision:
        raise NotFoundError("历史修订不存在")
    allowed_fields = {field.alias or name for name, field in ReportInput.model_fields.items()}
    request = {key: value for key, value in revision.snapshot.items() if key in allowed_fields}
    request.update({"version": row.version, "status": "draft"})
    return save_report(ctx, ReportInput.model_validate(request), report_id)
