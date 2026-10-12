# -*- coding: utf-8 -*-
"""FastAPI application entrypoint.

职责：FastAPI 应用的唯一装配点——创建 app 实例、注册中间件/异常处理器/路由、挂载静态前端，
并用 lifespan 管理启动与停机。

装配顺序（改动时需保持）：
1) 模块导入期：先打 Windows asyncio 套接字补丁，再设置 pydantic 告警过滤，避免影响后续导入；
2) create_app()：构造 FastAPI → 注册 CORS 与三层异常处理器 → 注册业务路由
   （auth 路由先行，再由 modules.router_index 聚合其余模块）→ 生成 OpenAPI 契约 →
   最后由 static_router 挂载页面路由与静态资源（页面路由必须最后挂，避免吃掉 API 路径）；
3) lifespan 启动：bootstrap() 建表/补种子数据 → 置 app.state.ready=True → 拉起读连接池清理任务，
   以及受 SCHEDULER_ENABLED 与内核租约约束的调度任务；
4) lifespan 停机：先复位 ready（让 /api/ready 立刻返回 503 以便摘流量），再取消后台任务并释放连接池。

关键约束：docs/redoc/openapi 三个端点显式关闭，对外契约由 modules.platform.openapi_contract 提供；
`app` 在模块导入时即创建，因此 `main:app` 被 uvicorn 导入就会执行整套装配。
"""
from __future__ import annotations

import logging
import asyncio
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic.warnings import UnsupportedFieldAttributeWarning

from .config import settings
from .core.observability import configure_logging, RequestLoggingMiddleware, log_event

configure_logging(settings)
from .core.windows_asyncio import install_windows_socket_cleanup
from .errors import AppError
from .seed import bootstrap

logger = logging.getLogger("app")

# Apply before Uvicorn accepts connections, including CLI and CMD entrypoints.
# 必须在事件循环建立监听之前生效：补丁只修 TCP 清理，不改变事件循环策略与子进程支持。
install_windows_socket_cleanup()

# FastAPI rebuilds request-field adapters on first use.  With the Pydantic
# version bundled by the desktop runtime that emits false-positive warnings
# for model-level ``repr`` and ``alias`` metadata even though Pydantic already
# applies those settings to the owning fields.  Preserve the schema/redaction
# metadata and suppress only those two compatibility messages so operators do
# not mistake them for a bad API configuration.
warnings.filterwarnings(
    "ignore",
    message=r"The '(?:repr|alias)' attribute .* was provided to the `Field\(\)` function.*",
    category=UnsupportedFieldAttributeWarning,
)


def register_routers(app: FastAPI) -> None:
    """把全后端路由挂到应用上。

    顺序有语义：auth 路由先注册（登录/会话是其余接口的前置），随后由 modules.router_index
    按固定清单聚合各业务模块路由——清单内某个模块导入失败会让启动直接失败（fail-fast），
    不做静默跳过。副作用：写 app.state.loaded_modules（供 /api/ready 报告已加载模块）。
    """
    from .modules.auth.router import router as auth_router
    app.include_router(auth_router)
    from .modules.router_index import include_all
    include_all(app)


def _warm_model():
    """Fire a trivial model call in the background to avoid a slow cold start.

    用途：后台线程预热模型通道，避免首个真实请求承担建连/加载开销。
    约束：失败只记 info 日志（预热不是启动前提，缺密钥时也必须能起来）；只在线程内抛错，
    不影响主进程启动结果。
    """
    import threading

    def _do():
        """线程体：忽略一切失败，只在日志留痕（预热失败不得影响服务可用性）。"""
        try:
            from .providers import model
            model.chat_for_run("reasoning", [{"role": "user", "content": "ping"}], temperature=0.1)
        except Exception as exc:
            logger.info("model warmup skipped: %s", exc)

    t = threading.Thread(target=_do, daemon=True)
    t.start()


