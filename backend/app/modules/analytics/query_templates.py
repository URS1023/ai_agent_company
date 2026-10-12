"""Versioned query-template CRUD; no fixed SQL against a demo database."""
from __future__ import annotations

from sqlalchemy import or_

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import ValidationError
from app.modules.analytics.models import QueryTemplate
from app.modules.analytics.schemas import QueryTemplateInput, QueryTemplateView
from app.modules.system.service_helpers import check_version, commit_change, flush_changes


def template_view(row: QueryTemplate) -> QueryTemplateView:
    """模板实体转对外视图；只回传文本与统计，不含任何执行结果。"""
    return QueryTemplateView(id=row.id, name=row.name, category=row.category, content=row.content,
                             mode=row.query_mode, parameters=row.parameters_json,
                             usageCount=row.usage_count, version=row.version)


def list_templates(ctx, keyword: str = "") -> list[QueryTemplateView]:
    """列出当前用户可见的查询模板（GET /api/v1/analytics/query-templates）。

    权限/数据范围：按 analytics.view 过滤；关键词同时匹配名称、分类与内容，
    使用 autoescape 让 % 与 _ 按字面处理。默认不返回他人不可见模板。
    """
    query = visible_records(ctx, QueryTemplate, "analytics.view")
    if keyword:
        query = query.where(or_(
            QueryTemplate.name.contains(keyword, autoescape=True),
            QueryTemplate.category.contains(keyword, autoescape=True),
            QueryTemplate.content.contains(keyword, autoescape=True),
        ))
    return [template_view(row) for row in ctx.db.scalars(query.order_by(QueryTemplate.updated_at.desc())).all()]


def save_template(ctx, payload: QueryTemplateInput, template_id: str | None = None) -> QueryTemplateView:
    """新建或按版本编辑查询模板（POST/PUT /api/v1/analytics/query-templates）。

    权限：新建需要 analytics.create，编辑需要模板的 analytics.edit 与数据范围；
    并发：编辑必须提交当前 version，过期抛 409（check_version）。
    抛出：名称或内容为空白时抛 ``ValidationError``。副作用：写模板并提交审计，
    不执行任何 SQL、不访问数据源。
    """
    if not payload.name.strip() or not payload.content.strip():
        raise ValidationError("模板名称和内容不能为空白")
    if template_id:
        row = owned_record(ctx, QueryTemplate, template_id, "analytics.edit")
        check_version(row, payload.version)
    else:
        require_permission(ctx, "analytics.create")
        row = QueryTemplate(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.name, row.category, row.content = payload.name.strip(), payload.category.strip() or "未分类", payload.content.strip()
    row.query_mode, row.parameters_json = payload.mode, payload.parameters
    flush_changes(ctx)
    commit_change(ctx, "analytics.template.save", "analytics", row.id)
    return template_view(row)


def use_template(ctx, template_id: str) -> QueryTemplateView:
    """复用模板并累加使用次数（POST /query-templates/{id}/use）。

    权限：analytics.view 与模板数据范围；只读取并记录复用，**不执行查询**——
    真正的执行会单独校验 analytics.execute。副作用：usage_count 自增并提交审计。
    """
    row = owned_record(ctx, QueryTemplate, template_id, "analytics.view")
    row.usage_count += 1
    commit_change(ctx, "analytics.template.use", "analytics", row.id)
    return template_view(row)


def delete_template(ctx, template_id: str) -> None:
    """删除模板（DELETE /query-templates/{id}），需要 analytics.delete 与数据范围。

    只删模板本身，不删除由此模板执行产生的查询历史与结果。
    """
    row = owned_record(ctx, QueryTemplate, template_id, "analytics.delete")
    ctx.db.delete(row)
    commit_change(ctx, "analytics.template.delete", "analytics", template_id)
