"""仅在内存中分发业务 HTTP 合同，不开启监听器或旧聊天生命周期。"""
from __future__ import annotations

import asyncio
import base64
import importlib
import logging

from fastapi import FastAPI, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .context import request_object
from .registry import router_modules, capabilities

_app = None


def create_application():
    """保留业务原路由，替换会话读取并移除旧引擎执行入口。"""
    from app.config import settings
    from app.errors import AppError
    from app.common.deps import get_current_context, get_db
    app = FastAPI(title=settings.APP_NAME, version="0.3.0", docs_url=None, redoc_url=None, openapi_url=None)
    async def business_context(request: Request, db=Depends(get_db), x_workspace_id: str | None = Header(default=None)):
        """把已认证的请求发起者传给辅助模型台账，不把调用算到工作区首位成员。"""
        from .context import request_actor
        ctx = get_current_context(request, db=db, x_workspace_id=x_workspace_id, credentials=None)
        actor = request_actor.get()
        if actor is not None:
            actor.update(user_id=ctx.user_id, workspace_id=ctx.workspace_id)
        return ctx
    app.dependency_overrides[get_current_context] = business_context
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                       allow_credentials=settings.CORS_ORIGINS != ["*"], allow_methods=["*"], allow_headers=["*"])
    for module_name in router_modules():
        module = importlib.import_module("app." + module_name)
        app.include_router(module.router)
    blocked = {
        ("POST", "/api/chat/conversations/{conversation_id}/send"),
        ("GET", "/api/chat/conversations/{conversation_id}"),
        ("DELETE", "/api/chat/conversations/{conversation_id}"),
        ("GET", "/api/chat/runs/{run_id}/events"),
        ("POST", "/api/chat/runs/{run_id}/cancel"),
    }
    app.router.routes = [route for route in app.router.routes if not any(
        (method, getattr(route, "path", "")) in blocked for method in getattr(route, "methods", []))]

    @app.exception_handler(AppError)
    async def business_error(request, exc):
        return JSONResponse({"ok": False, "code": exc.code, "message": exc.message, **exc.extra}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"ok": False, "code": "validation_error", "message": "输入参数不符合要求",
                             "issues": [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]} for item in exc.errors()]}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.exception("业务接口失败：%s", request.url.path)
        return JSONResponse({"ok": False, "code": "internal_error", "message": "业务处理失败"}, status_code=500)

    @app.get("/api/chat/capabilities")
    def chat_capabilities(ctx=Depends(get_current_context)):
        return capabilities()

    @app.get("/api/chat/conversations/{conversation_id}")
    def detail(conversation_id: str, ctx=Depends(get_current_context)):
        from .chat import history_for_context
        return history_for_context(ctx, conversation_id)

    @app.delete("/api/chat/conversations/{conversation_id}")
    def archive(conversation_id: str, ctx=Depends(get_current_context)):
        from datetime import datetime
        from sqlalchemy import select
        from app.models import Run
        from .chat import owned_conversation
        from .context import cancel
        conv = owned_conversation(ctx, conversation_id)
        for run in ctx.db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.workspace_id == ctx.workspace_id,
                                                   Run.status.in_(["created", "queued", "running"]))):
            cancel(run.id)
        conv.archived_at = datetime.utcnow()
        ctx.db.commit()
        return {"ok": True}

    from app.modules.platform.openapi_contract import install_openapi_contract
    install_openapi_contract(app)
    from app.static_router import mount_static
    mount_static(app)
    return app


def application():
    global _app
    if _app is None:
        _app = create_application()
    return _app


def routes():
    return [{"path": route.path, "methods": sorted(route.methods),
             "operation_id": route.operation_id or route.name}
            for route in application().routes if getattr(route, "methods", None)]


def dispatch(raw):
    """由调用线程运行独立 ASGI 周期，上传下载保留原始字节和重复头。"""
    async def execute():
        app = application()
        scope = dict(request_object(raw).scope)
        scope["app"] = app
        body = base64.b64decode(raw.get("body_base64") or "", validate=True)
        consumed = False
        completed = asyncio.Event()
        response = {"status": 500, "headers": []}
        chunks = []

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            await completed.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                response["headers"] = [[k.decode("latin-1"), v.decode("latin-1")] for k, v in message.get("headers", [])]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    completed.set()
        try:
            await app(scope, receive, send)
        except Exception:
            if not completed.is_set():
                raise
        response["body_base64"] = base64.b64encode(b"".join(chunks)).decode("ascii")
        return response
    from .context import request_actor
    token = request_actor.set({})
    try:
        return asyncio.run(execute())
    finally:
        request_actor.reset(token)
