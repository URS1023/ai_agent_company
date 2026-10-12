# -*- coding: utf-8 -*-
"""Bootstrap script: create initial organization, workspace and admin if absent.

Idempotent — safe to run on every startup.

职责：服务启动时的最小可用初始化——建结构、补默认组织/工作区/管理员、登记系统角色与
默认 PPT 插件能力。

系统位置：由 app.main 的 lifespan 启动阶段调用（也可 `python -m app.seed` 手动执行），
必须在接受流量前完成，因此这里抛错等于启动失败。

关键约束：
- 幂等：所有步骤都先查后建，重复启动不会产生重复组织/工作区/成员关系；已有口令与锁定状态
  属于用户资产，启动流程不得覆盖或重置。
- 首个管理员账号固定为 workspace-admin，初始口令来自 BOOTSTRAP_ADMIN_PASSWORD
  （见 config：默认值仅限本地开发，部署必须覆盖）。
- 本模块只做"补齐缺省"，不做数据修复或结构变更（结构交给 app.schema.initialize_schema）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from .config import settings
from .db import new_session
from .ids import new_id
from .models import (
    AuthIdentity, LocalCredential, Organization, OrganizationMember,
    User, Workspace, WorkspaceMember,
)
from .security import hash_password


def _first(db, stmt):
    """取查询结果的第一条（无结果返回 None）：本模块所有"是否已存在"判断都基于它。"""
    return db.execute(stmt).scalars().first()


def _ensure_admin(db) -> str:
    """确保存在默认组织、默认工作区与 workspace-admin 管理员，返回该用户 id。

    行为：按 created_at 取最早的组织/工作区作为默认归属；不存在则创建。管理员身份按
    (provider=local, username_normalized=workspace-admin) 唯一确认，凭据仅在缺失时写入。
    副作用：写库（含新建成员关系），函数内部自行 commit；调用方复用同一个 session 时
    需注意 commit 已经发生。
    约束：只有当身份是本次新建时才会补齐工作区/组织成员关系——避免把被人工移除的成员关系
    在每次启动时又加回来。
    """
    org = _first(db, select(Organization).order_by(Organization.created_at))
    if not org:
        org = Organization(id=new_id(), name="默认组织", created_at=datetime.utcnow())
        db.add(org)
        db.flush()
    org_id = org.id

    ws = _first(db, select(Workspace).order_by(Workspace.created_at))
    if not ws:
        ws = Workspace(id=new_id(), organization_id=org_id, name="默认工作区", created_at=datetime.utcnow())
        db.add(ws)
        db.flush()
    ws_id = ws.id

    # 用户名规范化后作为登录标识；历史数据可能是其他大小写/空白形式，这里固定字面量比较。
    username_norm = "workspace-admin"
    user = None
    identity = _first(db, select(AuthIdentity).where(
        AuthIdentity.provider == "local", AuthIdentity.username_normalized == username_norm))
    created_identity = identity is None
    if identity:
        user = db.get(User, identity.user_id)
        if not user:
            # 身份存在但用户行缺失（历史数据不完整）：补建用户，保持 id 一致。
            user = User(id=identity.user_id, display_name="系统管理员", status="active",
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
            db.add(user)
    else:
        user_id = new_id()
        user = User(id=user_id, display_name="系统管理员", status="active",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add(user)
        db.flush()
        identity = AuthIdentity(id=new_id(), user_id=user.id, provider="local",
                                username_normalized=username_norm, created_at=datetime.utcnow())
        db.add(identity)
        db.flush()

    # An existing password and its lock state belong to the user, not startup.
    cred = _first(db, select(LocalCredential).where(LocalCredential.auth_identity_id == identity.id))
    if not cred:
        cred = LocalCredential(id=new_id(), auth_identity_id=identity.id,
                               password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                               must_change_password=False, failed_attempt_count=0, credential_version=1,
                               password_changed_at=datetime.utcnow())
        db.add(cred)

    memberships = db.execute(select(WorkspaceMember).where(
        WorkspaceMember.user_id == user.id)).scalars().all()
    has_membership = any(m.workspace_id == ws_id for m in memberships)
    if not has_membership and created_identity:
        db.add(WorkspaceMember(id=new_id(), workspace_id=ws_id, user_id=user.id,
                               role="admin", created_at=datetime.utcnow()))
    om = _first(db, select(OrganizationMember).where(
        OrganizationMember.organization_id == org_id, OrganizationMember.user_id == user.id))
    if not om and created_identity:
        db.add(OrganizationMember(id=new_id(), organization_id=org_id, user_id=user.id,
                                  role="admin", created_at=datetime.utcnow()))
    db.commit()
    return user.id


def _ensure_default_ppt_plugin(db, workspace_id: str, user_id: str) -> str:
    """Ensure the composer's default PPT capability is a real published Plugin.

    用途：保证对话编排器依赖的"PPT 生成"插件存在且为已发布状态，返回插件 id。
    行为：不存在则创建并发布；已存在但没有已发布版本则发布；已发布版本缺少必需工具时
    新建草稿版本再发布（把 desired_spec 作为期望状态对齐）。
    副作用：写库（可能新增插件/版本记录），由调用方所在事务统一提交。
    """
    from .models import Plugin, PluginVersion
    from .modules.plugin import service as plugin_service

    # desired_spec 是"期望状态"：它同时决定工具清单（tool_refs）与对外 schema，
    # 修改这里的任何字段都会触发上面的对齐逻辑重新生成草稿版本。
    desired_spec = {
        "name": "PPT 生成",
        "description": "选择模板并把对话内容分阶段生成可下载的 PPTX 演示文稿。",
        "instructions": (
            "先确认主题与材料，读取用户附加文件；调用 create_presentation_plan 创建页面大纲；"
            "再分批调用 write_presentation_slides 写入详细正文，最后调用 finalize_presentation；"
            "始终继承会话中选择的 template_id、页面比例与总页数；"
            "正文页必须包含具体结论、证据和解释，禁止只生成标题或空框。"
        ),
        "tool_refs": [
            "create_presentation_plan",
            "write_presentation_slides",
            "finalize_presentation",
        ],
        "input_schema": {"type": "object", "properties": {
            "topic": {"type": "string"}, "template_id": {"type": "string"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "4:3"]},
            "page_count": {"type": "integer", "minimum": 3, "maximum": 50}},
            "required": ["topic", "template_id"]},
        "output_schema": {"type": "object", "properties": {
            "artifact_id": {"type": "string"}, "download_url": {"type": "string"}}},
        "permissions": {"artifact.create": True}, "risk_level": "low",
    }
    plugin = _first(db, select(Plugin).where(
        Plugin.workspace_id == workspace_id, Plugin.name == "PPT 生成"))
    if not plugin:
        result = plugin_service.create_plugin(
            db, workspace_id=workspace_id, owner_user_id=user_id,
            name="PPT 生成", description="选择模板并把对话内容生成可下载的 PPTX 演示文稿。",
            spec=desired_spec)
        plugin_id = result["plugin_id"]
        plugin_service.publish(db, plugin_id)
        return plugin_id
    if not plugin.published_version_id:
        plugin_service.publish(db, plugin.id)
    # 只比对工具名集合：说明文案变化不触发新版本，避免每次启动都堆出版本记录。
    published = db.get(PluginVersion, plugin.published_version_id)
    active_tools = {
        ref.get("tool_name") for ref in ((published.spec or {}).get("tool_refs") or [])
        if isinstance(ref, dict)
    } if published else set()
    required_tools = set(desired_spec["tool_refs"])
    if not required_tools.issubset(active_tools):
        plugin_service.new_draft_version(
            db, plugin.id, user_id, desired_spec, workspace_id
        )
        plugin_service.publish(db, plugin.id)
    return plugin.id


def bootstrap() -> dict:
    """启动引导总入口：建结构 → 补管理员 → 补系统角色与生命周期档案 → 补默认插件。

    返回：{"admin_user_id", "admin_username", "ppt_plugin_id"}（工作区缺失时插件 id 为 None）。
    异常：结构校验失败或建数据失败都会向上抛出（调用方是 lifespan，等于启动失败）。
    副作用：建表/写库；内部自建会话并保证关闭，调用方无需传 session。
    约束：可重复执行；不覆盖既有凭据、不重置用户数据。手动执行 `python -m app.seed` 亦走此函数。
    """
    from app.schema import initialize_schema

    initialize_schema()
    session = new_session()
    try:
        uid = _ensure_admin(session)
        ws = _first(session, select(Workspace).order_by(Workspace.created_at))
        from app.modules.system.bootstrap import ensure_system_roles
        # 每个工作区都要有系统内置角色，因此遍历全部工作区而不是只处理默认工作区。
        for workspace in session.scalars(select(Workspace)).all():
            ensure_system_roles(session, workspace.id)
        from app.modules.system.lifecycle_service import ensure_all_lifecycle_profiles
        ensure_all_lifecycle_profiles(session)
        ppt_plugin_id = _ensure_default_ppt_plugin(session, ws.id, uid) if ws else None
        session.commit()
        return {"admin_user_id": uid, "admin_username": "workspace-admin",
                "ppt_plugin_id": ppt_plugin_id}
    finally:
        session.close()


if __name__ == "__main__":
    print(bootstrap())
