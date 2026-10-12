"""平台外壳的导航、首页概览与接口中心导出。

职责：
  * `/api/v1/platform/navigation`：返回当前账号**已授权**的菜单项与工作区设置，供平台外壳渲染顶栏/侧栏；
  * `/api/v1/platform/overview`：首页概览的真实计数与最近记录（按数据权限过滤，**不使用演示值**）；
  * `/api/v1/platform/openapi.json`、`/api-collection`：导出真实注册接口的规范与 Postman 合集。

依赖：`modules/system/admin_service.menu_settings`（菜单目录与可见性）、`common/authorization`（权限与数据范围）。

约束：
  * 每个接口都要求对应权限码（`home.view` / `api.view`），并叠加 `allowed_owner_ids` 的数据范围过滤；
  * 导出的接口文档只含环境变量占位符，**不得**包含当前令牌、真实口令或数据库凭据；
  * 菜单项来自管理员配置的菜单目录，未授权或不可见的条目不返回（前端因此不会渲染死链接）。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select

from app.common.authorization import access_policy, allowed_owner_ids, require_permission, resource_grant_ids
from app.common.deps import AuthContext, get_current_context
from app.models import Artifact, DataSource, File, KnowledgeBase, Run
from app.modules.system.admin_service import menu_settings, workspace_settings

router = APIRouter(prefix="/api/v1/platform", tags=["平台与接口中心"])
Context = Annotated[AuthContext, Depends(get_current_context)]


# GET /api/v1/platform/navigation —— 无需额外权限码（仅返回本账号可见项，权限过滤在下方完成）
@router.get("/navigation", operation_id="platform_navigation", summary="获取当前用户的正常业务菜单")
def navigation(ctx: Context):
    """菜单按照功能授权筛选；尚未完成迁移的模块明确标记且不生成可点击死链接。"""
    # 菜单来自管理员配置（management=False 表示只取普通业务菜单），
    # 再按当前账号的权限集合过滤：未授权项不会出现在返回值里。
    policy = access_policy(ctx)
    entries = [item for item in menu_settings(ctx, management=False)
               if item["visible"] and item["permission"] in policy.permissions]
    return {
        "items": sorted(entries, key=lambda item: item["sort_order"]),
        "permissions": sorted(policy.permissions), "workspace": {"id": ctx.workspace_id, "name": ctx.workspace.name},
        "settings": {item["key"]: item["value"] for item in workspace_settings(ctx)},
    }


def _visible_statement(ctx: Context, model, permission: str, owner_field: str = "owner_user_id"):
    """构造"当前账号可见记录"的查询：工作区隔离 + 数据范围过滤。

    规则：
      * 始终限定 `workspace_id`（跨工作区不可见）；
      * 若该权限的数据范围不是"整个工作区"，则额外要求记录属于允许的 owner，
        或该记录被显式授予了当前账号（`resource_grant_ids`）；
      * `owner_field` 用于适配不同表的归属列名（如 Run 用 `started_by_user_id`）。
    """
    owners = allowed_owner_ids(ctx, permission)
    statement = select(model).where(model.workspace_id == ctx.workspace_id)
    if owners is not None:
        domain, action = permission.rsplit(".", 1)
        statement = statement.where(or_(
            getattr(model, owner_field).in_(owners), model.id.in_(resource_grant_ids(ctx, domain, action)),
        ))
    return statement


# GET /api/v1/platform/overview —— 需要 home.view；计数与最近记录均按数据范围过滤
@router.get("/overview", operation_id="platform_overview", summary="读取工作区首页的真实业务摘要")
def overview(ctx: Context):
    """所有数量和最近记录来自当前库并经过数据权限过滤，不使用演示值。"""
    require_permission(ctx, "home.view")
    policy = access_policy(ctx)
    # 指标清单：key/label/模型/权限码。缺权限的指标直接跳过（前端不显示该卡片）。
    definitions = (
        ("files", "资料文件", File, "files.view"),
        ("knowledge", "知识库", KnowledgeBase, "knowledge.view"),
        ("datasources", "数据源", DataSource, "datasources.view"),
        ("artifacts", "生成结果", Artifact, "files.view"),
    )
    metrics = []
    for key, label, model, permission in definitions:
        if permission not in policy.permissions:
            continue
        statement = _visible_statement(ctx, model, permission)
        # 已删除记录不计入统计（软删除标记因表而异）。
        if model is File:
            statement = statement.where(File.deleted_at.is_(None))
        elif model is DataSource:
            statement = statement.where(DataSource.status != "deleted")
        elif model is KnowledgeBase:
            statement = statement.where(KnowledgeBase.status != "deleted")
        count = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
        metrics.append({"key": key, "label": label, "value": count})
    recent_runs, recent_artifacts = [], []
    # 最近运行/最近产物各取 6 条；同样受数据范围限制，避免越权窥探他人记录。
    if "runs.view" in policy.permissions:
        rows = ctx.db.scalars(_visible_statement(ctx, Run, "runs.view", "started_by_user_id").order_by(Run.created_at.desc()).limit(6)).all()
        recent_runs = [{"id": row.id, "status": row.status, "created_at": row.created_at,
                        "error_summary": row.error_summary, "conversation_id": row.conversation_id} for row in rows]
    if "files.view" in policy.permissions:
        rows = ctx.db.scalars(_visible_statement(ctx, Artifact, "files.view").order_by(Artifact.created_at.desc()).limit(6)).all()
        recent_artifacts = [{"id": row.id, "name": row.name, "type": row.type, "created_at": row.created_at} for row in rows]
    # demo=False 是对外声明：本接口不返回任何演示/占位数据。
    return {"metrics": metrics, "recent_runs": recent_runs, "recent_artifacts": recent_artifacts,
            "generated_at": datetime.utcnow(), "demo": False}


# GET /api/v1/platform/openapi.json —— 需要 api.view
@router.get("/openapi.json", operation_id="platform_openapi", summary="下载当前真实注册接口的 OpenAPI 规范")
def openapi_spec(request: Request, ctx: Context):
    """需要接口中心权限；规范由实际运行路由生成，包含中文字段与稳定操作编号。"""
    require_permission(ctx, "api.view")
    return request.app.openapi()


# GET /api/v1/platform/api-collection —— 需要 api.view；导出 Postman 合集（只含占位符）
@router.get("/api-collection", operation_id="platform_api_collection", summary="下载按模块分组的 Postman 接口合集")
def api_collection(request: Request, ctx: Context):
    """只含环境变量占位符，不导出当前令牌、真实密码或数据库凭据。"""
    require_permission(ctx, "api.view")
    from app.modules.platform.openapi_contract import postman_collection
    return postman_collection(request.app.openapi())
