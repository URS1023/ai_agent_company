"""Complete prompt category/template management with scoped reuse and revisions."""
from __future__ import annotations

import json

from sqlalchemy import or_, select, update

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.modules.reports.content import RESOURCE_ROOT
from app.modules.reports.models import PromptCategory, PromptRevision, PromptTemplate
from app.modules.reports.schemas import CategoryInput, PromptInput
from app.modules.system.service_helpers import check_version, commit_change, flush_changes


def seed_prompts(db, workspace_id: str, owner_user_id: str) -> None:
    """Import missing source assets only; never overwrite customized user records.

    把随包的制造业提示词种子（分类 + 提示词）补写进该工作区并标记 ``builtin=True``：
    按名称/（分类,名称）比对已存在的内置项，只补缺失，幂等且不覆盖用户改动。
    仅由初始化流程调用，不对外暴露路由；副作用是写库 + flush（提交由调用方决定）。
    """
    seed = json.loads((RESOURCE_ROOT / "prompt_seeds.json").read_text(encoding="utf-8"))
    existing = {row.name: row for row in db.scalars(select(PromptCategory).where(
        PromptCategory.workspace_id == workspace_id, PromptCategory.builtin.is_(True),
    )).all()}
    for item in seed["categories"]:
        if item["name"] not in existing:
            row = PromptCategory(workspace_id=workspace_id, owner_user_id=owner_user_id,
                                 name=item["name"], icon=item["icon"], color=item["color"],
                                 sort_order=item["sort_order"], description="智慧云脑 Cloud Brain 制造业分析分类", builtin=True)
            db.add(row)
            db.flush()
            existing[row.name] = row
    names = {(row.category_id, row.name) for row in db.scalars(select(PromptTemplate).where(
        PromptTemplate.workspace_id == workspace_id, PromptTemplate.builtin.is_(True),
    )).all()}
    for item in seed["prompts"]:
        category = existing[item["category"]]
        if (category.id, item["name"]) not in names:
            db.add(PromptTemplate(workspace_id=workspace_id, owner_user_id=owner_user_id,
                                  category_id=category.id, name=item["name"], content=item["content"],
                                  description=item["description"], tags=item["tags"], mode="screen", builtin=True))
    db.flush()


def _visible(ctx, model):
    """构造"系统内置 + 当前数据范围内"的查询，需要 ``prompts.view``。

    内置项对所有工作区可见（``builtin=True``），自定义项走资源授权范围；分类与提示词共用这一
    口径，避免两处可见性实现漂移造成"能列出但打不开"。
    """
    require_permission(ctx, "prompts.view")
    scoped_ids = visible_records(ctx, model, "prompts.view").with_only_columns(model.id)
    return select(model).where(model.workspace_id == ctx.workspace_id, or_(model.builtin.is_(True), model.id.in_(scoped_ids)))


def category_view(row: PromptCategory) -> dict:
    """分类对外视图；内置项 ``createdBy`` 固定为 0，前端据此隐藏编辑/删除入口。"""
    return {"id": row.id, "name": row.name, "description": row.description, "icon": row.icon, "color": row.color,
            "sortOrder": row.sort_order, "createdBy": 0 if row.builtin else row.owner_user_id,
            "builtin": row.builtin, "version": row.version, "enabled": row.enabled}


def prompt_view(row: PromptTemplate) -> dict:
    """提示词对外视图；``useCount`` 只是复用计数，不代表内容版本或权限。"""
    return {"id": row.id, "categoryId": row.category_id, "name": row.name, "content": row.content,
            "description": row.description, "tags": row.tags, "mode": row.mode, "status": row.status,
            "useCount": row.usage_count, "createdBy": 0 if row.builtin else row.owner_user_id,
            "builtin": row.builtin, "version": row.version, "updateTime": row.updated_at.isoformat()}


def list_categories(ctx) -> list[dict]:
    """列出可用分类（内置 + 自定义，排除停用项），按排序号与名称排序；需 ``prompts.view``。"""
    return [category_view(row) for row in ctx.db.scalars(_visible(ctx, PromptCategory).where(
        PromptCategory.enabled.is_(True),
    ).order_by(PromptCategory.sort_order, PromptCategory.name)).all()]


def get_category(ctx, category_id: str) -> PromptCategory:
    """按 ID 读取可见且启用的分类；不存在或不可见统一抛 ``NotFoundError``（不泄漏存在性）。"""
    row = ctx.db.scalar(_visible(ctx, PromptCategory).where(PromptCategory.id == category_id, PromptCategory.enabled.is_(True)))
    if row is None:
        raise NotFoundError("提示词分类不存在或不可访问")
    return row


