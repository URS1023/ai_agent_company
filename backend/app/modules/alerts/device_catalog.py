"""A paged device catalogue with exact authorized counts and workflow status.

职责：设备台账列表接口的数据层 —— 按业务域（``alert`` / ``quality``）返回**已授权**的设备页，
并附带设备图片、质检服务列表以及该设备绑定的工作流（含各业务类别的流程条目）。

位置与依赖：由 ``alerts.router`` 的台账列表路由调用；依赖 ``owned_records.visible_records``
做工作区/所有者过滤，``registry.record_view`` 复用设备字段白名单，``card_images.image_map``
取图片，``workflows.service.get_workflow`` 复核流程可见性。

重要约束：
- ``total`` 必须是"过滤后的精确总数"（同一 query 的 count），不能把分页前的行数当作总数，
  否则前端分页会与实际可访问条数不一致。
- 设备对用户可见的条件是"其主数据源可见（或未绑定）+ 没有任何隐藏的质检服务"，
  任一质检服务不可见则整台设备隐藏，避免通过设备详情探测到无权访问的数据源。
- 数据库方言差异（JSON 数组成员匹配）在本模块内集中处理，其他模块不要复制该判断。
"""
import json

from sqlalchemy import String, cast, exists, false, func, or_, select

from app.common.owned_records import visible_records
from app.errors import NotFoundError, PermissionErrorEx
from app.modules.alerts import card_images, registry
from app.modules.alerts.models import Device, DeviceQualitySource
from app.models import DataSource
from app.modules.workflows.models import DeviceWorkflowBinding


def _domain_membership(ctx, domain: str):
    """Match one array value, including devices shared by both business domains.

    （中文补充）``domains`` 是 JSON 数组列，需按方言分别判断成员关系（MySQL 用
    ``JSON_CONTAINS``、SQLite 用 ``json_each``、其余方言退化为带引号的字符串包含）。
    必须精确匹配数组元素而不是子串，否则 ``alert`` 会命中 ``alert_maintenance`` 之类的值；
    设备同时属于 alert 与 quality 时两个域都应命中。
    """
    dialect = ctx.db.get_bind().dialect.name
    if dialect == "mysql":
        return func.JSON_CONTAINS(Device.domains, json.dumps(domain)) == 1
    if dialect == "sqlite":
        values = func.json_each(Device.domains).table_valued("value").alias("device_domain")
        return exists(select(1).select_from(values).where(values.c.value == domain))
    # DeviceInput restricts array values to the fixed alert/quality identifiers;
    # quoted tokens preserve exact membership on other platform dialects.
    return cast(Device.domains, String).contains(json.dumps(domain), autoescape=True)


def _visible_devices(ctx, domain: str, keyword: str):
    """构造"可见设备"基础查询：域匹配 + 关键字 + 数据源可见性 + 无隐藏质检服务。

    关键字对名称/编码/位置/类型做包含匹配（``autoescape=True`` 防通配符注入）。
    调用方注意：复合调用可以继续追加 where/order/limit，也会被 ``catalogue`` 用于 count，
    因此此处不能包含排序或分页，否则 count 口径会与被统计集合不一致。
    """
    query = visible_records(ctx, Device, "devices.view").where(
        Device.status != "deleted", _domain_membership(ctx, domain),
    )
    if keyword:
        query = query.where(or_(*(column.contains(keyword, autoescape=True)
                                  for column in (Device.name, Device.code, Device.location, Device.category))))
    try:
        allowed_sources = visible_records(ctx, DataSource, "datasources.view").with_only_columns(DataSource.id)
    except PermissionErrorEx:
        # Devices without linked sources remain readable. Grants cannot bypass
        # the underlying datasources.view action permission.
        allowed_sources = select(DataSource.id).where(false())
    hidden_detector = exists(select(1).where(
        DeviceQualitySource.workspace_id == ctx.workspace_id,
        DeviceQualitySource.device_id == Device.id,
        DeviceQualitySource.datasource_id.not_in(allowed_sources),
    ))
    return query.where(
        or_(Device.datasource_id.is_(None), Device.datasource_id.in_(allowed_sources)),
        ~hidden_detector,
    )


