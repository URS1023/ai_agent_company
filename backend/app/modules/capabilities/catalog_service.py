"""Category and method governance layered over the executable tool registry.

中文补充（工具目录治理服务）：
- 职责：在"可执行工具注册表"（capabilities/service._iter_visible_tools）之上叠加
  分类与启停治理，并提供列表/导出所需的数据形状；不复制工具定义、不执行工具。
- 权限码：查看 capabilities.view、新建 capabilities.create、修改 capabilities.edit、
  归档 capabilities.delete、导出 capabilities.export；每个入口都先 require_permission。
- 版本化：分类与方法的修改都要求携带 version（check_version 做乐观锁），
  未创建过元数据的工具首次修改必须传 version=0，否则报冲突要求刷新。
- 所有变更都经 commit_change 写操作日志（审计留痕），不做物理删除。
"""
from __future__ import annotations

import csv
import io
import heapq

from sqlalchemy import func, select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, NotFoundError, ValidationError
from app.ids import new_id
from app.modules.system.service_helpers import check_version, commit_change

from .models import ToolCategory, ToolMethodMetadata
from .schemas import ToolCategoryInput, ToolCategoryPatch, ToolMethodPatch


def _category(ctx: AuthContext, category_id: str) -> ToolCategory:
    """按 ID 取当前工作区的有效分类，不存在则抛 NotFoundError。

    查询同时限定 workspace_id 与 status=active：跨工作区 ID、或已归档的分类
    对外一律表现为"不存在"，不泄露其它工作区的数据。
    """
    row = ctx.db.scalar(select(ToolCategory).where(
        ToolCategory.id == category_id,
        ToolCategory.workspace_id == ctx.workspace_id,
        ToolCategory.status == "active",
    ))
    if row is None:
        raise NotFoundError("工具分类不存在")
    return row


def _category_view(row: ToolCategory, paths: dict[str, str], counts: dict[str, int]) -> dict:
    """分类的对外视图（camelCase 字段名与前端契约保持一致）。

    path 为完整层级路径、methodCount 为该分类下已登记方法数；两者由调用方批量
    计算后传入，避免逐行查库（N+1）。
    """
    return {
        "id": row.id,
        "parentId": row.parent_id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "sortOrder": row.sort_order,
        "enabled": row.enabled,
        "status": row.status,
        "version": row.version,
        "path": paths.get(row.id, row.name),
        "methodCount": counts.get(row.id, 0),
        "updateTime": row.updated_at.isoformat() if row.updated_at else None,
    }


def _paths(rows: list[ToolCategory]) -> dict[str, str]:
    """批量计算"分类 ID → 完整路径"（如 ``财务 / 报表``），带缓存与环保护。

    ``pending`` 集合用于打断互相指向的父子关系：遇到环时退回本级名称而不是
    无限递归——历史数据可能因人工改库产生环，列表接口不能因此 500。
    """
    by_id = {item.id: item for item in rows}
    cache: dict[str, str] = {}

    def path_for(item: ToolCategory, pending: set[str] | None = None) -> str:
        if item.id in cache:
            return cache[item.id]
        pending = set(pending or ())
        if item.id in pending:
            return item.name
        pending.add(item.id)
        parent = by_id.get(item.parent_id or "")
        cache[item.id] = f"{path_for(parent, pending)} / {item.name}" if parent else item.name
        return cache[item.id]

    for row in rows:
        path_for(row)
    return cache


def list_categories(ctx: AuthContext) -> dict:
    """分类树列表（按完整路径 + 排序号排列），含每个分类下的方法数。

    权限：capabilities.view。只返回本工作区 active 分类；已归档分类不再出现。
    计数用一次 group by 聚合完成，排序在内存中按路径进行以体现层级顺序。
    """
    require_permission(ctx, "capabilities.view")
    rows = list(ctx.db.scalars(select(ToolCategory).where(
        ToolCategory.workspace_id == ctx.workspace_id,
        ToolCategory.status == "active",
    ).order_by(ToolCategory.sort_order, ToolCategory.name, ToolCategory.id)).all())
    counts = dict(ctx.db.execute(select(
        ToolMethodMetadata.category_id, func.count(ToolMethodMetadata.id),
    ).where(
        ToolMethodMetadata.workspace_id == ctx.workspace_id,
        ToolMethodMetadata.category_id.is_not(None),
    ).group_by(ToolMethodMetadata.category_id)).all())
    paths = _paths(rows)
    rows.sort(key=lambda item: (paths[item.id], item.sort_order, item.id))
    return {"items": [_category_view(item, paths, counts) for item in rows], "total": len(rows)}


