# -*- coding: utf-8 -*-
"""Registers all module routers onto the FastAPI app."""
from __future__ import annotations

import logging
import importlib

logger = logging.getLogger("router_index")

from app.business_worker.registry import router_modules

# 兼容现有导入方，但实际注册来源只有可追踪的业务插件目录。
_MODULES = router_modules()


def include_all(app) -> None:
    """Load every required module exactly once; a broken import fails startup."""
    loaded = []
    for mod in _MODULES:
        module = importlib.import_module(f"app.{mod}")
        existing = {(getattr(route, "path", ""), method) for route in app.routes
                    for method in getattr(route, "methods", [])}
        declared = {(route.path, method) for route in module.router.routes
                    for method in getattr(route, "methods", [])}
        # 历史测试宿主先单独安装认证模块；相同模块不再次注册。
        if not declared.issubset(existing):
            app.include_router(module.router)
        loaded.append(mod)
    app.state.loaded_modules = loaded
