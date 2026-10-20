"""平台装配入口：模块注册、请求安全、事务和静态前端保持独立。"""

from contextlib import asynccontextmanager
from importlib import import_module
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, OperationalError

from .core.auth import LoginBudget
from .core.database import Database
from .core.errors import DomainError
from .core.files import FileService
from .core.secrets import SecretVault
from .core.settings import PROJECT_ROOT, Settings
from .registry import load_modules


def accepts_html(value: str) -> bool:
    """区分浏览器文档与默认健康探针，尊重显式禁止HTML的q=0。"""
    for part in value.split(','):
        media, *parameters = [item.strip() for item in part.split(';')]
        if media.lower() != 'text/html':
            continue
        quality = next((item.partition('=')[2].strip() for item in parameters
                        if item.partition('=')[0].strip().lower() == 'q'), '1')
        try:
            if 0 < float(quality) <= 1:
                return True
        except ValueError:
            continue
    return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    loaded, missing = load_modules(settings.enabled_modules)
    if missing and settings.environment == 'production':
        raise RuntimeError('缺少启用模块：' + ', '.join(m.key for m in missing))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings)
        app.state.database = database
        try:
            database.initialize()
            app.state.secret_vault = SecretVault(database, settings)
            app.state.file_reader = lambda id, actor, session: {
                **FileService(session, actor, settings).get(id),
                'text': FileService(session, actor, settings).read_text(id),
            }
            yield
        finally:
            database.dispose()

    app = FastAPI(title='制造业 Agent 平台', version='0.1.0', lifespan=lifespan)
    app.state.settings = settings
    app.state.bootstrap_lock = Lock()
    app.state.login_budget = LoginBudget()
    app.state.modules = [m for m, _ in loaded]
    app.state.missing_modules = missing
    try:
        app.state.service_actor_resolver = import_module(
            'agent_platform.modules.identity.service_accounts').resolve_service_actor
    except ModuleNotFoundError as exc:
        if exc.name != 'agent_platform.modules.identity.service_accounts':
            raise

    @app.middleware('http')
    async def request_boundary(request: Request, call_next):
        trace_id = uuid4().hex
        request.state.trace_id = trace_id
        origin = request.headers.get('origin')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin and origin not in settings.allowed_origins:
            return JSONResponse({'error': {'code': 'origin_denied', 'message': '请求来源未获授权', 'fields': {}},
                                 'trace_id': trace_id}, status_code=403)
        response = await call_next(request)
        response.headers['X-Trace-Id'] = trace_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message, 'fields': exc.fields},
                             'trace_id': getattr(request.state, 'trace_id', '')}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = {'.'.join(str(v) for v in e['loc'][1:]): '请检查该字段的值和格式' for e in exc.errors()}
        return JSONResponse({'error': {'code': 'validation_failed', 'message': '请检查标记的字段', 'fields': fields},
                             'trace_id': getattr(request.state, 'trace_id', '')}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        return JSONResponse({'error': {'code': 'data_conflict', 'message': '名称、编号重复或记录仍被引用', 'fields': {}},
                             'trace_id': getattr(request.state, 'trace_id', '')}, status_code=409)

    @app.exception_handler(OperationalError)
    async def storage_error(request: Request, exc: OperationalError):
        return JSONResponse({'error': {'code': 'storage_unavailable', 'message': '数据存储暂不可用，请联系管理员核查', 'fields': {}},
                             'trace_id': getattr(request.state, 'trace_id', '')}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # 客户端只得到可追溯编号，不暴露连接信息、SQL或第三方响应正文。
        trace_id = getattr(request.state, 'trace_id', '')
        logging.getLogger('agent_platform').error('请求异常 trace_id=%s type=%s', trace_id, type(exc).__name__)
        return JSONResponse({'error': {'code': 'internal_error', 'message': '本次操作未完成，请联系管理员并提供请求编号', 'fields': {}},
                             'trace_id': trace_id}, status_code=500)

    @app.get('/health')
    def health(request: Request):
        # 菜单与探针沿用同一路径：浏览器刷新拿页面，默认JSON探针保持原契约。
        document = PROJECT_ROOT / 'apps/web/dist/index.html'
        if document.is_file() and accepts_html(request.headers.get('accept', '')):
            return FileResponse(document, headers={'Vary': 'Accept', 'Cache-Control': 'no-cache'})
        return JSONResponse({'status': 'ok', 'service': 'manufacturing-platform',
                'modules': [m.key for m in app.state.modules],
                'missing_modules': [m.key for m in app.state.missing_modules]}, headers={'Vary': 'Accept'})

    for _, router in loaded:
        app.include_router(router)

    dist = PROJECT_ROOT / 'apps' / 'web' / 'dist'
    if dist.is_dir():
        class SPAFiles(StaticFiles):
            async def get_response(self, path, scope):
                from starlette.exceptions import HTTPException
                try:
                    return await super().get_response(path, scope)
                except HTTPException as exc:
                    if exc.status_code == 404 and '.' not in Path(path).name and not path.startswith('api/'):
                        return await super().get_response('index.html', scope)
                    raise
        app.mount('/', SPAFiles(directory=dist, html=True), name='portal')
    return app