def create_category(ctx: AuthContext, payload: ToolCategoryInput) -> dict:
    """新建分类；权限 capabilities.create。

    父分类必须存在且属于本工作区；同一工作区 code 与同级 name 由数据库唯一约束
    保证（冲突会由 commit_change/约束报错）。返回新建分类的完整视图。
    副作用：写库 + 写审计日志。
    """
    require_permission(ctx, "capabilities.create")
    if payload.parent_id:
        _category(ctx, payload.parent_id)
    row = ToolCategory(
        id=new_id(),
        workspace_id=ctx.workspace_id,
        parent_id=payload.parent_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        sort_order=payload.sort_order,
        enabled=payload.enabled,
        status="active",
        created_by_user_id=ctx.user_id,
        updated_by_user_id=ctx.user_id,
    )
    ctx.db.add(row)
    commit_change(ctx, "capability.category.create", "tool_category", row.id, {"code": row.code})
    items = list_categories(ctx)["items"]
    return next(item for item in items if item["id"] == row.id)


def _validate_parent(ctx: AuthContext, row: ToolCategory, parent_id: str | None) -> None:
    """校验新父节点合法：不能指向自己，也不能形成祖先环。

    沿父链向上逐级查询；只要回到自身即为环，抛 ValidationError。
    历史数据可能已存在环，这里用 seen 集合兜底而不是无界循环。
    """
    if not parent_id:
        return
    if parent_id == row.id:
        raise ValidationError("工具分类不能把自己设为上级")
    parent = _category(ctx, parent_id)
    seen = {row.id}
    while parent:
        if parent.id in seen:
            raise ValidationError("工具分类不能形成循环层级")
        seen.add(parent.id)
        parent = _category(ctx, parent.parent_id) if parent.parent_id else None


def update_category(ctx: AuthContext, category_id: str, payload: ToolCategoryPatch) -> dict:
    """修改分类（全量字段覆盖）；权限 capabilities.edit。

    先做版本乐观锁（check_version），再校验父节点层级，避免并发改动互相覆盖或
    产生循环；status 不在此处变更（归档走 archive_category）。
    副作用：写库 + 审计日志，返回更新后的分类视图。
    """
    require_permission(ctx, "capabilities.edit")
    row = _category(ctx, category_id)
    check_version(row, payload.version)
    _validate_parent(ctx, row, payload.parent_id)
    row.parent_id = payload.parent_id
    row.code = payload.code
    row.name = payload.name
    row.description = payload.description
    row.sort_order = payload.sort_order
    row.enabled = payload.enabled
    row.updated_by_user_id = ctx.user_id
    commit_change(ctx, "capability.category.update", "tool_category", row.id, {"code": row.code})
    return next(item for item in list_categories(ctx)["items"] if item["id"] == row.id)


def archive_category(ctx: AuthContext, category_id: str, version: int) -> dict:
    """归档分类（软删除）；权限 capabilities.delete。

    前置条件：没有 active 子分类、也没有任何方法挂在该分类下，否则抛 ConflictError，
    避免把在用工具变成悬挂状态。归档时同时置 enabled=False，并保留历史记录
    （日志里标记 retainedHistory），不做物理删除。
    异常：version 不匹配抛冲突；分类不存在抛 NotFoundError。
    """
    require_permission(ctx, "capabilities.delete")
    row = _category(ctx, category_id)
    check_version(row, version)
    child = ctx.db.scalar(select(ToolCategory.id).where(
        ToolCategory.workspace_id == ctx.workspace_id,
        ToolCategory.parent_id == row.id,
        ToolCategory.status == "active",
    ).limit(1))
    if child:
        raise ConflictError("请先移动或删除下级工具分类")
    assigned = ctx.db.scalar(select(ToolMethodMetadata.id).where(
        ToolMethodMetadata.workspace_id == ctx.workspace_id,
        ToolMethodMetadata.category_id == row.id,
    ).limit(1))
    if assigned:
        raise ConflictError("该分类仍有工具方法，请先调整方法分类")
    row.status = "archived"
    row.enabled = False
    row.updated_by_user_id = ctx.user_id
    commit_change(ctx, "capability.category.archive", "tool_category", row.id, {"retainedHistory": True})
    return {"id": row.id, "version": row.version, "deleted": True}


