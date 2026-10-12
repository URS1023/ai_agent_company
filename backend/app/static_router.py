# -*- coding: utf-8 -*-
"""Serve the ordinary business platform and its independent AI conversation.

职责：提供前端页面路由（HTML 外壳）与静态资源挂载，不做业务数据处理。

系统位置：由 app.main 在路由注册的最后一步调用 mount_static() 挂载——其中的通配路径
（`/{page_name}.html`、`/workflows/{id}`）会吃掉任意单段路径，因此必须晚于业务 API 路由注册。

关键约束：
- 本模块所有页面路由都带 include_in_schema=False，不进入对外 OpenAPI 契约（API 契约见
  modules.platform.openapi_contract），接口清单里只应有 /api/*。
- 页面白名单一律在函数内用集合/正则硬校验（`_SYSTEM_PAGES`、`re.fullmatch(r"[A-Za-z0-9_-]{1,26}")` 等）：
  这些路由把路径片段拼进文件路径，白名单是防止路径穿越的唯一屏障，新增页面必须同时
  更新白名单，禁止直接把用户输入拼进 WEB_ROOT。
- 前后端分离：平台业务壳是 platform.html，AI 对话是 index.html（独立页面，不复用平台壳），
  数据分析/工作流编辑器是 apps/analytics 构建出的 Vue bundle（整屏挂载，套在业务壳外）。
- 旧版路径不删除，统一用 307（保留方法与查询串）重定向到新路径，保证前端书签与历史链接可用。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .branding import PRODUCT_NAME
from .config import PROJECT_ROOT

# 目录常量集中在模块顶部：构建产物缺失时各路由按"未构建"提示降级，而不是启动失败。
WEB_ROOT = Path(PROJECT_ROOT) / "apps" / "web"
ASSETS_DIR = WEB_ROOT / "assets"
JIMU_ASSETS_DIR = ASSETS_DIR / "analytics" / "jimu-screen"
INDEX_FILE = WEB_ROOT / "index.html"
ADMIN_FILE = WEB_ROOT / "admin.html"
PLATFORM_FILE = WEB_ROOT / "platform.html"
ANALYTICS_FILE = ASSETS_DIR / "analytics" / "index.html"

router = APIRouter(tags=["web"])


@router.get("/api/webinfo")
def webinfo():
    """前端运行时元信息（产品名、版本、构建产物是否存在）；唯一进入 OpenAPI 的端点。"""
    return {"name": PRODUCT_NAME, "version": "0.2.0", "assets": ASSETS_DIR.exists()}


@router.get("/", include_in_schema=False)
def index(request: Request):
    """站点根路径（GET /）：默认给平台业务壳，历史对话链接转交 AI 页。

    307 保留原查询串：老书签里带 cid / knowledge_base_id / plugin_id / plugin_version_id /
    file_id 的链接仍应落到独立的 AI 对话页，而不是平台壳。
    """
    # Existing conversation bookmarks continue to reach the independent AI page.
    if any(key in request.query_params for key in ("cid", "knowledge_base_id", "plugin_id", "plugin_version_id", "file_id")):
        return RedirectResponse("/ai?" + request.url.query, status_code=307)
    if PLATFORM_FILE.exists():
        return FileResponse(str(PLATFORM_FILE))
    return JSONResponse({"error": "前端未构建，请先完成 apps/web"}, status_code=404)


@router.get("/app", include_in_schema=False)
def app_index():
    """GET /app：平台业务壳的兼容入口（与 "/" 同一份 platform.html）。"""
    if PLATFORM_FILE.exists():
        return FileResponse(str(PLATFORM_FILE))
    return JSONResponse({"error": "前端未构建"}, status_code=404)


@router.get("/ai", include_in_schema=False)
def ai_page():
    """AI conversation is a separate module, not the root application shell.

    GET /ai：交付对话页 index.html；禁用缓存，避免前端更新后浏览器仍跑旧壳。
    """
    return FileResponse(str(INDEX_FILE), headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/analytics-app", include_in_schema=False)
def analytics_bundle():
    """Isolated Vue component bundle embedded by the normal business shell.

    GET /analytics-app：业务壳通过该地址内嵌 Vue 子应用（构建产物不存在时返回 503）。
    """
    return _analytics_file()


def _analytics_file():
    """Serve the Vue host for embedded modules and full-screen editors.

    返回：apps/analytics 的 index.html（no-cache，构建路径变更后需重新构建）；
    构建产物缺失时返回 503 与构建提示——这不是 404，前端据此区分"路径错"与"未构建"。
    """
    if not ANALYTICS_FILE.is_file():
        return JSONResponse({"error": "数据分析界面未构建，请运行 apps/analytics 的构建命令"}, status_code=503)
    return FileResponse(str(ANALYTICS_FILE), headers={"Cache-Control": "no-cache"})


@router.get("/datasources", include_in_schema=False)
@router.get("/workflows", include_in_schema=False)
@router.get("/alerts", include_in_schema=False)
@router.get("/quality", include_in_schema=False)
@router.get("/analytics", include_in_schema=False)
@router.get("/dashboards", include_in_schema=False)
@router.get("/templates", include_in_schema=False)
@router.get("/prompts", include_in_schema=False)
@router.get("/report", include_in_schema=False)
@router.get("/report/designer", include_in_schema=False)
@router.get("/jimu-screen", include_in_schema=False)
@router.get("/data-query", include_in_schema=False)
@router.get("/ontology", include_in_schema=False)
@router.get("/extraction", include_in_schema=False)
@router.get("/graphs", include_in_schema=False)
@router.get("/bots", include_in_schema=False)
@router.get("/assets-catalog", include_in_schema=False)
@router.get("/documents", include_in_schema=False)
@router.get("/capabilities", include_in_schema=False)
@router.get("/connectors", include_in_schema=False)
@router.get("/skills", include_in_schema=False)
@router.get("/plugins", include_in_schema=False)
@router.get("/applications", include_in_schema=False)
@router.get("/operations", include_in_schema=False)
@router.get("/knowledge", include_in_schema=False)
@router.get("/files", include_in_schema=False)
def analytics_page():
    """上述一级业务路径的共用处理器：一律返回平台壳，具体页面由前端路由决定。

    这里显式逐个装饰而不写通配路径，是为了把"允许的一级路径"固定成白名单：
    新增一级入口必须在此显式登记，否则会落到模块页/404 逻辑。
    """
    return FileResponse(str(PLATFORM_FILE))


@router.get("/workflows/{workflow_id}", include_in_schema=False)
def workflow_page(workflow_id: str):
    """GET /workflows/{workflow_id}：具体工作流直接整屏挂载编辑器。

    workflow_id 只允许字母/数字/下划线/连字符且 ≤26 字符（与 ULID 主键同宽），
    校验不通过按 404 处理，不泄露路径细节。
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", workflow_id):
        return JSONResponse({"error": "工作流页面不存在"}, status_code=404)
    # The workflow editor owns the whole browser viewport.  Listing and other
    # business pages still use the platform shell, while a concrete workflow
    # is mounted directly so no shell/iframe can constrain its canvas.
    return _analytics_file()


