"""Non-destructive adoption of existing workspace members into explicit roles.

职责：把“历史遗留的工作区成员”非破坏性地纳入显式角色体系 —— 补齐三个内置角色、为每个成员
按旧版 role 字段初始化一次角色绑定，并顺带种入品牌参数与报表提示词。

位置与依赖：不注册路由（system 模块路由前缀统一为 /api/v1/system，见 router.py /
governance_router.py），但被多处幂等调用：启动初始化、创建组织/工作区（lifecycle_service）、
OIDC 配置与登录（oidc_service）。依赖 permissions 中的三套权限集合。

关键约束：
- 幂等且“不倒退用户改动”：内置角色每次调用都会按代码里的定义重新对齐（升级后新增的模块权限
  自动补上、已下线的权限自动移除），但自定义角色与已初始化过的成员角色绑定一律不动
  （MemberProfile.roles_initialized 为 True 即跳过）。
- 成员的隐式身份只在首次迁移时映射一次：admin → workspace_admin，其余 → member；
  此后即使 WorkspaceMember.role 仍是 admin，真正生效的授权也只来自 UserRole。
- site.name 始终被写回产品名（产品名称固定，不允许被改），说明文案同时回填。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.branding import PRODUCT_NAME
from app.config import settings
from app.models import WorkspaceMember
from app.modules.system.models import MemberProfile, Role, SystemSetting, UserRole
from app.modules.system.permissions import ALL_PERMISSIONS, MEMBER_PERMISSIONS, VIEWER_PERMISSIONS


def ensure_system_roles(db: Session, workspace_id: str) -> None:
    """Create missing initial roles without reverting a user's later changes.

    用途：工作区初始化/升级时的幂等对齐逻辑（可在任意请求路径上重复调用）。
    参数：db 会话；workspace_id 目标工作区。返回：无。
    异常：无显式业务异常（唯一约束冲突会以数据库异常形式冒泡，调用方负责回滚）。
    副作用：
    1. 写入或修正 site.name（产品名固定）；
    2. 缺失时创建 workspace_admin/member/viewer 三个 builtin 角色，已存在且 builtin 时按
       permissions.py 的当前定义重写权限、数据范围、说明并强制 enabled=True；
       自定义角色（builtin=False）完全不动；
    3. 为尚未初始化过的成员创建 MemberProfile 与一条 UserRole（admin→workspace_admin，
       其余→member），并置 roles_initialized=True；
    4. 工作区有成员时种入报表提示词（owner 取第一个 admin，没有则取第一个成员），
       并按 settings.BOOTSTRAP_LYNX_REPORTS 决定是否补齐示例报表；
    5. 最后 flush（不提交，由调用方决定事务边界），因此本函数可安全地嵌在创建组织/工作区的事务里。
    权限：内部使用，调用方必须已经确认上下文（本函数不校验权限）。
    """
    brand_setting = db.scalar(select(SystemSetting).where(
        SystemSetting.workspace_id == workspace_id,
        SystemSetting.key == "site.name",
    ))
    if brand_setting is None:
        db.add(SystemSetting(
            workspace_id=workspace_id,
            key="site.name",
            value=PRODUCT_NAME,
            description="固定产品名称，用于顶栏、登录页和页面标题",
        ))
    else:
        brand_setting.value = PRODUCT_NAME
        brand_setting.description = "固定产品名称，用于顶栏、登录页和页面标题"

    # 内置角色的定义（角色编码、名称、权限集合、数据范围、说明）就是本元组，是升级对齐的唯一依据：
    # 修改这里等于改变所有工作区的内置角色，需评估对已授权用户的影响。
    # 三个角色都用 data_scope="workspace"：数据可见性由“模块动作 + 角色范围”共同决定，
    # 内置成员/查看者依靠权限集合（而非范围）限制能力。
    defaults = (
        ("workspace_admin", "工作区管理员", ALL_PERMISSIONS, "workspace", "管理本工作区的业务、成员与权限"),
        ("member", "业务成员", MEMBER_PERMISSIONS, "workspace", "继承已有成员业务权限，不授予系统管理"),
        ("viewer", "只读查看者", VIEWER_PERMISSIONS, "workspace", "查看已授权业务，不允许修改、执行或导出"),
    )
    roles = {row.code: row for row in db.scalars(select(Role).where(Role.workspace_id == workspace_id)).all()}
    for code, name, permissions, scope, description in defaults:
        if code not in roles:
            role = Role(
                workspace_id=workspace_id, code=code, name=name, permissions=sorted(permissions),
                data_scope=scope, description=description, builtin=True,
            )
            db.add(role)
            db.flush()
            roles[code] = role
        elif roles[code].builtin:
            # Built-in roles are a versioned platform contract. Keep custom
            # roles untouched, but adopt newly registered modules and remove
            # permissions that no longer exist after an upgrade.
            role = roles[code]
            role.name = name
            role.permissions = sorted(permissions)
            role.data_scope = scope
            role.description = description
            role.enabled = True
    members = db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)).all()
    # 迁移规则（只对 roles_initialized 为假的成员执行一次）：
    # - 旧版 admin 映射到内置 workspace_admin，其余一律 member（不沿用任何隐含权限）；
    # - 已经在 UserRole 里有绑定的成员只补 MemberProfile 标记，不重复插入；
    # 这样既能承接历史数据，又不会覆盖管理员后来手工调整的角色。
    for member in members:
        profile = db.scalar(select(MemberProfile).where(
            MemberProfile.workspace_id == workspace_id, MemberProfile.user_id == member.user_id,
        ))
        if profile and profile.roles_initialized:
            continue
        if profile is None:
            profile = MemberProfile(workspace_id=workspace_id, user_id=member.user_id)
            db.add(profile)
        existing = db.scalar(select(UserRole).where(
            UserRole.workspace_id == workspace_id, UserRole.user_id == member.user_id,
        ))
        if existing is None:
            role = roles["workspace_admin" if member.role == "admin" else "member"]
            db.add(UserRole(workspace_id=workspace_id, user_id=member.user_id, role_id=role.id))
        profile.roles_initialized = True
    db.flush()
    if members:
        from app.modules.reports.prompts import seed_prompts
        owner = next((member.user_id for member in members if member.role == "admin"), members[0].user_id)
        seed_prompts(db, workspace_id, owner)
        if settings.BOOTSTRAP_LYNX_REPORTS:
            from app.modules.reports.source_reports import ensure_lynx_source_reports
            ensure_lynx_source_reports(db, workspace_id, owner)