def _available_tools(ctx: AuthContext, *, provider: str = ""):
    """治理视角的工具集合：包含已停用工具（include_disabled=True）。

    与模型侧列表（只给启用工具）的差别是本模块存在的意义：管理台必须能看到并
    重新启用被停用的方法。权限与数据范围过滤仍在 service 层完成。
    """
    from .service import _iter_visible_tools
    return _iter_visible_tools(ctx, include_disabled=True, provider=provider)


def list_methods(
    ctx: AuthContext, *, keyword: str = "", provider: str = "", category_id: str = "",
    enabled: bool | None = None, limit: int | None = None, offset: int = 0,
) -> dict:
    """工具方法列表（含治理元数据）；权限 capabilities.view。

    参数：keyword 命中名称/展示名/描述/code/分类路径/标签；provider、category_id
    为精确筛选；``enabled=None`` 表示不过滤启停状态。
    性能：``limit=None`` 时（导出场景）遍历全部匹配项；交互式请求用
    heapq.nsmallest 只保留前 offset+limit 条，避免把整个注册表排序装载。
    返回：items + total（匹配总数）+ 回显的 limit/offset。
    """
    require_permission(ctx, "capabilities.view")
    needle = keyword.strip().lower()
    total = 0

    def matched():
        nonlocal total
        for item in _available_tools(ctx, provider=provider):
            if needle and needle not in " ".join([
                item.get("name", ""), item.get("displayName", ""), item.get("description", ""),
                item.get("code", ""), item.get("categoryPath", ""), " ".join(item.get("tags") or []),
            ]).lower():
                continue
            if category_id and item.get("categoryId") != category_id:
                continue
            if enabled is not None and bool(item.get("enabled")) is not enabled:
                continue
            total += 1
            yield item

    sort_key = lambda item: (item.get("categoryPath") or "~", item.get("displayName") or item["name"], item["name"], item.get("connectorId") or "")
    # Export intentionally visits every match. Interactive requests retain only
    # the first offset+limit matching records, never the whole registry payload.
    items = sorted(matched(), key=sort_key) if limit is None else heapq.nsmallest(offset + limit, matched(), key=sort_key)
    if limit is not None:
        offset = min(offset, max(0, (total - 1) // limit * limit))
        items = items[offset:offset + limit]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _method_row(ctx: AuthContext, tool_name: str) -> ToolMethodMetadata | None:
    """按工具名取治理元数据行；未治理过的工具返回 None（调用方按"首次创建"处理）。"""
    return ctx.db.scalar(select(ToolMethodMetadata).where(
        ToolMethodMetadata.workspace_id == ctx.workspace_id,
        ToolMethodMetadata.tool_name == tool_name,
    ))


def _apply_method_patch(ctx: AuthContext, tool_name: str, payload: ToolMethodPatch) -> ToolMethodMetadata:
    """新建或更新一条方法治理元数据（不提交事务，由调用方 commit_change 统一提交）。

    校验链：工具必须真实存在且在数据范围内（否则 NotFoundError）→ 目标分类必须
    存在且处于启用状态 → 已有记录做版本乐观锁、无记录时要求 payload.version == 0
    （防止前端基于旧列表覆盖他人新建的治理行）。
    注意：这里只改治理字段，不触碰工具定义本身。
    """
    if not any(item["name"] == tool_name for item in _available_tools(ctx)):
        raise NotFoundError("工具方法不存在或不在当前数据范围内")
    category = _category(ctx, payload.category_id) if payload.category_id else None
    if category and not category.enabled:
        raise ValidationError("不能把工具方法分配到已停用分类")
    row = _method_row(ctx, tool_name)
    if row is None:
        if payload.version != 0:
            raise ConflictError("工具方法元数据尚未创建，请刷新后重试", current_version=0)
        row = ToolMethodMetadata(
            id=new_id(),
            workspace_id=ctx.workspace_id,
            tool_name=tool_name,
            code=payload.code or tool_name,
            category_id=payload.category_id,
            display_name=payload.display_name,
            description=payload.description,
            enabled=payload.enabled,
            tags=payload.tags,
            created_by_user_id=ctx.user_id,
            updated_by_user_id=ctx.user_id,
        )
        ctx.db.add(row)
    else:
        check_version(row, payload.version)
        row.category_id = payload.category_id
        row.code = payload.code or tool_name
        row.display_name = payload.display_name
        row.description = payload.description
        row.enabled = payload.enabled
        row.tags = payload.tags
        row.updated_by_user_id = ctx.user_id
    return row


def update_method(ctx: AuthContext, tool_name: str, payload: ToolMethodPatch) -> dict:
    """更新单个工具方法的治理信息（含启停）；权限 capabilities.edit。

    停用后该方法不再进入 AI/工作流的新调用（ToolGateway 实时读 enabled 列）。
    副作用：写库 + 审计日志（记录工具名、enabled、分类），返回治理后的工具视图。
    """
    require_permission(ctx, "capabilities.edit")
    row = _apply_method_patch(ctx, tool_name, payload)
    commit_change(ctx, "capability.method.update", "tool_method", row.id, {
        "tool": tool_name, "enabled": row.enabled, "categoryId": row.category_id,
    })
    return next(item for item in _available_tools(ctx) if item["name"] == tool_name)


def batch_update_methods(ctx: AuthContext, items) -> dict:
    """原子批量更新多个工具方法；权限 capabilities.edit。

    先拒绝重复工具名（同一批里改两次会互相覆盖），再逐条应用改动；全部成功后才
    统一提交与写一条批量审计日志——任一条失败都会整体回滚，不留部分生效状态。
    """
    require_permission(ctx, "capabilities.edit")
    if len({item.tool_name for item in items}) != len(items):
        raise ValidationError("批量方法不能包含重复工具名")
    rows = [_apply_method_patch(ctx, item.tool_name, item) for item in items]
    commit_change(ctx, "capability.method.batch_update", "tool_method", "batch", {
        "count": len(rows), "tools": [item.tool_name for item in items],
    })
    current = {item["name"]: item for item in _available_tools(ctx)}
    return {"items": [current[item.tool_name] for item in items], "count": len(items)}


def categories_csv(ctx: AuthContext) -> str:
    """导出分类目录为 CSV 文本；权限 capabilities.export。

    返回不含 BOM 的纯文本，BOM 由路由层添加（保持 service 输出可直接用于测试）。
    列为固定契约，前端与客户脚本按列名解析，增删列属于对外变更。
    """
    require_permission(ctx, "capabilities.export")
    rows = list_categories(ctx)["items"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["code", "name", "path", "parent_id", "enabled", "method_count", "description"])
    for item in rows:
        writer.writerow([item["code"], item["name"], item["path"], item["parentId"] or "", item["enabled"], item["methodCount"], item["description"]])
    return output.getvalue()


def methods_csv(ctx: AuthContext) -> str:
    """导出工具方法目录为 CSV 文本；权限 capabilities.export。

    导出不分页（list_methods 不传 limit），因此会遍历全部可见方法；
    标签以逗号连接为单列，便于在表格软件中再拆分。
    """
    require_permission(ctx, "capabilities.export")
    rows = list_methods(ctx)["items"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["tool_name", "code", "display_name", "provider", "category", "enabled", "tags", "description"])
    for item in rows:
        writer.writerow([
            item["name"], item["code"], item["displayName"], item["provider"], item["categoryPath"],
            item["enabled"], ",".join(item["tags"]), item["description"],
        ])
    return output.getvalue()