@router.get("/workflows/{workflow_id}/{section}", include_in_schema=False)
def workflow_workspace_page(workflow_id: str, section: str):
    """GET /workflows/{workflow_id}/{section}：工作流的接入点/日志/概览/运行子页。

    section 白名单与前端 Vue 路由保持一致，改名需同步前端，否则前端路由匹配不到页面。
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", workflow_id) or section not in {"access-point", "logs", "overview", "run"}:
        return JSONResponse({"error": "工作流页面不存在"}, status_code=404)
    return _analytics_file()


@router.get("/alerts/devices/{device_id}/workflow", include_in_schema=False)
@router.get("/quality/devices/{device_id}/workflow", include_in_schema=False)
def device_workflow_page(device_id: str):
    """GET /alerts|/quality/devices/{device_id}/workflow：设备侧工作流的整屏入口。

    两个前缀共用同一处理器：设备台账在预警与质检域之间共享，页面实现只有一份。
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", device_id):
        return JSONResponse({"error": "设备工作流页面不存在"}, status_code=404)
    return _analytics_file()


@router.get("/alerts/devices/{device_id}/workflow/{section}", include_in_schema=False)
@router.get("/quality/devices/{device_id}/workflow/{section}", include_in_schema=False)
def device_workflow_workspace_page(device_id: str, section: str):
    """GET /alerts|/quality/devices/{device_id}/workflow/{section}：设备工作流子页（同上白名单）。"""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", device_id) or section not in {"access-point", "logs", "overview", "run"}:
        return JSONResponse({"error": "设备工作流页面不存在"}, status_code=404)
    return _analytics_file()


