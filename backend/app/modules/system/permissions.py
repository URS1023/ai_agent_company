"""Finite action registry shared by role forms, API policies and AI tools.

职责：定义“系统里到底允许哪些动作编码”的唯一权威目录。角色表单、后端 require_permission、
AI 工具暴露清单都从这里取值，任何未登记编码一律被拒绝（role_service.validate_role_policy）。

依赖与位置：以 app.modules.platform.catalog.MODULES（模块/菜单登记表）为唯一模块来源，
模块的 permission = "<domain>.<action>"，本模块据此展开成完整权限目录；被
role_service / admin_service / bootstrap（内置角色）以及 common.authorization 共用。
本文件不注册路由（system 模块路由前缀统一为 /api/v1/system）。

数据范围（DATA_SCOPES）与权限目录的关系：权限回答“能做哪个动作”，数据范围回答“能在谁的数据上做”，
两者绑定在同一个角色上；判定在 common.authorization 中完成：
- self：仅本人数据（allowed_owner_ids 只返回自己）；
- department：本人所属部门内成员的数据（按 MemberProfile.department_id 匹配）；
- department_tree：本部门 + 全部下级部门（沿 parent_id 递归展开）；
- custom：角色 department_ids 显式指定的部门集合（至少一个，否则校验不通过）；
- workspace：整个工作区（管理员与 workspace 范围角色等价于不限制）。
重要约束：数据范围跟着“授予该动作的那个角色”走，某个模块的宽范围角色不会放宽另一个模块的窄范围角色
（见 allowed_owner_ids 的实现说明）。
"""
from __future__ import annotations

from app.modules.platform.catalog import MODULES

# 业务模块通用的五个动作及中文标签，用于生成 <domain>.<action> 编码与角色表单文案。
ACTION_LABELS = {
    "view": "查看", "create": "创建", "edit": "修改", "delete": "删除",
    "execute": "执行", "publish": "发布与分享", "export": "导出与下载",
}
# 平台管理域：这些域的权限不进入内置“业务成员”角色，只由管理员显式授予。
ADMIN_DOMAINS = {"models", "secrets", "monitor", "audit", "api"}
PERMISSION_CATALOG: dict[str, dict[str, str]] = {}
# 展开规则：
# - system.* 属于系统管理域，只提供 view/manage 两个动作，避免把用户、角色、菜单等管理动作
#   拆成 create/edit/delete 后出现“能改但不能看”这类无法自洽的授权组合；
# - 其它域的模块默认展开 ACTION_LABELS（view/create/edit/delete/execute/publish/export），
#   但 home 只保留 view（首页是导航入口，不承载写操作）。
# 目录项字段（code/name/group/module/action）同时被角色表单、OpenAPI 与权限清单接口使用，
# 新增模块只需在 platform.catalog.MODULES 登记，无需改本文件。
for entry in MODULES:
    if entry.permission.startswith("system."):
        domain = entry.permission.rsplit(".", 1)[0]
        for action, label in {"view": "查看", "manage": "管理"}.items():
            code = f"{domain}.{action}"
            PERMISSION_CATALOG[code] = {
                "code": code, "name": f"{entry.name} · {label}", "group": entry.group,
                "module": entry.id, "action": action,
            }
    else:
        domain = entry.permission.rsplit(".", 1)[0]
        actions = {"view": "查看"} if domain == "home" else ACTION_LABELS
        for action, label in actions.items():
            code = f"{domain}.{action}"
            PERMISSION_CATALOG[code] = {
                "code": code, "name": f"{entry.name} · {label}", "group": entry.group,
                "module": entry.id, "action": action,
            }

# devices.* 是跨模块的“共享设备与测点”动作集合：模块归属登记为 alerts，但 group 归入
# “设备与质量”，因此必须单独补登记，不能依赖上面的 MODULES 循环生成。
for action, label in ACTION_LABELS.items():
    code = "devices." + action
    PERMISSION_CATALOG[code] = {"code": code, "name": "共享设备与测点 · " + label,
                               "group": "设备与质量", "module": "alerts", "action": action}

# 高影响操作：无法归入某个业务模块的“域.动作”命名（如 system.admin、*.writeback），
# 或虽然名字符合规则但需要单独提示风险的动作。这些编码不在模块循环里生成，必须在这里显式登记。
# 注意：编码一旦发布就被角色 permissions、审计历史与前端缓存引用，只能新增、不要改名。
for code, name in {
    "system.admin": "系统管理员级操作",
    "system.users.reset_password": "重置用户密码",
    "system.users.assign_roles": "为用户分配角色",
    "system.users.import": "批量导入与更新用户",
    "system.users.export": "导出工作区用户资料",
    "system.permissions.history": "查看权限变更历史",
    "alerts.writeback": "预警结果写入授权目标表",
    "datasources.writeback": "授权业务表字段写入（高影响）",
    "quality.review": "质检逐项人工复核（保留原始判断）",
    "quality.writeback": "质检结果写入授权目标表",
}.items():
    PERMISSION_CATALOG[code] = {
        "code": code, "name": name, "group": "高影响操作",
        "module": code.split(".")[0], "action": code.rsplit(".", 1)[-1],
    }

ALL_PERMISSIONS = frozenset(PERMISSION_CATALOG)
# 内置“业务成员”角色的授权集合：从全量目录里做减法，而不是手工列举，避免新模块上线后被漏掉。
# 减法条件对应三类高风险动作：
# - system.*：系统管理动作只给管理员；
# - ADMIN_DOMAINS：平台域（模型、密钥、监控、审计、API）不对业务成员开放；
# - *.writeback 与 quality.review：写回外部表/人工复核会影响客户数据，必须显式授权。
# 边界：这些集合只用于 bootstrap 生成内置角色。用户后来改过的内置角色会在升级时被重新对齐，
# 自定义角色不受影响（见 bootstrap.ensure_system_roles）。
MEMBER_PERMISSIONS = frozenset(
    code for code in ALL_PERMISSIONS
    if not code.startswith("system.")
    and code.split(".")[0] not in ADMIN_DOMAINS
    and not code.endswith(".writeback")
    and code != "quality.review"
)
# 只读查看者的授权集合：在成员集合上再取 .view，保证既不写也不执行、不导出。
VIEWER_PERMISSIONS = frozenset(code for code in MEMBER_PERMISSIONS if code.endswith(".view"))
# 数据范围枚举：顺序与前端表单展示一致，也是 role_service.permission_catalog 的中文来源；
# 取值必须与 schemas.DataScope、models.Role.data_scope 的取值保持一致（三处同步修改）。
DATA_SCOPES = ("self", "department", "department_tree", "custom", "workspace")
