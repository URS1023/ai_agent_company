"""Rebuild the current actor for non-HTTP tool execution without granting new rights."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.data_scope import bind_data_scope
from app.common.deps import AuthContext
from app.errors import AuthError, PermissionErrorEx
from app.models import User, Workspace, WorkspaceMember


def auth_context_from_tool(execution_context: dict) -> AuthContext:
    """Resolve live membership and account state for every tool call.

    The context deliberately does not reuse an HTTP request's cached policy. Role
    changes, account disabling and workspace removal therefore apply to an
    already-open conversation before the business service reads any data.

    拒绝缓存的理由：长对话可能在权限被撤销后仍在执行，工具调用必须按**当前**用户
    状态、工作区成员关系重新判定，因此这里用 populate_existing 强制重读数据库。
    抛出：缺少可验证身份、用户停用/不存在、工作区不存在抛 ``AuthError``；
    用户已不属于该工作区抛 ``PermissionErrorEx``。副作用：只读，并绑定数据范围。
    """

    db = execution_context.get("db")
    workspace_id = str(execution_context.get("workspace_id") or "")
    user_id = str(execution_context.get("user_id") or "")
    if not isinstance(db, Session) or not workspace_id or not user_id:
        raise AuthError("工具缺少可验证的用户或工作区执行身份")
    user = db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    if user is None or user.status != "active":
        raise AuthError("执行用户已停用或不存在")
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id).execution_options(populate_existing=True))
    if workspace is None:
        raise AuthError("执行工作区不存在")
    membership = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id,
    ).execution_options(populate_existing=True))
    if membership is None:
        raise PermissionErrorEx("用户已不属于当前工作区")
    # 重新读取成员权限时必须保留调用方的客户端上限，不能把受限服务身份变成管理员身份。
    supplied = execution_context.get("auth_context")
    if supplied is not None and (not isinstance(supplied, AuthContext) or supplied.db is not db
            or supplied.user_id != user_id or supplied.workspace_id != workspace_id):
        raise AuthError("工具授权上下文与当前执行身份不一致")
    ctx = AuthContext(db=db, user=user, workspace=workspace, role=membership.role, token="",
        client_scopes=supplied.client_scopes if supplied is not None else None,
        client_id=supplied.client_id if supplied is not None else None)
    bind_data_scope(ctx)
    return ctx