def save_category(ctx, payload: CategoryInput, category_id: str | None = None) -> dict:
    """新建（需 ``prompts.create``）或按版本更新（需 ``prompts.edit``）提示词分类。

    内置分类只读，尝试修改抛权限错误；更新必须提交正确 ``version``（并发修改返回 409）。
    副作用：写库 + 审计事件。返回分类视图。
    """
    if not payload.name.strip():
        raise ValidationError("分类名称不能为空白")
    if category_id:
        row = owned_record(ctx, PromptCategory, category_id, "prompts.edit")
        if row.builtin:
            raise PermissionErrorEx("内置分类不能直接修改")
        check_version(row, payload.version)
    else:
        require_permission(ctx, "prompts.create")
        row = PromptCategory(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.name, row.description, row.icon = payload.name.strip(), payload.description, payload.icon
    row.color, row.sort_order = payload.color, payload.sort_order
    flush_changes(ctx)
    commit_change(ctx, "reports.prompt_category.save", "prompts", row.id)
    return category_view(row)


def remove_category(ctx, category_id: str) -> None:
    """移除自定义分类（需 ``prompts.delete``）：置 ``enabled=False`` 的软删除。

    分类下仍有未删除提示词时抛 ``ConflictError``（409），避免产生孤立提示词；
    内置分类不可删除。
    """
    row = owned_record(ctx, PromptCategory, category_id, "prompts.delete")
    if row.builtin:
        raise PermissionErrorEx("内置分类不能删除")
    if ctx.db.scalar(select(PromptTemplate.id).where(
        PromptTemplate.workspace_id == ctx.workspace_id, PromptTemplate.category_id == category_id, PromptTemplate.status != "deleted",
    ).limit(1)):
        raise ConflictError("分类仍有提示词，请先移动或移除这些提示词")
    row.enabled = False
    commit_change(ctx, "reports.prompt_category.delete", "prompts", category_id)


def list_prompts(ctx, *, category_id: str | None = None, keyword: str = "", mode: str | None = None) -> list[dict]:
    """搜索/筛选可见（status=active）的提示词；需 ``prompts.view``。

    ``keyword`` 在名称、正文、说明、标签四列上做转义后的包含匹配；``mode`` 区分
    screen/widget/query 场景；指定 ``category_id`` 时会先校验分类可见性。按使用次数与
    更新时间倒序，便于复用高频提示词。
    """
    query = _visible(ctx, PromptTemplate).where(PromptTemplate.status == "active")
    if category_id:
        get_category(ctx, category_id)
        query = query.where(PromptTemplate.category_id == category_id)
    if keyword:
        query = query.where(or_(*[getattr(PromptTemplate, field).contains(keyword, autoescape=True)
                                 for field in ("name", "content", "description", "tags")]))
    if mode:
        query = query.where(PromptTemplate.mode == mode)
    return [prompt_view(row) for row in ctx.db.scalars(query.order_by(PromptTemplate.usage_count.desc(), PromptTemplate.updated_at.desc())).all()]


def get_prompt(ctx, prompt_id: str) -> PromptTemplate:
    """读取可见且未删除的提示词；不存在或不可见统一抛 ``NotFoundError``。"""
    row = ctx.db.scalar(_visible(ctx, PromptTemplate).where(PromptTemplate.id == prompt_id, PromptTemplate.status == "active"))
    if not row:
        raise NotFoundError("提示词不存在或不可访问")
    return row


def save_prompt(ctx, payload: PromptInput, prompt_id: str | None = None) -> dict:
    """新建或修改提示词（新建需 ``prompts.create``，修改需 ``prompts.edit``）。

    内置提示词只允许复制、不可覆盖；修改必须提交正确 ``version``。每次修改递增版本并追加一条
    内容修订快照，因此历史生成可以追溯到当时用的正文。正文只作为文本保存，其中的工具或权限
    指令不会被执行。返回修改后的视图（也是写入修订的快照）。
    """
    if not payload.name.strip() or not payload.content.strip():
        raise ValidationError("提示词名称与正文不能为空白")
    get_category(ctx, payload.category_id)
    if prompt_id:
        row = owned_record(ctx, PromptTemplate, prompt_id, "prompts.edit")
        if row.builtin:
            raise PermissionErrorEx("内置提示词只能复制，不可覆盖")
        check_version(row, payload.version)
    else:
        require_permission(ctx, "prompts.create")
        row = PromptTemplate(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.category_id, row.name, row.content = payload.category_id, payload.name.strip(), payload.content.strip()
    row.description, row.tags, row.mode = payload.description, payload.tags, payload.mode
    if prompt_id:
        row.version += 1
    flush_changes(ctx)
    snapshot = prompt_view(row)
    ctx.db.add(PromptRevision(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                              prompt_id=row.id, revision=row.version, snapshot=snapshot))
    commit_change(ctx, "reports.prompt.save", "prompts", row.id)
    return snapshot


def use_prompt(ctx, prompt_id: str) -> dict:
    """复用提示词：只把使用次数 +1（原子更新）并记录审计，不产生内容修订。

    计数不改变内容版本，因此正在编辑该提示词的人不会因计数更新而冲突；返回刷新后的视图。
    """
    row = get_prompt(ctx, prompt_id)
    # A usage counter does not create a content revision or invalidate editors.
    ctx.db.execute(update(PromptTemplate).where(
        PromptTemplate.workspace_id == ctx.workspace_id, PromptTemplate.id == row.id,
    ).values(usage_count=PromptTemplate.usage_count + 1).execution_options(synchronize_session=False))
    commit_change(ctx, "reports.prompt.use", "prompts", row.id, {"prompt_version": row.version})
    ctx.db.refresh(row)
    return prompt_view(row)


def remove_prompt(ctx, prompt_id: str) -> None:
    """删除自定义提示词（需 ``prompts.delete``）：``status="deleted"`` 软删除。

    内置提示词不可删除；已使用过的内容版本仍然保留，以便追溯历史生成用的是哪一版正文。
    """
    row = owned_record(ctx, PromptTemplate, prompt_id, "prompts.delete")
    if row.builtin:
        raise PermissionErrorEx("内置提示词不能删除")
    row.status = "deleted"
    commit_change(ctx, "reports.prompt.delete", "prompts", prompt_id)


def prompt_revisions(ctx, prompt_id: str) -> list[dict]:
    """倒序返回提示词的内容修订（不可变快照）；先校验提示词可见性。"""
    get_prompt(ctx, prompt_id)
    return [{"revision": row.revision, "snapshot": row.snapshot, "createTime": row.created_at.isoformat()}
            for row in ctx.db.scalars(select(PromptRevision).where(
                PromptRevision.workspace_id == ctx.workspace_id, PromptRevision.prompt_id == prompt_id,
            ).order_by(PromptRevision.revision.desc())).all()]
