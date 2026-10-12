"""Scoped metric/template lifecycle and immutable inspection standards.

职责：质检"配置类对象"（指标 metrics、模板 templates）的统一服务层 —— 增删改查、
授权、以及模板修订（不可变标准）的读取与校验。

位置与依赖：被 ``quality.router``、``quality.inspections``、``quality.detection``、
``quality.concession`` 等调用；复用 ``alerts.registry`` 的设备校验与"先授权后分页"分页器，
因为设备台账由预警域持有。

对外约定：
- 权限串为 ``quality.<动作>``（view / create / edit / delete / execute）；
- 删除一律软删（``status='deleted'``），指标/模板的历史标准与原始检验记录永久保留；
- 模板保存时**在同一事务**里写入修订快照（模板 + 当时全部指标标准），
  因此历史判定口径不随指标后续修改而变；
- 模板列表/修订列表都要**逐个复核其指标可见性**，避免通过模板名探测到无权访问的指标。
"""
from typing import Any

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, ValidationError
from app.modules.alerts.registry import authorized_page, device
from app.modules.collection.transport import validate_body_secrets
from app.modules.quality.models import QualityMetric, QualityTemplate, QualityTemplateRevision
from app.modules.quality.schemas import MetricInput, MetricView, RevisionView, TemplateInput, TemplateView
from app.modules.system.service_helpers import check_version, commit_change, flush_changes

# kind → (模型, 入参契约, 输出视图)。新增可配置对象必须在此登记，否则
# record/view/save 无法解析模型、字段白名单与响应形状。
DEFINITIONS = {"metrics": (QualityMetric, MetricInput, MetricView), "templates": (QualityTemplate, TemplateInput, TemplateView)}


def record(ctx: AuthContext, kind: str, record_id: str, action: str = "view", *, active: bool = False, deleted: bool = False):
    """Check exact action/data scope; soft-deleted references remain readable in history.

    （中文补充）按 ``quality.<action>`` 权限取配置对象；``deleted=True`` 仅供历史回读
    （检验记录引用的指标/模板可能已被移除）；``active=True`` 要求对象为 active，
    否则抛"已停用，不能开始新检测"——停用是阻止新检测、不影响历史。
    """
    row = owned_record(ctx, DEFINITIONS[kind][0], record_id, "quality." + action)
    if row.status == "deleted" and not deleted:
        raise NotFoundError("质检配置已移除")
    if active and row.status != "active":
        raise ValidationError("质检指标或模板已停用，不能开始新检测")
    return row


def view(kind: str, row) -> dict[str, Any]:
    """Serialize documented config fields, not ORM internals.

    （中文补充）按入参契约声明的字段做白名单序列化，并补 ``id`` / ``createTime`` /
    ``updateTime``（驼峰别名），因此 ORM 内部字段（所有者等）不会外泄。
    不做权限判断，调用方必须已完成授权。
    """
    _, schema, response = DEFINITIONS[kind]
    fields = {key: getattr(row, key) for key in schema.model_fields}
    return response(**fields, id=row.id, createTime=row.created_at.isoformat(), updateTime=row.updated_at.isoformat()).model_dump(by_alias=True, mode="json")


def template_revision(ctx: AuthContext, template_id: str, revision: int, *, active: bool = False, execute: bool = False) -> QualityTemplateRevision:
    """Pin all metric standards; current visibility/disable rules still apply.

    （中文补充）按明确修订号读取模板快照，并逐个复核快照里引用的**指标**当前是否可见
    （``active=True`` 时还要求指标仍启用）—— 即"能看到模板"不等于"能看到它用的指标"。
    ``execute=True`` 用 ``quality.execute`` 权限（即将真实检测）；异常：模板停用/已移除、
    修订不存在、指标失权或停用时抛出。只读。
    """
    record(ctx, "templates", template_id, "execute" if execute else "view", active=active, deleted=not active)
    row = ctx.db.scalar(select(QualityTemplateRevision).where(QualityTemplateRevision.workspace_id == ctx.workspace_id,
                       QualityTemplateRevision.template_id == template_id, QualityTemplateRevision.revision == revision))
    if row is None:
        raise NotFoundError("指定质检模板修订不存在")
    for metric_id in row.snapshot["metricIds"]:
        record(ctx, "metrics", metric_id, active=active, deleted=not active)
    return row


