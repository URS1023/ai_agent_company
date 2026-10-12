"""Shared, default-deny action and resource authorization for all entry points.

职责：本模块是后端唯一的"动作授权 + 资源归属"判定中心，回答两个问题——
- 能做什么动作：``require_permission(ctx, "<域>.<动作>")``，权限码必须已登记在
  ``modules/system/permissions.ALL_PERMISSIONS``；
- 能在谁的数据上做：``allowed_owner_ids`` / ``permitted_department_ids`` /
  ``allowed_department_ids`` / ``require_resource`` 按"角色 + 数据范围 + 显式资源授权"裁剪。

系统位置与判定链（四段式，缺一段都不完整）：

    common.deps（认证：Bearer / 内容 Cookie）→ 本模块（动作权限、资源归属）
    → common.data_scope（把同一套判定下沉为 SQLAlchemy 查询条件）
    → common.owned_records（按 id 加载单条记录并做归属校验）

关键机制：
- **默认拒绝**：未登记的权限码、未登记权限规则的请求路径、非工作区成员一律拒绝，没有隐式放行；
  路径级映射只覆盖旧 Cloud Brain 路径，未登记的域直接抛 PermissionErrorEx("接口未登记权限规则")。
- **数据范围绑定在"授予该动作的那个角色"上**：某个模块的宽范围角色不会放宽另一个模块的窄范围角色
  （见 allowed_owner_ids 的说明）。self / department / department_tree / custom / workspace 五个取值
  的含义与校验见 modules/system/permissions.py。
- **服务客户端恒被 scope 快照收窄**：即使其执行身份是工作区成员里的 admin 角色，也不算
  administrator（client_scopes 非 None 时强制与 scope 求交）。
- **显式资源授权（ResourceGrant）只补充、不放大**：它能让某个具体资源进入可见范围，但不能突破资源
  自身的 private 可见性，也不能让未授权的动作通过。

约束：AccessPolicy 是请求内缓存（``ctx._access_policy``），角色/授权变更后必须下一个请求才生效；
本模块只读权限相关表，不写库、不产生审计（审计由服务层显式调用 ``common.audit``）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import or_, select

from app.errors import PermissionErrorEx
from app.models import WorkspaceMember
from app.modules.system.models import Department, MemberProfile, ResourceGrant, Role, UserRole
from app.modules.system.permissions import ALL_PERMISSIONS, MEMBER_PERMISSIONS

if TYPE_CHECKING:
    from app.common.deps import AuthContext


@dataclass(frozen=True)
class AccessPolicy:
    """Resolved policy is request-local and never reused after a permission change.

    中文补充：某个请求内"这个用户此刻到底有哪些权限"的不可变快照。

    字段：
    - ``permissions``：最终生效的权限码集合（已与客户端 scope 求交、已按 ALL_PERMISSIONS 过滤），
      是 require_permission 与前端可见性的唯一依据；
    - ``roles``：当前工作区内已启用（enabled）且已授予该用户的角色行，用于按动作反查数据范围；
    - ``administrator``：是否按管理员处理——要求成员角色为 admin **且**不是服务客户端令牌。

    不变式：frozen 是刻意的，调用方只读不写；快照在请求内由 ``access_policy`` 缓存到
    ``ctx._access_policy``，因此同一次请求内角色被改也不会中途变化，跨请求则必须重新解析。
    """

    permissions: frozenset[str]
    roles: tuple[Role, ...]
    administrator: bool


def access_policy(ctx: AuthContext) -> AccessPolicy:
    """Resolve current persisted roles; explicit empty or disabled roles grant nothing.

    中文补充：从库里重新读取成员关系与角色，算出本次请求生效的权限集合。

    参数：``ctx`` 为 common.deps 构造的认证上下文（提供 workspace_id / user_id / client_scopes）。
    返回：``AccessPolicy``；结果缓存在 ``ctx._access_policy``，同请求内重复调用不重复查库。

    业务异常：PermissionErrorEx(403) —— 用户已不在当前工作区（成员关系被摘除）。

    副作用：只读数据库，不写库。查询使用 ``populate_existing=True`` 强制绕过 Session 的 identity map
    缓存，避免同一请求内先读到旧角色、后面又把旧值当成本次策略。

    回退规则（两条都不能省）：
    - 管理员（成员角色 admin 且非客户端令牌）直接拿 ALL_PERMISSIONS；
    - 无任何启用角色时，仅当成员档案存在但 ``roles_initialized`` 为 False（迁移前的老成员）才回退到
      MEMBER_PERMISSIONS；"角色被刻意清空"不得变成默认成员授权。
    - 客户端令牌最后统一与 ``client_scopes`` 求交，保证 scope 收缩在下一个请求即生效。
    """
    cached = getattr(ctx, "_access_policy", None)
    if cached is not None:
        return cached
    memberships = ctx.db.scalars(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ctx.workspace_id, WorkspaceMember.user_id == ctx.user_id,
    ).execution_options(populate_existing=True)).all()
    if not memberships:
        raise PermissionErrorEx("用户已不属于当前工作区")
    rows = ctx.db.scalars(select(Role).join(UserRole, UserRole.role_id == Role.id).where(
        Role.workspace_id == ctx.workspace_id, UserRole.workspace_id == ctx.workspace_id,
        UserRole.user_id == ctx.user_id, Role.enabled.is_(True),
    ).execution_options(populate_existing=True)).all()
    # Service clients are always bounded by their scope snapshot, even when
    # their execution identity is a workspace administrator.
    administrator = any(member.role == "admin" for member in memberships) and ctx.client_scopes is None
    permissions = ALL_PERMISSIONS if administrator else frozenset(
        permission for role in rows for permission in role.permissions if permission in ALL_PERMISSIONS
    )
    if not rows and not administrator:
        profile = ctx.db.scalar(select(MemberProfile).where(
            MemberProfile.workspace_id == ctx.workspace_id, MemberProfile.user_id == ctx.user_id,
        ))
        # Only pre-migration members use the old contract. An intentionally empty
        # role assignment must never become a default-member grant on restart.
        if profile is None or not profile.roles_initialized:
            permissions = MEMBER_PERMISSIONS
    if ctx.client_scopes is not None:
        # 求交必须放在最后一步：它是唯一能让"客户端 scope 被管理员角色放大"的兜底，顺序颠倒即失效。
        permissions = frozenset(permissions).intersection(ctx.client_scopes)
    policy = AccessPolicy(permissions=permissions, roles=tuple(rows), administrator=administrator)
    ctx._access_policy = policy
    return policy


def require_permission(ctx: AuthContext, permission: str) -> None:
    """Reject unknown or ungranted action codes, including direct and AI requests.

    中文补充：动作级闸门。所有入口（HTTP 路由、AI 工具、工作流节点、定时任务）都必须调用它，
    不允许因为"调用方是内部代码"而跳过——默认拒绝策略正建立在此函数上。

    参数：``permission`` 为 ``<域>.<动作>`` 权限码（如 ``files.delete``、``system.users.manage``），
    必须已登记在 ALL_PERMISSIONS 中，拼错的码也会被拒绝而不是放行。

    业务异常：PermissionErrorEx(403)，``extra.permission`` 回带被拒的权限码，供前端定位缺哪个授权。
    副作用：无（只读策略缓存），不写库、不写审计。
    """
    if permission not in ALL_PERMISSIONS or permission not in access_policy(ctx).permissions:
        raise PermissionErrorEx("没有执行此操作的权限", permission=permission)


def permitted_department_ids(ctx: AuthContext, role: Role) -> set[str]:
    """Resolve an allowed department subtree without following cross-workspace IDs.

    中文补充：把单个角色的数据范围（department / department_tree / custom）展开成部门 id 集合；
    该函数不理解权限码，只解释范围，因此由调用方保证"这个角色确实授予了目标动作"。

    参数：``role`` 必须来自 ``access_policy(ctx).roles``（即当前工作区内已启用的角色）。

    返回：允许的部门 id 集合，可能为空集（例如成员没有部门档案）；``self`` 与 ``workspace`` 范围
    在这里没有意义，会得到空集，调用方需按范围自行短路（见 allowed_owner_ids）。

    业务异常：无。副作用：无（只读）。

    约束：
    - 只在本工作区的 Department 行内匹配，跨工作区的 department_id（哪怕是 custom 里手填的）
      不会命中，因此越权引用外工作区部门是静默失效而不是报错；
    - department_tree 用不动点迭代逐层加子节点，深度不限，但依赖 parent_id 构成树；若数据里出现
      环，循环会在集合不再增长时结束，不会死循环。
    """
    profile = ctx.db.scalar(select(MemberProfile).where(
        MemberProfile.workspace_id == ctx.workspace_id, MemberProfile.user_id == ctx.user_id,
    ))
    if role.data_scope == "custom":
        roots = set(role.department_ids)
    else:
        roots = {profile.department_id} if profile and profile.department_id else set()
    departments = ctx.db.scalars(select(Department).where(Department.workspace_id == ctx.workspace_id)).all()
    result = {row.id for row in departments if row.id in roots}
    if role.data_scope == "department_tree":
        while True:
            children = {row.id for row in departments if row.parent_id in result}
            previous_count = len(result)
            result |= children
            if len(result) == previous_count:
                break
    return result


def allowed_owner_ids(ctx: AuthContext, permission: str) -> set[str] | None:
    """Return allowed creators, or None for the complete current workspace.

    Data scope is attached to the role granting this exact action. A broad role
    for one module cannot widen another module's narrow role.

    中文补充：数据范围判定的主入口，``common.data_scope`` 与 ``common.owned_records`` 都以它为准。

    参数：``permission`` 为实现该动作的完整权限码（调用方应传当前请求的动作，例如
    ``ctx.request_permission``），数据范围按"哪些角色授予了这个码"来取。

    返回：
    - ``None`` —— 不做创建人限制，表示该动作允许整个工作区（管理员、workspace 范围角色，
      或迁移前无角色的老成员契约）；
    - 非空集合 —— 允许的创建人 user_id 集合，**恒包含本人**（self 范围的语义），部门范围会并入
      这些部门内所有成员。

    业务异常：PermissionErrorEx(403) —— 由 require_permission 抛出（未授权/未登记的动作码）。
    副作用：无（只读 MemberProfile 与角色行）。

    约束：多角色取并集，但只并入"授予该动作"的角色；同一个人若只在一个模块上拿到窄范围，
    不会因另一个模块的宽范围角色而放宽（模块间不串权）。
    """
    require_permission(ctx, permission)
    policy = access_policy(ctx)
    if policy.administrator:
        return None
    roles = [role for role in policy.roles if permission in role.permissions]
    if not roles:  # pre-migration member contract
        return None
    if any(role.data_scope == "workspace" for role in roles):
        return None
    users = {ctx.user_id}
    department_ids = set()
    for role in roles:
        if role.data_scope in ("department", "department_tree", "custom"):
            department_ids |= permitted_department_ids(ctx, role)
    if department_ids:
        # 部门范围按"部门内成员"展开成创建人集合，因此成员换部门后无需改数据即可自动收敛。
        users.update(ctx.db.scalars(select(MemberProfile.user_id).where(
            MemberProfile.workspace_id == ctx.workspace_id,
            MemberProfile.department_id.in_(department_ids),
        )).all())
    return users


def allowed_department_ids(ctx: AuthContext, permission: str) -> set[str] | None:
    """Resolve allowed organization assignments for an exact administrative action.

    中文补充：用于"管理部门/成员"这类以部门为对象的管理动作（不是业务数据的创建人范围）。

    返回：``None`` 表示该动作覆盖整个工作区；否则为允许操作的部门 id 集合（多个角色的并集，
    已按本工作区过滤）。集合可能为空，语义是"没有任何可管理部门"，调用方应据此拒绝而不是放行。

    业务异常：PermissionErrorEx(403) —— 动作未授权或权限码未登记（经 require_permission）。
    副作用：无。
    """
    require_permission(ctx, permission)
    policy = access_policy(ctx)
    roles = [role for role in policy.roles if permission in role.permissions]
    if policy.administrator or any(role.data_scope == "workspace" for role in roles):
        return None
    return set().union(*(permitted_department_ids(ctx, role) for role in roles
                         if role.data_scope in {"department", "department_tree", "custom"}))


def require_user_scope(ctx: AuthContext, permission: str, user_id: str) -> None:
    """Action-specific people scope protects administration as well as business data.

    中文补充：判断"能不能对某个人执行该动作"，用于成员管理、代操作等以人为对象的接口；
    业务数据的归属判定请用 allowed_owner_ids / require_resource。

    参数：``user_id`` 为目标人；``permission`` 为实现该动作的权限码（范围取自此码对应的角色）。

    业务异常：PermissionErrorEx(403) —— 目标人不在该动作允许的数据范围内（也包含动作本身未授权的
    情况，由 allowed_owner_ids 内部抛出）。
    副作用：无。
    """
    owners = allowed_owner_ids(ctx, permission)
    if owners is not None and user_id not in owners:
        raise PermissionErrorEx("该用户不在此操作允许的数据范围内", permission=permission)


def resource_grant_ids(ctx: AuthContext, resource_type: str, action: str) -> set[str]:
    """Return explicitly shared resources for this actor and their active roles.

    中文补充：读出"显式授权"给本人或本人角色的资源 id 集合，用于在数据范围之外打开单个资源。

    参数：``resource_type`` 必须与 ResourceGrant 的登记口径一致（admin_service.RESOURCE_MODELS 的键，
    同时也是权限码的域，例如 files / knowledge）；``action`` 是权限码的动作段（如 view / edit / delete），
    二者拼起来才是被授予的动作。

    返回：资源 id 集合（可能为空集，表示没有显式授权）。

    业务异常：无。副作用：无（只读 system_resource_grants）。

    约束：只认 subject_type 为 user（本人）或 role（本人已启用角色）的授权；范围仍由调用方限定在
    当前工作区，跨工作区行不参与匹配。授权是"补充"手段：资源自身的 private 约束优先（见
    private_visibility_predicate），不能靠授权绕过。
    """
    roles = {role.id for role in access_policy(ctx).roles}
    grants = ctx.db.scalars(select(ResourceGrant).where(
        ResourceGrant.workspace_id == ctx.workspace_id, ResourceGrant.resource_type == resource_type,
    )).all()
    return {
        row.resource_id for row in grants if action in row.actions and (
            (row.subject_type == "user" and row.subject_id == ctx.user_id)
            or (row.subject_type == "role" and row.subject_id in roles)
        )
    }


# 具备"仅自己可见"语义的资源表名白名单：这些表必须有 visibility 与 owner_user_id 两列。
# 新增一张带 private 可见性的表时，必须同时登记到 common/data_scope.ROOT_RESOURCES，
# 否则查询级过滤不会生效，只有接口层的 require_resource 还会拦住单条访问。
PRIVATE_RESOURCE_TABLES = frozenset({"skills", "plugins", "connectors"})


def private_visibility_predicate(ctx: AuthContext, model):
    """The UI's 'only me' setting is independent of a role's data scope.

    Workspace administrators retain administrative access. A broad workspace
    role or an old resource grant cannot undo an owner's switch to private.

    中文补充：返回一段 SQLAlchemy 条件（或 None），供 visible_records / bind_data_scope 直接 and_ 进查询。

    返回：
    - ``None`` —— 该模型不适用（表名不在 PRIVATE_RESOURCE_TABLES）或调用者是管理员，不需要额外条件；
    - 条件对象 —— ``visibility != 'private' OR owner_user_id = 本人``。

    业务异常：无。副作用：无（只读策略缓存）。

    约束：这是"创建者的主观设置"，优先级高于角色数据范围与历史资源授权；管理员保留可见性是为了
    可运维，不要用本条件去限制管理员。（注意与 require_resource 中的同类判断必须保持一致）
    """
    if getattr(model, "__tablename__", None) not in PRIVATE_RESOURCE_TABLES or access_policy(ctx).administrator:
        return None
    return or_(model.visibility != "private", model.owner_user_id == ctx.user_id)


def require_resource(ctx: AuthContext, permission: str, resource, *, owner_field: str = "owner_user_id") -> None:
    """Enforce workspace and role-attached data boundaries on a loaded resource.

    中文补充：对一条**已加载**的记录做完整归属校验，是接口层最后一道闸门（查询级过滤见 data_scope）。

    参数：``permission`` 为实现该动作的权限码；``owner_field`` 指定该表的创建人列名，默认
    ``owner_user_id``，对 Run 这类用 ``started_by_user_id`` 的表必须显式传入。

    业务异常：均为 PermissionErrorEx(403)，按以下顺序判定，先命中先抛：
    1. 记录不属于当前工作区；
    2. 表属于 PRIVATE_RESOURCE_TABLES 且 visibility=private 且非本人且非管理员；
    3. 创建人不在 allowed_owner_ids 范围，且该资源没有针对本动作的显式资源授权。

    返回：无（通过即返回）。副作用：无（只读策略与授权表，不改记录）。

    约束：调用前必须已经取到记录实例，因此它不能替代查询级过滤——列表接口若只靠它，会把越权记录
    查出来再逐条拒绝，既泄漏数量也浪费 IO。正确用法是 visible_records/owned_record 先过滤、本函数兜底。
    """
    require_permission(ctx, permission)
    if getattr(resource, "workspace_id", None) != ctx.workspace_id:
        raise PermissionErrorEx("无权限访问该工作区的资源")
    if (getattr(resource, "__tablename__", None) in PRIVATE_RESOURCE_TABLES
            and getattr(resource, "visibility", None) == "private"
            and getattr(resource, "owner_user_id", None) != ctx.user_id
            and not access_policy(ctx).administrator):
        raise PermissionErrorEx("该资源仅创建者和工作区管理员可访问")
    owners = allowed_owner_ids(ctx, permission)
    if owners is None or getattr(resource, owner_field, None) in owners:
        return
    domain, action = permission.rsplit(".", 1)
    # 权限码按 "<域>.<动作>" 拆分；若某权限码没有点号（不该出现），rsplit 会抛 ValueError，
    # 这属于登记错误，宁可 500 也不能静默放行。
    if resource.id in resource_grant_ids(ctx, domain, action):
        return
    raise PermissionErrorEx("该资源不在当前用户的数据权限范围内")


# 旧 Cloud Brain 路径段 → 权限域的映射表，仅供 enforce_request_permission 的路径推断使用。
# 左侧是 URL 里的资源段，右侧是权限码的域（可与路径段不同：artifacts 归入 files、
# presentation-templates 归入 documents、capability 归入 capabilities）。
# 新增旧版路径或改域名时，必须同时改这里与 modules/system/permissions 的权限目录，否则该路径会
# 落到"接口未登记权限规则"而被整体拒绝（默认拒绝，不做猜测）。
LEGACY_DOMAINS = {
    "chat": "chat", "knowledge": "knowledge", "plugins": "plugins", "skills": "skills",
    "connectors": "connectors", "datasources": "datasources", "files": "files",
    "artifacts": "files", "presentation-templates": "documents", "runs": "runs",
    "models": "models", "secrets": "secrets", "audit": "audit", "capability": "capabilities",
}


def enforce_request_permission(ctx: AuthContext, request: Request) -> None:
    """Apply finite action checks to existing Cloud Brain APIs during migration.

    New versioned routers declare exact action checks in their service layer.
    Safe read-like POST endpoints are explicitly listed, never inferred from text.

    中文补充：由 common.deps 在认证后调用，把请求路径映射成权限码并校验；只覆盖**旧版未版本化路径**。

    跳过规则（按顺序）：
    - ``/api/v1/*``：新版路由（如 ``/api/v1/system``）在服务层显式声明精确动作，这里必须整体跳过——
      否则会按路径段猜出错误的域/动作，既可能与真实校验冲突（同一动作被校验两次而其中一次是错的），
      也会把 v1 刻意设计的细粒度码（如 system.users.reset_password）压成粗粒度 create/edit/delete。
      因此 v1 接口的授权责任在服务层，不能依赖本函数的路径映射兜底（现有 v1 接口均已调用
      require_permission / owned_record）。
    - ``/api/auth/*``：登录、登出、改密等本身就是获取会话的入口，无域级权限码可依；其权限边界由
      登录依赖与"仅限本人"语义保证（见 modules/auth/router.py 的约束说明）。
    - ``/api/workspace``：登录后选择/读取当前工作区，是所有页面的前置调用，不能要求额外的域权限。
    - ``/api/workspace/*``（成员与工作区管理）：按方法映射为 system.users.view（GET）或
      system.users.manage（其它方法）。

    其余路径：取第二段经 LEGACY_DOMAINS 查域，未登记的域直接抛 PermissionErrorEx
    ("接口未登记权限规则")，**绝不按路径猜测放行**；动作由 HTTP 方法与末段语义共同决定
    （读方法 view，写方法 create/edit/delete，未识别方法 execute，末段 download/example/export →
    export、publish/share → publish、archive/versions → edit、send/test*/probe/sync 等 → execute；
    ``/api/knowledge/search`` 例外为只读 view）。

    业务异常：PermissionErrorEx(403)。副作用：把最终权限码写入 ``ctx.request_permission``，
    供 bind_data_scope 决定按哪个动作限定可见数据（不写库、不写审计）。
    """
    path = request.url.path
    if path.startswith("/api/v1/") or path.startswith("/api/auth/") or path == "/api/workspace":
        return
    if path.startswith("/api/workspace/"):
        require_permission(ctx, "system.users.view" if request.method == "GET" else "system.users.manage")
        return
    segments = path.strip("/").split("/")
    # 旧路径形如 /api/<资源段>/...，资源段固定在下标 1；下标越界（如 /api）时查表得到 None，
    # 同样走"接口未登记权限规则"拒绝。
    domain = LEGACY_DOMAINS.get(segments[1] if len(segments) > 1 else "")
    if domain is None:
        raise PermissionErrorEx("接口未登记权限规则")
    action = "view" if request.method in ("GET", "HEAD") else {
        "POST": "create", "PUT": "edit", "PATCH": "edit", "DELETE": "delete",
    }.get(request.method, "execute")
    leaf = segments[-1]
    if domain == "models":
        # 模型名与配置 ID 是数据，不允许 export/probe 等名称改变写操作的权限。
        # 仅固定的导出、探测路由具有特殊动作，其余模型路由保持 HTTP 方法语义。
        if segments[2:] == ["usage", "export"] and request.method in {"GET", "HEAD"}:
            action = "export"
        elif request.method == "POST" and (
            (len(segments) == 4 and segments[2] == "probe")
            or (len(segments) == 5 and segments[2] == "profiles" and segments[4] == "probe")
        ):
            action = "execute"
    elif leaf in {"download", "example", "export"}:
        action = "export"
    elif leaf in {"send", "test", "test-run", "test-invoke", "reindex", "recover-stale", "probe", "sync"} or "probe" in segments:
        action = "execute"
    elif leaf in {"publish", "share"}:
        action = "publish"
    elif path == "/api/knowledge/search":
        action = "view"
    elif leaf in {"archive", "versions"}:
        action = "edit"
    require_permission(ctx, f"{domain}.{action}")
    ctx.request_permission = f"{domain}.{action}"