def catalogue(ctx, *, domain, limit, offset, keyword=""):
    """返回一页设备台账：``{items, total, limit, offset}``。

    每个 item 是设备字段白名单视图（``registry.record_view``）并补充：
    - ``image``：设备图片文件标识与版本（无图为 ``None``）；
    - ``workflow``：**当前业务域**绑定的流程摘要（``id`` / ``status`` / ``publishedRevision``）；
    - ``workflowEntries``：仅 alert 域给出巡检/保养/维修三类条目（键为
      ``inspection`` / ``maintenance`` / ``repair``），供台账按业务分别展示。

    查询次数与页大小成正比且保持常数级（图片、质检服务、流程绑定各一次批量查询），
    除流程逐个复核权限外不再逐设备查库。
    权限：先经 ``visible_records`` 过滤，逐个流程再用 ``get_workflow`` 复核；
    **流程不可见或不存在时静默 ``continue``**（该设备的该项留空，不报错、不影响其它设备），
    这是刻意的降级策略：台账列表不应因单条绑定的流程被删/失权而整体失败。
    """
    query = _visible_devices(ctx, domain, keyword)
    # total 用同一过滤条件的子查询计数，保证与分页结果同口径（精确总数，非估算）。
    total = ctx.db.scalar(select(func.count()).select_from(query.subquery()))
    page = ctx.db.scalars(query.order_by(Device.updated_at.desc(), Device.id).limit(limit).offset(offset)).all()
    ids = [row.id for row in page]
    extra: dict[str, list[str]] = {}
    if ids:
        mappings = ctx.db.execute(select(DeviceQualitySource.device_id, DeviceQualitySource.datasource_id).where(
            DeviceQualitySource.workspace_id == ctx.workspace_id, DeviceQualitySource.device_id.in_(ids),
        ).order_by(DeviceQualitySource.created_at, DeviceQualitySource.id))
        for device_id, source_id in mappings:
            extra.setdefault(device_id, []).append(source_id)
    photos = card_images.image_map(ctx, "device", ids)
    # alert 域需要连带取出保养/维修绑定（其 domain 值不同），quality 域只用同名绑定。
    bindings = ctx.db.scalars(select(DeviceWorkflowBinding).where(
        DeviceWorkflowBinding.workspace_id == ctx.workspace_id,
        DeviceWorkflowBinding.domain.in_(["alert", "alert_maintenance", "alert_repair"] if domain == "alert" else [domain]), DeviceWorkflowBinding.device_id.in_(ids),
    )).all() if ids else []
    workflows, workflow_entries = {}, {}
    from app.modules.workflows.service import get_workflow
    for binding in bindings:
        try:
            workflow = get_workflow(ctx, binding.workflow_id)
            if domain == 'alert' and workflow.status == 'disabled':
                continue
            item = {"id": workflow.id, "status": workflow.status, "publishedRevision": workflow.published_revision}
            if binding.domain == domain:
                workflows[binding.device_id] = item
            if domain == "alert":
                # 业务类别映射固定：alert→巡检，alert_maintenance→保养，alert_repair→维修。
                category = {"alert": "inspection", "alert_maintenance": "maintenance", "alert_repair": "repair"}[binding.domain]
                workflow_entries.setdefault(binding.device_id, {})[category] = item
        except (NotFoundError, PermissionErrorEx):
            # 流程被删除或超出权限：忽略该绑定，不泄露其存在性。
            continue
    # quality_sources 由本函数批量查出后传入，避免记录视图逐设备再查一次。
    return {"items": [{**registry.record_view(ctx, "devices", row, quality_sources=extra.get(row.id, [])), "image": photos.get(row.id),
                       "workflow": workflows.get(row.id), "workflowEntries": workflow_entries.get(row.id, {})} for row in page],
            "total": total, "limit": limit, "offset": offset}