def validate_inspection(ctx: AuthContext, config, *, active: bool = False, execute: bool = False):
    """Validate full business configuration before any upstream device request.

    （中文补充）质检执行/预检的统一入口，返回 ``(模板修订, 设备)``。
    ``active=True``（即将真实检测）时额外要求：设备必须启用 quality 用途；
    模板若声明了 ``deviceType``，设备类别必须一致（避免用错模板标准）。
    ``execute=True`` 时权限动作用 ``quality.execute``。
    约束：所有校验都在**第一次外部请求之前**完成，失败不产生外部调用与副作用。
    """
    revision = template_revision(ctx, config.templateId, config.templateRevision, active=active, execute=execute)
    equipment = device(ctx, config.deviceId, active=active, deleted=not active)
    if active and "quality" not in equipment.domains:
        raise ValidationError("设备未启用质检用途")
    category = revision.snapshot["deviceType"]
    if active and category and equipment.category != category:
        raise ValidationError("模板适用设备类别与所选设备不一致")
    return revision, equipment


def save(ctx: AuthContext, kind: str, payload, record_id: str | None = None) -> dict:
    """Persist config and a complete snapshot in the same transaction; no detection.

    （中文补充）新建需 ``quality.create``，修改需 ``quality.edit`` 且携带正确 ``version``。
    模板保存时会先取全部指标的**当前**视图（要求指标 active），再把
    ``{模板字段, metrics:[...]}`` 作为修订快照写入 ``quality_template_revisions``
    （修订号 = 更新后的模板版本），因此历史记录的标准可独立复算。
    副作用：写配置表、模板额外写修订表、写审计；**不执行任何检测、不发起外部请求**。
    """
    require_permission(ctx, "quality.edit" if record_id else "quality.create")
    values = payload.model_dump(exclude={"version"}, mode="json")
    validate_body_secrets(values)
    metrics = []
    if kind == "templates":
        metrics = [view("metrics", record(ctx, "metrics", metric_id, active=True)) for metric_id in payload.metric_ids]
    if record_id:
        row = record(ctx, kind, record_id, "edit")
        check_version(row, payload.version)
        row.version += 1
    else:
        row = DEFINITIONS[kind][0](workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    flush_changes(ctx)
    if kind == "templates":
        ctx.db.add(QualityTemplateRevision(workspace_id=ctx.workspace_id, owner_user_id=row.owner_user_id,
                   template_id=row.id, revision=row.version, snapshot={**view(kind, row), "metrics": metrics}))
    commit_change(ctx, "quality." + kind + ".save", "quality", row.id, {"version": row.version})
    return view(kind, row)


def listing(ctx: AuthContext, kind: str, *, limit: int, offset: int, keyword: str = "") -> list[dict]:
    """Page after checking nested metric visibility; do not leak hidden templates.

    （中文补充）指标直接分页；模板则对每行复核其**当前修订**里全部指标的可见性，
    不可见的模板被跳过（不计入偏移），因此不会通过模板列表暴露无权访问的指标。
    关键字只匹配名称并使用 ``autoescape``。只读。
    """
    model = DEFINITIONS[kind][0]
    query = visible_records(ctx, model, "quality.view").where(model.status != "deleted")
    if keyword:
        query = query.where(model.name.contains(keyword, autoescape=True))
    def authorize(row):
        if kind == "templates":
            template_revision(ctx, row.id, row.version)
    rows = authorized_page(ctx, query.order_by(model.updated_at.desc(), model.id), authorize, limit=limit, offset=offset)
    return [view(kind, row) for row in rows]


def revisions(ctx: AuthContext, template_id: str, *, limit: int, offset: int) -> list[RevisionView]:
    """All historical revisions are paginated and current-source authorized.

    （中文补充）按修订号倒序返回模板修订；每条都经 ``template_revision`` 复核当前权限，
    指标已失权的修订不会出现在列表里。``record(..., deleted=True)`` 允许查看已删除模板的历史修订。
    只读；``createTime`` 为修订落库时间。
    """
    record(ctx, "templates", template_id, deleted=True)
    query = select(QualityTemplateRevision).where(QualityTemplateRevision.workspace_id == ctx.workspace_id,
                QualityTemplateRevision.template_id == template_id).order_by(QualityTemplateRevision.revision.desc())
    rows = authorized_page(ctx, query, lambda row: template_revision(ctx, template_id, row.revision), limit=limit, offset=offset)
    return [RevisionView(revision=row.revision, snapshot=row.snapshot, createTime=row.created_at.isoformat()) for row in rows]


def remove(ctx: AuthContext, kind: str, record_id: str, version: int) -> None:
    """Revoke new usage; retain every standard and original inspection.

    （中文补充）软删（``quality.delete`` + 版本校验）：置 ``deleted`` 并递增版本，
    仅阻止后续使用；模板修订与历史检验记录全部保留，**不做物理删除**。
    注意删除**不会**产生新的可编辑修订（审计里不写 revision 字段）。
    """
    row = record(ctx, kind, record_id, "delete")
    check_version(row, version)
    row.status, row.version = "deleted", row.version + 1
    # Deleting a template is not a new editable revision.
    commit_change(ctx, "quality." + kind + ".remove", "quality", row.id)