@router.get("/quality/{page}", include_in_schema=False)
def quality_page(page: str):
    """GET /quality/{page}：质检的指标/模板/巡检三个子页，其余 slug 一律 404。"""
    if page not in {"metrics", "templates", "inspections"}:
        return JSONResponse({"error": "质量检查页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


@router.get("/qc/{page}", include_in_schema=False)
def legacy_quality_page(page: str):
    """旧版质检路径 GET /qc/{page} → 307 重定向到 /quality/*（device 落到设备台账）。

    307 而非 301：保留方法与查询串，且避免浏览器把旧路径永久缓存成新地址。
    """
    from fastapi.responses import RedirectResponse
    target = {"metric": "/quality/metrics", "template": "/quality/templates", "inspection": "/quality/inspections", "device": "/alerts/devices"}.get(page)
    if not target:
        return JSONResponse({"error": "质检页面不存在"}, status_code=404)
    return RedirectResponse(target, status_code=307)


@router.get("/alerts/{page}", include_in_schema=False)
def alert_page(page: str):
    """GET /alerts/{page}：设备预警的设备/测点/建议/规则/报告/问题六个子页（白名单校验）。"""
    if page not in {"devices", "points", "advice", "rules", "reports", "problems"}:
        return JSONResponse({"error": "设备预警页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


@router.get("/alert/report", include_in_schema=False)
@router.get("/alert/problem", include_in_schema=False)
@router.get("/alert/rule", include_in_schema=False)
def legacy_alert_page(request: Request):
    """旧版单数路径 GET /alert/{report|problem|rule} → 307 重定向到 /alerts/{reports|problems|rules}。

    目标由路径末段映射得出，映射表键必须与上面三个装饰器一一对应，新增旧路径需同时补键。
    """
    from fastapi.responses import RedirectResponse
    target = {"report": "reports", "problem": "problems", "rule": "rules"}[request.url.path.rsplit("/", 1)[1]]
    return RedirectResponse("/alerts/" + target, status_code=307)


@router.get("/account/profile", include_in_schema=False)
def account_profile_page():
    """Personal profile is a normal full-width page reached from the account menu.

    GET /account/profile：个人资料页（账号菜单入口，非工作区级页面，因此不校验 workspace）。
    """
    return FileResponse(str(PLATFORM_FILE))


@router.get("/analytics/{page}", include_in_schema=False)
def analytics_subpage(page: str):
    """GET /analytics/{page}：SQL / 历史 / 质量 / 数据源四个子页（白名单校验）。"""
    if page not in {"sql", "history", "quality", "sources"}:
        return JSONResponse({"error": "分析页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


@router.get("/report/{mode}/{report_id}", include_in_schema=False)
def report_page(mode: str, report_id: str):
    """GET /report/{mode}/{report_id}：报表的设计/基础/查看三种模式。

    mode 与 report_id 双重白名单；页面本身不校验数据权限，真正的可见性由报表 API 决定。
    """
    import re
    if mode not in {"designer", "basic", "view"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", report_id):
        return JSONResponse({"error": "报表页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


_SYSTEM_PAGES = {"users", "workspaces", "departments", "positions", "roles", "permissions", "menus", "sessions", "security", "models", "settings", "operations", "development"}


@router.get("/sso/callback", include_in_schema=False)
def oidc_callback_page():
    """The platform shell completes the JSON callback and stores the resulting session.

    GET /sso/callback：由平台壳读取 JSON 回调结果并落地会话（本后端不在此处交换令牌）。
    """
    return FileResponse(str(PLATFORM_FILE))


@router.get("/system/api", include_in_schema=False)
def legacy_api_center_page():
    """Keep old bookmarks valid after the API center moved into development.

    旧版 API 中心 GET /system/api → 307 重定向到 /system/development（开发设置下的新位置）。
    """
    return RedirectResponse("/system/development", status_code=307)


@router.get("/system/{page}", include_in_schema=False)
def system_page(page: str):
    """GET /system/{page}：系统治理页面，页面名以 _SYSTEM_PAGES 白名单为准。

    该集合是后端页面路由的白名单，新增系统页必须在此登记，否则直接 404。
    """
    if page not in _SYSTEM_PAGES:
        return JSONResponse({"error": "业务页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


@router.get("/system/users/{user_id}", include_in_schema=False)
def user_detail_page(user_id: str):
    """GET /system/users/{user_id}：用户详情页；user_id 需匹配 26 字符以内安全字符集。"""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", user_id):
        return JSONResponse({"error": "用户页面不存在"}, status_code=404)
    return FileResponse(str(PLATFORM_FILE))


@router.get("/runs", include_in_schema=False)
def existing_business_page(request: Request):
    """GET /runs：运行记录页面，直接映射同名静态 HTML。

    路径片段来自本路由固定的 '/runs'（非用户输入），因此可以直接拼文件名。
    """
    page = request.url.path.strip("/")
    return FileResponse(str(WEB_ROOT / (page + ".html")))


@router.get("/admin", include_in_schema=False)
def admin_index():
    """Dedicated administrator configuration route, separate from chat.

    GET /admin：独立的管理员配置前端（admin.html），与业务壳/对话页互不复用。
    """
    if ADMIN_FILE.exists():
        return FileResponse(str(ADMIN_FILE))
    return JSONResponse({"error": "管理员前端不存在"}, status_code=404)


@router.get("/app.js", include_in_schema=False)
def app_js_file():
    """GET /app.js：平台壳脚本，禁用缓存以便前端改动即时生效。"""
    p = WEB_ROOT / "app.js"
    if p.exists():
        return FileResponse(
            str(p), media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return JSONResponse({"error": "app.js 不存在"}, status_code=404)


@router.get("/app.css", include_in_schema=False)
def app_css_file():
    """GET /app.css：平台壳样式，同样禁用缓存。"""
    p = WEB_ROOT / "app.css"
    if p.exists():
        return FileResponse(
            str(p), media_type="text/css",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return JSONResponse({"error": "app.css 不存在"}, status_code=404)


@router.get("/assets/platform/chat-session.js", include_in_schema=False)
@router.get("/assets/platform/chat-session-view.js", include_in_schema=False)
@router.get("/assets/platform/chat-session.css", include_in_schema=False)
@router.get("/assets/platform/chat-dashboard-preview.js", include_in_schema=False)
@router.get("/assets/platform/chat-dashboard-preview.css", include_in_schema=False)
def chat_asset_file(request: Request):
    """聊天交互资源与页面同步更新，避免浏览器长期运行旧任务列表或滚动逻辑。"""
    name = request.url.path.rsplit("/", 1)[-1]
    allowed = {"chat-session.js", "chat-session-view.js", "chat-session.css",
               "chat-dashboard-preview.js", "chat-dashboard-preview.css"}
    if name not in allowed:
        return JSONResponse({"error": "聊天资源不存在"}, status_code=404)
    target = ASSETS_DIR / "platform" / name
    if not target.is_file():
        return JSONResponse({"error": "聊天资源不存在"}, status_code=404)
    return FileResponse(str(target), media_type="text/css" if name.endswith(".css") else "application/javascript",
                        headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/{page_name}.html", include_in_schema=False)
def module_page(page_name: str):
    """兜底的模块页路由 GET /{page_name}.html。

    安全约束：page_name 必须匹配 [A-Za-z0-9_-]+（不含点与斜杠），否则可能拼出 WEB_ROOT 之外的路径；
    旧文件名在此就地 307 重定向到新系统页（admin-members → /system/users、admin-models → /system/models）。
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]+", page_name):
        return JSONResponse({"error": "bad page"}, status_code=404)
    if page_name == "admin-members":
        return RedirectResponse("/system/users", status_code=307)
    if page_name == "admin-models":
        return RedirectResponse("/system/models", status_code=307)
    p = WEB_ROOT / (page_name + ".html")
    if p.exists():
        return FileResponse(str(p))
    return JSONResponse({"error": "page not found"}, status_code=404)


def mount_static(app) -> None:
    """Mount built assets and the original source asset-path compatibility alias.

    顺序与边界：先挂页面路由（必须先于下面的 StaticFiles，否则 /assets 挂载会遮蔽同前缀接口），
    再挂 /assets/analytics、/assets、/jimu-screen 三个静态目录；目录不存在时跳过对应挂载
    （前端未构建时只影响页面，不影响 API）。这里只暴露公开静态根，不额外放宽父目录边界。
    """
    app.include_router(router)
    analytics_dir = ASSETS_DIR / "analytics"
    if analytics_dir.exists():
        # The build directory can be a Windows junction on another drive.
        # Resolve this explicit public root without following arbitrary links
        # inside it or widening the parent /assets mount's boundary.
        app.mount("/assets/analytics", StaticFiles(directory=str(analytics_dir.resolve())), name="analytics-assets")
    if ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
    if JIMU_ASSETS_DIR.exists():
        app.mount(
            "/jimu-screen",
            StaticFiles(directory=str(JIMU_ASSETS_DIR.resolve())),
            name="jimu-screen-assets",
        )
