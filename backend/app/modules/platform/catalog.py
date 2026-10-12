"""业务导航目录：平台外壳的菜单清单，与 AI 对话界面相互独立。

职责：
  * 定义平台侧边栏/顶栏可见的业务页面清单（模块 id、显示名、所属业务域、路径、权限码、是否默认可见）；
  * 作为 `/api/v1/platform/navigation` 的唯一数据源，前端按登录账号的权限过滤后渲染菜单。

依赖：`app.branding.PRODUCT_NAME`（显示的产品名），无数据库依赖。

约束：
  * 本文件是**权限码的声明处**：每个条目的 `permission` 必须登记在 `system/permissions.py` 的权限目录里，否则对应角色无法授予、菜单永远不可见；
  * `available=False` 表示该页面对外不可达（前端不展示、也不应出现在导航里）；
  * `default_visible=False` 的条目仍可通过 URL 直接访问，只是默认不进侧边栏（管理员可显式放开）；
  * **注意 `path` 指向旧版静态页（`.html` 或旧路由）的条目**：点击后会整页跳转离开平台外壳（丢失平台导航与 iframe 上下文），已在下方逐条标注。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.branding import PRODUCT_NAME


@dataclass(frozen=True)
class ModuleEntry:
    """一个业务页面条目：可用性与菜单可见性相互独立。

    字段语义：
      * `id`：模块标识，前端用于路由别名、最近访问记录与权限过滤，改动会影响前端映射；
      * `name`：菜单显示名（中文）；
      * `group`：所属业务域（对应前端顶栏分组）；
      * `path`：页面路径（可能指向平台内页面、iframe 承载的 Vue 页面，或旧版静态页）；
      * `permission`：进入该页面所需的权限码，必须已登记在权限目录中；
      * `source`：来源标记（当前统一为产品名）；
      * `available`：该页面是否真实可用（False 表示未实现/不可达）；
      * `default_visible`：是否默认出现在侧边栏（False 表示仅 URL 可达或由管理员显式放开）。
    """

    id: str
    name: str
    group: str
    path: str
    permission: str
    source: str
    available: bool = False
    default_visible: bool = True


MODULES = (
    ModuleEntry("home", "首页概览", "工作空间", "/", "home.view", PRODUCT_NAME, True),
    ModuleEntry("chat", "AI 对话", "工作空间", "/ai", "chat.view", PRODUCT_NAME, True),
    ModuleEntry("assets", "数据与资料目录", "数据与分析", "/assets-catalog", "assets.view", PRODUCT_NAME, True),
    ModuleEntry("datasources", "数据源", "数据与分析", "/datasources", "datasources.view", PRODUCT_NAME, True),
    ModuleEntry("connectors", "连接器", "数据与分析", "/connectors", "connectors.view", PRODUCT_NAME, True),
    ModuleEntry("analytics", "AI 数据分析", "数据与分析", "/analytics", "analytics.view", PRODUCT_NAME, True),
    ModuleEntry("dashboards", "报表与数字大屏", "数据与分析", "/dashboards", "dashboards.view", PRODUCT_NAME, True),
    ModuleEntry("templates", "模板中心", "数据与分析", "/templates", "templates.view", PRODUCT_NAME, True),
    ModuleEntry("prompts", "提示词库", "数据与分析", "/prompts", "prompts.view", PRODUCT_NAME, True),
    ModuleEntry("files", "文件与生成结果", "知识与内容", "/files", "files.view", PRODUCT_NAME, True),
    ModuleEntry("knowledge", "知识库", "知识与内容", "/knowledge", "knowledge.view", PRODUCT_NAME, True),
    ModuleEntry("ontology", "概念与关系模型", "知识与内容", "/ontology", "ontology.view", PRODUCT_NAME, True),
    ModuleEntry("extraction", "知识抽取", "知识与内容", "/extraction", "extraction.view", PRODUCT_NAME, True),
    ModuleEntry("graphs", "知识图谱", "知识与内容", "/graphs", "graphs.view", PRODUCT_NAME, True),
    ModuleEntry("documents", "文档与 PPT", "知识与内容", "/documents", "documents.view", PRODUCT_NAME, True),
    ModuleEntry("bots", "Bot 与智能体", "自动化与应用", "/bots", "bots.view", PRODUCT_NAME, True),
    ModuleEntry("workflows", "可视化工作流", "自动化与应用", "/workflows", "workflows.view", PRODUCT_NAME, True),
    ModuleEntry("skills", "Skills 技能", "自动化与应用", "/skills", "skills.view", PRODUCT_NAME, True),
    ModuleEntry("plugins", "Plugins 插件", "自动化与应用", "/plugins", "plugins.view", PRODUCT_NAME, True),
    ModuleEntry("capabilities", "工具与能力管理", "自动化与应用", "/capabilities", "capabilities.view", PRODUCT_NAME, True),
    ModuleEntry("applications", "应用与解决方案", "自动化与应用", "/applications", "applications.view", PRODUCT_NAME, True),
    ModuleEntry("alerts", "设备与预警", "设备与质量", "/alerts", "alerts.view", PRODUCT_NAME, True),
    ModuleEntry("quality", "质量检查", "设备与质量", "/quality", "quality.view", PRODUCT_NAME, True),
    ModuleEntry("operations", "运行记录", "运营与运维", "/operations", "operations.view", PRODUCT_NAME, True),
    # 以下保留页面完全可达，但默认不进导航：它们的常用任务已合并进
    # 运行记录/开发与集成页面。管理员可以显式放开。
    # ⚠ /runs 由旧版静态页 apps/web/runs.html 提供：点击会整页跳离平台外壳。
    ModuleEntry("runs", "运行记录", "运营与运维", "/runs", "runs.view", PRODUCT_NAME, True, False),
    ModuleEntry("models", "模型与用量", "运营与运维", "/system/models", "models.view", PRODUCT_NAME, True),
    # ⚠ /admin-secrets.html 为旧版静态页：点击会整页跳离平台外壳（且是默认可见项）。
    ModuleEntry("secrets", "密钥管理", "运营与运维", "/admin-secrets.html", "secrets.view", PRODUCT_NAME, True),
    # ⚠ /admin-ops.html 为旧版静态页：点击会整页跳离平台外壳。
    ModuleEntry("monitor", "运行监控", "运营与运维", "/admin-ops.html", "monitor.view", PRODUCT_NAME, True, False),
    ModuleEntry("users", "用户管理", "系统管理", "/system/users", "system.users.view", PRODUCT_NAME, True),
    ModuleEntry("workspaces", "组织与工作区", "系统管理", "/system/workspaces", "system.workspaces.view", PRODUCT_NAME, True),
    ModuleEntry("departments", "组织与部门", "系统管理", "/system/departments", "system.departments.view", PRODUCT_NAME, True),
    ModuleEntry("positions", "岗位管理", "系统管理", "/system/positions", "system.positions.view", PRODUCT_NAME, True),
    ModuleEntry("roles", "角色管理", "系统管理", "/system/roles", "system.roles.view", PRODUCT_NAME, True),
    ModuleEntry("permissions", "权限与数据范围", "系统管理", "/system/permissions", "system.permissions.view", PRODUCT_NAME, True),
    ModuleEntry("menus", "菜单管理", "系统管理", "/system/menus", "system.menus.view", PRODUCT_NAME, True),
    ModuleEntry("sessions", "在线会话", "系统管理", "/system/sessions", "system.sessions.view", PRODUCT_NAME, True),
    ModuleEntry("security", "登录与企业 SSO", "系统管理", "/system/security", "system.security.view", PRODUCT_NAME, True),
    ModuleEntry("settings", "字典与参数", "系统管理", "/system/settings", "system.settings.view", PRODUCT_NAME, True),
    # ⚠ /admin-audit.html 为旧版静态页：点击会整页跳离平台外壳。
    ModuleEntry("audit", "审计日志", "系统管理", "/admin-audit.html", "audit.view", PRODUCT_NAME, True, False),
    ModuleEntry("system-operations", "系统运维", "系统管理", "/system/operations", "system.operations.view", PRODUCT_NAME, True),
    ModuleEntry("system-development", "开发与集成", "系统管理", "/system/development", "system.development.view", PRODUCT_NAME, True),
    # 保留稳定的 API 权限契约（api.view），可见界面已合并到上面的"开发与集成"页面。
    ModuleEntry("api", "接口中心", "系统管理", "/system/development", "api.view", PRODUCT_NAME, True, False),
)


def navigation_catalog() -> list[dict]:
    """返回全部菜单定义（不做权限过滤），供 `/api/v1/platform/navigation` 使用。

    调用方负责按当前账号权限过滤；本函数只做数据到字典的转换，保证条目来源可追溯。
    """
    return [asdict(entry) for entry in MODULES]
