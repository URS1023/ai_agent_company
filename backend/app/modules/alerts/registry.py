"""Permission-aware equipment, measurement, expert and policy lifecycle services.

职责：设备预警域"配置类对象"的统一服务层 —— 设备、测点、专家经验、预警策略的
增删改查与授权，以及策略修订的读取。所有页面入口都经由此处，以保证权限与一致性口径统一。

位置与依赖：被 ``alerts.router``（REST 接口）、``alerts.device_catalog``、
``alerts.reports``（读取时复核授权）、``alerts.workflow_builder``（向导预检）调用。
依赖 ``owned_records`` 做工作区/所有者范围过滤，``system.service_helpers`` 做乐观锁与审计。

对外约定（重要）：
- 权限模型是"对象 + 动作"：``<业务域>.<动作>``，业务域取 ``DEFINITIONS`` 中的第三项
  （devices / alerts），动作有 view / create / edit / delete / execute。
- 删除一律**软删**（``status='deleted'``）：修订、报告、问题处置历史全部保留。
- 设备是共享资源：读取设备会连带校验其采集源与全部质检服务的可见性；
  读取测点/策略/经验还会回溯校验其父设备，避免通过子对象绕过设备权限。
- 列表查询采用"先授权后分页"，因此返回条数等于请求条数（可能多扫描若干批）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, or_, select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.models import DataSource
from app.modules.alerts.models import AlertRule, AlertRuleRevision, Device, DeviceQualitySource, ExpertAdvice, MeasurementPoint
from app.modules.alerts.schemas import AdviceInput, AlertRuleInput, DeviceInput, Editable, PointInput
from app.modules.collection.transport import validate_body_secrets
from app.modules.system.service_helpers import check_version, commit_change, flush_changes
from app.modules.workflows.expressions import validate_expression
from app.modules.workflows.graph import validate_json

# kind → (模型, 入参契约, 权限业务域)。新增可配置对象时必须同时登记，
# 否则 get_record/save_record 无法解析权限串与字段白名单。
# 注意 points 归入 devices 域、advice/rules 归入 alerts 域，这是既有的授权边界。
DEFINITIONS = {
    "devices": (Device, DeviceInput, "devices"),
    "points": (MeasurementPoint, PointInput, "devices"),
    "advice": (ExpertAdvice, AdviceInput, "alerts"),
    "rules": (AlertRule, AlertRuleInput, "alerts"),
}


def device(ctx: AuthContext, device_id: str, *, active: bool = False, deleted: bool = False) -> Device:
    """Protect shared equipment and its configured source, including old reports.

    （中文补充）设备读取的唯一入口：``devices.view`` 权限 + 未删除（除非 ``deleted=True``，
    供历史报告回读）+ ``active=True`` 时要求 status=='active' 且采集源也处于 active。
    另外会逐个校验**质检服务**的可见性 —— 设备配置里的服务标识本身属于设备信息，
    因此只要有一个服务不可见，整台设备视为不可见（抛 ``PermissionErrorEx``/``NotFoundError``）。
    异常：设备已删除或不存在、无权查看、已停用而要求 active、采集源已停用时分别抛出。
    """
    row = owned_record(ctx, Device, device_id, "devices.view")
    if row.status == "deleted" and not deleted:
        raise NotFoundError("设备已移除")
    if active and row.status != "active":
        raise ValidationError("设备已停用、维护或移除，不能开始新检测")
    if row.datasource_id:
        source = owned_record(ctx, DataSource, row.datasource_id, "datasources.view")
        if active and source.status != "active":
            raise ValidationError("设备采集源已停用或移除")
    # The identifiers are part of the device configuration, so callers may only
    # see the device when every linked detector service remains in their scope.
    for source_id in quality_source_ids(ctx, row.id):
        owned_record(ctx, DataSource, source_id, "datasources.view")
    return row


def quality_source_ids(ctx: AuthContext, device_id: str) -> list[str]:
    """Return the stable configured order without exposing another workspace.

    （中文补充）设备获准的质检 HTTP 服务标识列表，按 ``(created_at, id)`` 排序，
    保证同一设备每次返回顺序一致（前端与快照比对依赖该稳定性）。
    查询强制带 ``workspace_id`` 条件，避免跨工作区读到他人配置。只读。
    """
    return list(ctx.db.scalars(select(DeviceQualitySource.datasource_id).where(
        DeviceQualitySource.workspace_id == ctx.workspace_id,
        DeviceQualitySource.device_id == device_id,
    ).order_by(DeviceQualitySource.created_at, DeviceQualitySource.id)))


def detector_source_ids(ctx: AuthContext, row: Device) -> set[str]:
    """The primary source remains compatible and additional QC services extend it.

    （中文补充）返回"设备可用的全部检测服务"集合 = 质检服务白名单 ∪ 设备主数据源。
    主数据源保留在内是为了兼容历史上只用主源做质检的设备；新配置应使用质检服务白名单。
    """
    return set(quality_source_ids(ctx, row.id)) | ({row.datasource_id} if row.datasource_id else set())


def get_record(ctx: AuthContext, kind: str, record_id: str, action: str = "view", *, deleted: bool = False):
    """Apply exact operation scope and all parent-resource visibility checks.

    （中文补充）按 ``kind`` 解析模型与业务域，用 ``<域>.<action>`` 权限取对象，
    并回溯父资源：devices 递归校验自身，points/rules 校验其所属设备。
    已删除对象仅在 ``deleted=True``（内部读历史快照）时可见，否则 ``NotFoundError``。
    ``action`` 直接拼进权限串，因此只接受调用方给定的固定枚举值，不要传入用户输入。
    """
    model, _, domain = DEFINITIONS[kind]
    row = owned_record(ctx, model, record_id, domain + "." + action)
    if row.status == "deleted" and not deleted:
        raise NotFoundError("此业务记录已移除")
    if kind == "devices":
        device(ctx, row.id, deleted=deleted)
    if kind in {"points", "rules"}:
        device(ctx, row.device_id, deleted=deleted)
    return row


def record_view(ctx: AuthContext, kind: str, row, *, quality_sources: list[str] | None = None) -> dict[str, Any]:
    """Return documented aliases without ORM/credential/internal ownership fields.

    （中文补充）对外序列化只取入参契约里声明过的字段（白名单），因此 ORM 上的内部字段
    （所有者、内部标记等）不会外泄；``rules`` 在模型上叫 ``rules_json``，此处做一次映射。
    两处易踩坑：
    - ``status='deleted'`` 无法通过入参校验（契约里没有该枚举），故先用 ``inactive`` 占位校验，
      再把真实值覆盖回 ``status`` 返回 —— 调用方拿到的是 ``deleted``，不是 ``inactive``。
    - ``quality_sources`` 允许调用方传入已批量查好的服务列表，避免列表接口逐设备查库；
      不传时才单设备查询。默认值语义必须保留（``None`` 表示"没预取"）。
    """
    _, schema, _ = DEFINITIONS[kind]
    values = {
        name: (quality_sources if quality_sources is not None else quality_source_ids(ctx, row.id))
        if kind == "devices" and name == "quality_datasource_ids"
        else getattr(row, "rules_json" if name == "rules" else name)
        for name in schema.model_fields
    }
    # Deleted records are only used internally for old snapshots, not input parsing.
    status = values.pop("status")
    parsed = schema.model_validate({**values, "status": "inactive" if status == "deleted" else status})
    return {**parsed.model_dump(by_alias=True, mode="json"), "status": status, "id": row.id,
            "createTime": row.created_at.isoformat(), "updateTime": row.updated_at.isoformat()}


def authorized_page(ctx: AuthContext, statement, authorize: Callable, *, limit: int, offset: int) -> list:
    """Filter referenced-resource permissions before paging without loading all rows.

    （中文补充）"先授权、后分页"的分页器：按 200 行一批向后扫描，对每行执行 ``authorize``，
    被拒绝（``NotFoundError`` / ``PermissionErrorEx``）的行直接跳过且**不计入 offset**，
    因此返回的页恰好是"用户可见记录中的第 offset..offset+limit 条"。
    代价：当用户不可见的记录很多时会多扫若干批（最坏情况接近全表），
    因此调用方应先用 SQL 把结果集收窄到该用户范围内的对象。
    退出条件：取满 limit 行、批为空、或最后一批不足 200 行；只读。
    """
    page, seen, scanned = [], 0, 0
    while len(page) < limit:
        batch = ctx.db.scalars(statement.limit(200).offset(scanned)).all()
        if not batch:
            break
        scanned += len(batch)
        for row in batch:
            try:
                authorize(row)
            except (NotFoundError, PermissionErrorEx):
                continue
            if seen >= offset:
                page.append(row)
                if len(page) == limit:
                    break
            seen += 1
        if len(batch) < 200:
            break
    return page


def list_records(ctx: AuthContext, kind: str, *, limit: int, offset: int, keyword: str = "", device_id: str = "") -> list[dict]:
    """List only current authorized records; sources are not exposed through counts.

    （中文补充）按更新时间倒序返回一页配置对象。``keyword`` 只搜名称 +
    编码/位置/类型（设备专属字段），全部使用 ``autoescape=True`` 防止 ``%``/``_`` 被当通配符。
    ``device_id`` 仅对 points/rules 生效，且会先做设备权限校验（不存在则报错而不是返回空）。
    不可见对象在分页阶段被跳过，返回条数可能与 offset 语义之外的直觉不同，属预期行为。
    """
    model, _, domain = DEFINITIONS[kind]
    query = visible_records(ctx, model, domain + ".view").where(model.status != "deleted")
    if keyword:
        query = query.where(or_(
            model.name.contains(keyword, autoescape=True),
            *(
                column.contains(keyword, autoescape=True)
                for column in (model.code, model.location, model.category)
            ) if kind == "devices" else (),
        ))
    if device_id and kind in {"points", "rules"}:
        device(ctx, device_id)
        query = query.where(model.device_id == device_id)
    rows = authorized_page(ctx, query.order_by(model.updated_at.desc(), model.id),
                           lambda row: get_record(ctx, kind, row.id), limit=limit, offset=offset)
    return [record_view(ctx, kind, row) for row in rows]


def _validate_references(ctx: AuthContext, kind: str, payload: Editable) -> None:
    """保存前的跨资源引用校验（不写库）。

    设备：主数据源须可见；质检服务须可见且 **provider 必须是 http**（外观/成分检测走 HTTP）。
    测点/策略：所属设备须可见，且策略所在设备必须启用 alert 用途。
    策略：关联经验须可见；规则项标识不得重复、标识与名称不得为空白；表达式必须合法；
    ``record_key_field`` 额外拒绝 ``__proto__``/``constructor``/``prototype`` ——
    该字段会进入取值路径，必须挡住原型污染类输入。
    校验顺序与错误信息面向业务用户，保持一致便于前端定位到具体字段。
    """
    if kind == "devices" and payload.datasource_id:
        owned_record(ctx, DataSource, payload.datasource_id, "datasources.view")
    if kind == "devices":
        for source_id in payload.quality_datasource_ids:
            source = owned_record(ctx, DataSource, source_id, "datasources.view")
            if source.provider != "http":
                raise ValidationError("外观与成分检测服务必须选择 HTTP 数据源")
    if kind in {"points", "rules"}:
        selected = device(ctx, payload.device_id)
        if kind == "rules" and "alert" not in selected.domains:
            raise ValidationError("此设备未启用预警用途")
    if kind == "rules":
        if payload.advice_id:
            get_record(ctx, "advice", payload.advice_id)
        if len({item.id for item in payload.rules}) != len(payload.rules):
            raise ValidationError("规则项标识不能重复")
        for item in payload.rules:
            if not item.id.strip() or not item.name.strip():
                raise ValidationError("规则项标识与名称不能为空白")
            validate_expression(item.expression)
        if payload.record_key_field in {"__proto__", "constructor", "prototype"}:
            raise ValidationError("问题定位字段无效")


def save_record(ctx: AuthContext, kind: str, payload: Editable, record_id: str | None = None) -> dict:
    """Save typed configuration, then append an immutable alert-policy revision.

    （中文补充）配置对象的统一写入口：``record_id`` 为空表示新建（需 ``<域>.create``），
    否则为修改（需 ``<域>.edit`` 且必须携带正确的 ``version``，过期 409）。
    顺序：权限 → 取值（剔除 ``version``，``rules`` 走 ``rules_json``）→ 结构校验
    （``validate_json``）→ 敏感字段校验（``validate_body_secrets``，防止把凭据写进配置）
    → 引用校验 → 写库并 flush → 设备侧同步质检服务关联 → 策略侧追加修订 → 审计提交。
    细节与约束：
    - 测点**不允许改用另一台设备**（只有"在目标设备新建"这一条路）。
    - 设备质检服务采用差集同步：仅删除不再选中的关联、仅新增新选中的关联，
      避免整表重建导致的其他字段历史（如 created_at 顺序）抖动。
    - 策略保存会新增 ``AlertRuleRevision``，修订号 = 更新后的 ``row.version``，
      并快照关联经验；已发布流程绑定的是**旧修订**，不受本次保存影响。
    返回 ``record_view``（已删除状态也会如实返回真实 status）。副作用：写业务表、可能写
    关联表与修订表，并写审计。
    """
    model, _, domain = DEFINITIONS[kind]
    require_permission(ctx, domain + (".edit" if record_id else ".create"))
    # quality_datasource_ids 不是设备表字段，单独走关联表同步，故从写入值中剔除。
    values = payload.model_dump(exclude={"version", "quality_datasource_ids"}, mode="json")
    validate_json(values)
    validate_body_secrets(values)
    _validate_references(ctx, kind, payload)
    if record_id:
        row = get_record(ctx, kind, record_id, "edit")
        check_version(row, payload.version)
        if kind == "points" and row.device_id != payload.device_id:
            raise ValidationError("测点不能移动到另一设备，请在目标设备新建测点")
        row.version += 1
    else:
        row = model(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    for key, value in values.items():
        # ORM 字段名与对外字段名不同：契约里叫 rules，模型上是 rules_json。
        setattr(row, "rules_json" if key == "rules" else key, value)
    flush_changes(ctx)
    if kind == "devices":
        # 差集同步：只删不选中的、只加新选中的，保留既有行（含创建时间与顺序语义）。
        current = set(quality_source_ids(ctx, row.id))
        selected = set(payload.quality_datasource_ids)
        if current - selected:
            ctx.db.execute(delete(DeviceQualitySource).where(
                DeviceQualitySource.workspace_id == ctx.workspace_id,
                DeviceQualitySource.device_id == row.id,
                DeviceQualitySource.datasource_id.in_(current - selected),
            ))
        for source_id in payload.quality_datasource_ids:
            if source_id not in current:
                ctx.db.add(DeviceQualitySource(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                               device_id=row.id, datasource_id=source_id))
        flush_changes(ctx)
    if kind == "rules":
        # 修订快照用对外视图 + 经验快照，保证历史修订可独立回读（不依赖当前关联）。
        snapshot = record_view(ctx, kind, row)
        snapshot["advice"] = record_view(ctx, "advice", get_record(ctx, "advice", row.advice_id)) if row.advice_id else None
        ctx.db.add(AlertRuleRevision(workspace_id=ctx.workspace_id, owner_user_id=row.owner_user_id,
                                    rule_id=row.id, revision=row.version, snapshot=snapshot))
    commit_change(ctx, "equipment." + kind + ".save", domain, row.id, {"version": row.version})
    return record_view(ctx, kind, row)


def remove_record(ctx: AuthContext, kind: str, record_id: str, version: int) -> None:
    """Soft-delete only; revisions, findings and handling history remain available.

    （中文补充）删除 = 置 ``status='deleted'`` 并递增版本，需要 ``<域>.delete`` 权限与正确版本。
    不物理删除：策略修订、报告、问题与处置事件仍引用该对象，历史必须可追溯；
    已删除对象默认不可见，仅内部读历史快照时（``deleted=True``）可取。
    """
    row = get_record(ctx, kind, record_id, "delete")
    check_version(row, version)
    row.status, row.version = "deleted", row.version + 1
    commit_change(ctx, "equipment." + kind + ".delete", DEFINITIONS[kind][2], row.id)


def rule_revision(ctx: AuthContext, rule_id: str, revision: int, *, active: bool = False, execute: bool = False) -> AlertRuleRevision:
    """Read an explicit immutable policy and recheck current operating permissions.

    （中文补充）按明确的修订号读取策略快照（不是"最新版"），并用**当前**权限重新校验：
    规则自身、所属设备（``active=True`` 时要求设备为 active 且仍启用 alert 用途）、
    以及快照里引用的专家经验（允许已删除，因为历史快照里已固化内容）。
    ``execute=True`` 表示即将运行，权限串用 ``alerts.execute``；否则用 ``alerts.view``。
    异常：规则/设备停用或移除、修订不存在、设备取消 alert 用途时分别抛出。
    """
    rule = get_record(ctx, "rules", rule_id, "execute" if execute else "view", deleted=not active)
    if active and rule.status != "active":
        raise ValidationError("预警规则已停用或移除，不能开始新执行")
    row = ctx.db.scalar(select(AlertRuleRevision).where(AlertRuleRevision.workspace_id == ctx.workspace_id,
                         AlertRuleRevision.rule_id == rule_id, AlertRuleRevision.revision == revision))
    if row is None:
        raise NotFoundError("指定的预警规则修订不存在")
    equipment = device(ctx, row.snapshot["deviceId"], active=active, deleted=not active)
    if active and "alert" not in equipment.domains:
        raise ValidationError("设备已取消预警用途，不能启动新预警")
    if row.snapshot.get("adviceId"):
        get_record(ctx, "advice", row.snapshot["adviceId"], deleted=True)
    return row


def list_rule_revisions(ctx: AuthContext, rule_id: str) -> list[dict]:
    """Expose only revisions whose referenced equipment/advice remain authorized.

    （中文补充）返回修订号倒序的修订列表（最多 100 条）。每条都经 ``rule_revision`` 复核，
    设备或经验已失权的修订会被跳过 —— 即"能看到策略本身"不等于"能看到它引用的设备/经验"。
    只读；``createTime`` 为修订落库时间。
    """
    get_record(ctx, "rules", rule_id)
    query = select(AlertRuleRevision).where(AlertRuleRevision.workspace_id == ctx.workspace_id,
                  AlertRuleRevision.rule_id == rule_id).order_by(AlertRuleRevision.revision.desc())
    rows = authorized_page(ctx, query, lambda row: rule_revision(ctx, rule_id, row.revision), limit=100, offset=0)
    return [{"revision": row.revision, "snapshot": row.snapshot, "createTime": row.created_at.isoformat()} for row in rows]