def create_app() -> FastAPI:
    """构造并返回 FastAPI 应用实例（中间件 → 异常处理 → 路由 → 静态资源，顺序见模块 docstring）。

    无参数、无外部副作用：启动期动作全部放在 lifespan 里，因此测试可以只构建 app 而不触发建表。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """启动/停机钩子：建表补种子 → 拉起后台任务；停机时先摘就绪再收任务。"""
        log_event("server.starting", "服务启动，正在初始化平台")
        bootstrap()
        app.state.ready = True
        log_event("server.ready", "平台初始化完成", scheduler_enabled=settings.SCHEDULER_ENABLED)
        stop_scheduler = asyncio.Event()
        from .modules.analytics.connection_pool import dispose_read_pools, reap_idle_pools

        async def read_pool_maintenance():
            """周期性回收分析库的闲置只读连接池（30s 一次，停机事件可立即中断等待）。"""
            while not stop_scheduler.is_set():
                try:
                    await asyncio.wait_for(stop_scheduler.wait(), timeout=30)
                except asyncio.TimeoutError:
                    await asyncio.to_thread(reap_idle_pools)

        pool_task = asyncio.create_task(read_pool_maintenance(), name="analytics-read-pool-cleanup")
        scheduler_task = None
        if settings.SCHEDULER_ENABLED:
            async def scheduler_worker():
                """定时任务派发循环：先抢内核租约，只有持锁进程才执行到期任务。

                约束：HTTP 与 HTTPS 监听可能同时存在，因此业务锁之外还要有内核级租约，
                确保同一时刻只有一个进程派发；租约计数的是真实工作线程，取消 await 只是
                请求释放，锁要等 run_due_jobs 返回后才真正让出。
                """
                from .modules.system_tools.scheduler_lease import LeaseUnavailable, SchedulerLease
                from .modules.system_tools.schedules import run_due_jobs

                # An HTTP and an HTTPS listener can share this project.  Only
                # the process holding the kernel lease may dispatch jobs; a
                # second listener waits and takes over after the owner exits.
                while not stop_scheduler.is_set():
                    lease = SchedulerLease()
                    try:
                        acquired = lease.acquire()
                    except Exception:
                        logger.exception("scheduled-job lease acquisition failed")
                        acquired = False
                    if not acquired:
                        logger.info("scheduled-job lease is held by another listener; waiting")
                        try:
                            await asyncio.wait_for(
                                stop_scheduler.wait(),
                                timeout=settings.SCHEDULER_POLL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            pass
                        continue
                    try:
                        logger.info("scheduled-job lease acquired")
                        while not stop_scheduler.is_set():
                            try:
                                # The lease counts the actual worker thread.
                                # Cancelling this await (including event-loop
                                # shutdown) requests release, but the kernel
                                # lock remains held until run_due_jobs returns.
                                await asyncio.to_thread(lease.run, run_due_jobs)
                            except asyncio.CancelledError:
                                lease.release()
                                raise
                            except LeaseUnavailable:
                                break
                            except Exception:
                                logger.exception("scheduled-job polling failed")
                            try:
                                await asyncio.wait_for(
                                    stop_scheduler.wait(),
                                    timeout=settings.SCHEDULER_POLL_SECONDS,
                                )
                            except asyncio.TimeoutError:
                                pass
                    finally:
                        lease.release()
                        logger.info("scheduled-job lease release requested")
            scheduler_task = asyncio.create_task(scheduler_worker(), name="cloud-brain-business-scheduler")
        try:
            yield
        finally:
            # 停机顺序：先撤销就绪状态（负载均衡/探针立即摘流量），再停后台任务与连接池。
            app.state.ready = False
            stop_scheduler.set()
            await pool_task
            if scheduler_task:
                try:
                    await asyncio.wait_for(scheduler_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    scheduler_task.cancel()
            dispose_read_pools()
            log_event("server.stopped", "后台任务和连接池已关闭")

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ready = False
    app.add_middleware(RequestLoggingMiddleware)

    # CORS：默认 "*" 时不能同时 allow_credentials（浏览器规范禁止二者并用），
    # 因此只有显式配置来源才放开凭据，见 config.CORS_ORIGINS。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS if settings.CORS_ORIGINS != ["*"] else ["*"],
        allow_credentials=settings.CORS_ORIGINS != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        """业务异常统一响应：HTTP 状态码取异常自带值，响应体带稳定 code 与 extra 透传字段。"""
        log_event("http.business_error", "接口返回业务错误", level=logging.WARNING,
                  error_code=exc.code, status=exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": exc.message, **exc.extra},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        """请求体校验失败（422）：只回传字段位置与错误类型，避免回显口令等原始输入。"""
        # Pydantic's default error payload can echo plaintext password inputs.
        issues = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
                  for item in exc.errors()]
        log_event("http.validation_error", "接口参数校验未通过", level=logging.WARNING,
                  error_code="validation_error", issue_count=len(issues))
        return JSONResponse(status_code=422, content={"ok": False, "code": "validation_error",
                            "message": "输入参数不符合要求", "issues": issues})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """兜底异常处理：记完整堆栈到日志，对外只回统一 500 文案，不泄露内部细节。"""
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "code": "internal_error", "message": "服务处理失败，请联系管理员并查看运行日志"},
        )

    @app.get("/api/health")
    def health():
        """存活探针（不查库）：只要进程能响应即返回 ok，用于容器重启判定。"""
        return {"ok": True, "app": settings.APP_ENV}

    @app.get("/api/ready", tags=["health"], summary="检查数据库与模块启动就绪状态")
    def readiness():
        """就绪探针（GET /api/ready）：lifespan 未完成或数据库不通时返回 503。"""
        from sqlalchemy import text
        from .db import get_engine

        if not app.state.ready:
            return JSONResponse({"ok": False, "code": "not_ready"}, status_code=503)
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False, "code": "database_unavailable"}, status_code=503)
        return {"ok": True, "modules": app.state.loaded_modules}

    @app.get("/api/ai/ready", tags=["health"], summary="检查当前对话引擎的本机运行条件")
    def ai_readiness():
        """独立报告对话引擎安装状态；实际会话仍须通过原生组件启动握手。"""
        from .modules.runtime.engine import runtime_health

        result = runtime_health()
        return JSONResponse({"ok": bool(result.get("ready")), **result},
                            status_code=200 if result.get("ready") else 503)

    register_routers(app)
    from .modules.platform.openapi_contract import install_openapi_contract
    install_openapi_contract(app)
    # 静态/页面路由最后挂载：其中的通配路径（如 /{page_name}.html、/workflows/{id}）不得遮挡业务 API。
    from .static_router import mount_static
    mount_static(app)
    return app


def create_server() -> FastAPI:
    """Create app plus kick off model warmup (used by uvicorn/managers).

    与 create_app 的区别：额外触发一次后台模型预热，供 uvicorn 启动入口使用；
    测试/脚本若只想要应用本身请用 create_app。
    """
    application = create_app()
    _warm_model()
    return application


# 模块导入即装配：uvicorn 以 "main:app" 形式加载时会直接执行上面的注册流程。
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
