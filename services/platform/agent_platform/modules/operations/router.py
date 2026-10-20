"""运维读接口不隐式访问外部依赖；备份仅操作本项目独立平台文件。"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import time

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ...core.context import Actor, require_permission
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.models import AuditEvent, ResourceRecord, Task, new_id
from ...core.records import RecordStore
from ...core.settings import get_settings
from .support_router import router as support_router

router = APIRouter(prefix='/api/v1', tags=['运行与运营'])
router.include_router(support_router)


@router.post('/engine-callbacks/{engine}')
async def engine_callback(engine: str, request: Request,
                          integration_id: str = Header(..., alias='X-Integration-Id'),
                          workspace: str = Header(..., alias='X-Workspace-Id'),
                          timestamp: str = Header(..., alias='X-Callback-Timestamp'),
                          signature: str = Header(..., alias='X-Callback-Signature'),
                          session: Session = Depends(get_session)):
    from ..integrations.router import callback
    from ..integrations.service import INTEGRATION
    record = session.scalar(select(ResourceRecord).where(ResourceRecord.id == integration_id,
        ResourceRecord.kind == INTEGRATION, ResourceRecord.workspace_id == workspace, ResourceRecord.deleted.is_(False)))
    if record is None or record.data.get('engine') != engine:
        raise DomainError(404, 'callback_integration_not_found', '回调来源与登记实例不一致')
    # 复用原处理器的HMAC/重放/归属校验，不因兼容路由绕过签名验证。
    return await callback(integration_id, request, workspace, timestamp, signature, session)


@router.get('/operations/overview')
def overview(request: Request, actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    rows = session.execute(select(Task.status, func.count()).where(Task.workspace_id == actor.workspace_id).group_by(Task.status)).all()
    return {'service': 'manufacturing-platform', 'status': 'running', 'task_counts': dict(rows),
            'enabled_modules': [m.key for m in request.app.state.modules],
            'missing_modules': [m.key for m in request.app.state.missing_modules],
            'native_runtime': {'deployment': 'local_process', 'verified': False}}


@router.get('/operations/dependencies')
def dependencies(request: Request, actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    from ..integrations.service import INTEGRATION
    # 原生依赖仅显示已登记实例的最近状态；GET不能悄悄触发原生或业务库测试。
    rows = RecordStore(session, actor).list(INTEGRATION, include_archived=True)['items']
    return {'items': [{'id': 'platform', 'name': '平台业务服务', 'status': 'running'},
            *[{'id': r['id'], 'name': r['name'], 'status': r.get('connection_status', 'not_checked'),
                'last_checked_at': r.get('last_checked_at'), 'engine': r.get('engine')} for r in rows]],
            'checked_external_dependencies': False}


@router.get('/operations/usage')
@router.get('/operational-dashboards')
def usage(actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    rows = session.execute(select(Task.engine, Task.kind, Task.status, func.count()).where(Task.workspace_id == actor.workspace_id)
                            .group_by(Task.engine, Task.kind, Task.status)).all()
    total, succeeded, failed, unknown = 0, 0, 0, 0
    items = []
    for engine, kind, status, count in rows:
        total += count
        succeeded += count if status == 'succeeded' else 0
        failed += count if status == 'failed' else 0
        unknown += count if status == 'unknown' else 0
        items.append({'engine': engine, 'kind': kind, 'status': status, 'count': count})
    finished = succeeded + failed
    return {'items': items, 'total': total, 'succeeded': succeeded, 'failed': failed, 'unknown': unknown,
            'success_rate': succeeded / finished if finished else None, 'cost': None}


@router.get('/audit-events')
def audit_events(q: str = '', limit: int = 50, offset: int = 0,
                   actor: Actor = Depends(require_permission('m14.audit')), session: Session = Depends(get_session)):
    rules = [AuditEvent.workspace_id == actor.workspace_id]
    if q:
        rules.append(AuditEvent.action.contains(q, autoescape=True))
    total = session.scalar(select(func.count()).select_from(AuditEvent).where(*rules)) or 0
    rows = session.scalars(select(AuditEvent).where(*rules).order_by(AuditEvent.created_at.desc())
                            .limit(min(max(limit, 1), 200)).offset(max(offset, 0)))
    keys = ('id', 'user_id', 'action', 'resource_type', 'resource_id', 'details', 'created_at')
    return {'items': [{k: getattr(r, k) for k in keys} for r in rows], 'total': total}


@router.get('/audit-events/{id}')
def audit_event(id: str, actor: Actor = Depends(require_permission('m14.audit')), session: Session = Depends(get_session)):
    row = session.scalar(select(AuditEvent).where(AuditEvent.id == id, AuditEvent.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'audit_event_not_found', '审计记录不存在')
    return {k: getattr(row, k) for k in ('id', 'user_id', 'action', 'resource_type', 'resource_id', 'details', 'created_at')}


@router.get('/extension-registry')
def extension_registry(request: Request, actor: Actor = Depends(require_permission('m16.read'))):
    rows = [{'id': m.key, 'name': m.name, 'feature_modules': list(m.feature_modules),
             'status': 'enabled', 'contract_version': '1'} for m in request.app.state.modules]
    return {'items': rows, 'total': len(rows)}


@router.get('/api-catalog')
def api_catalog(request: Request, actor: Actor = Depends(require_permission('m16.read'))):
    schema = request.app.openapi()
    rows = [{'method': method.upper(), 'path': path, 'operation_id': operation.get('operationId'),
             'summary': operation.get('summary', '')} for path, methods in schema['paths'].items()
            for method, operation in methods.items() if method in {'get', 'post', 'patch', 'delete', 'put'}]
    return {'items': rows, 'total': len(rows), 'source': 'running-platform-openapi'}


@router.get('/integration-bindings')
def integration_bindings(actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    from ..integrations.service import BINDING, AUTOMATION, SPACE, IDENTITY, WORKSPACE_MAP
    rows = []
    for kind in (BINDING, AUTOMATION, SPACE, IDENTITY, WORKSPACE_MAP):
        rows.extend({'kind': kind, **r} for r in RecordStore(session, actor).list(kind, limit=100)['items'])
    return {'items': rows, 'total': len(rows)}


@router.get('/integration-bindings/{id}')
def integration_binding(id: str, actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    from ..integrations.service import BINDING, AUTOMATION, SPACE, IDENTITY, WORKSPACE_MAP
    for kind in (BINDING, AUTOMATION, SPACE, IDENTITY, WORKSPACE_MAP):
        try:
            return {'kind': kind, **RecordStore(session, actor).get(kind, id)}
        except DomainError as exc:
            if exc.status != 404:
                raise
    raise DomainError(404, 'integration_binding_not_found', '集成映射不存在')


@router.get('/operations/runs')
def operation_runs(status: str = '', limit: int = 50,
                     actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    rules = [Task.workspace_id == actor.workspace_id]
    if status:
        rules.append(Task.status == status)
    rows = list(session.scalars(select(Task).where(*rules).order_by(Task.created_at.desc()).limit(min(max(limit, 1), 200))))
    return {'items': [{k: getattr(r, k) for k in ('id', 'title', 'kind', 'engine', 'status', 'source_run_id', 'created_at', 'updated_at')} for r in rows],
            'total': session.scalar(select(func.count()).select_from(Task).where(*rules)) or 0}


@router.get('/releases')
def releases(actor: Actor = Depends(require_permission('m16.read')), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    rows = []
    for kind in ('application-publication', 'graph-publication'):
        for row in store.list(kind, limit=100)['items']:
            rows.append({'id': row['id'], 'name': row['name'], 'resource_type': kind,
                         'version': row.get('version', str(row['revision'])), 'status': 'published', 'created_at': row['created_at']})
    from ..reports.models import Report
    for report in session.scalars(select(Report).where(Report.workspace_id == actor.workspace_id)):
        published = getattr(report, 'published_revision', None)
        if published:
            rows.append({'id': report.id, 'name': report.name, 'resource_type': 'report',
                         'version': str(published), 'status': 'published', 'created_at': report.updated_at})
    return {'items': rows, 'total': len(rows)}


@router.post('/integration-reconciliations')
def reconciliation(body: dict, request: Request, actor: Actor = Depends(require_permission('m16.admin')),
                      session: Session = Depends(get_session)):
    id = str(body.get('run_id', ''))
    if not id:
        raise DomainError(422, 'run_required', '请选择需要核对的来源运行')
    from ..integrations.service import IntegrationService
    return IntegrationService(session, actor, request).refresh_run(id)


@router.get('/backups')
def backups(actor: Actor = Depends(require_permission('m16.admin')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).list('backup')


@router.post('/backup-jobs', status_code=201)
def create_backup(request: Request, actor: Actor = Depends(require_permission('m16.admin')),
                    session: Session = Depends(get_session)):
    settings = get_settings(request)
    url = make_url(settings.database_url)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        raise DomainError(422, 'backup_provider_required', '当前数据库需配置对应备份工具')
    source = Path(url.database).resolve()
    if not source.is_relative_to(settings.data_dir) or not source.is_file() or source.stat().st_size > 50 * 1024 * 1024:
        raise DomainError(422, 'backup_budget_exceeded', '当前目标不在平台文件范围或超过单次备份预算')
    # 结束当前只读事务，避免在线备份与本请求的写锁互相等待。
    session.commit()
    folder = settings.data_dir / 'backups'
    folder.mkdir(exist_ok=True)
    filename = new_id() + '.sqlite3'
    target = folder / filename
    started = time.monotonic()

    def progress(status, remaining, total):
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() - started > 5:
            raise DomainError(503, 'backup_stopped', '备份遇到锁等待或超过预算，已停止，请核查后再操作')

    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1) as src, sqlite3.connect(target, timeout=1) as dst:
        src.backup(dst, pages=64, progress=progress, sleep=0)
    row = RecordStore(session, actor).create('backup', {'name': '平台数据库备份', 'filename': filename,
        'size': target.stat().st_size, 'status': 'completed', 'elapsed_ms': round((time.monotonic() - started) * 1000),
        'contains_files': False, 'master_key_included': False}, visibility='private')
    return row


@router.post('/restore-drills', status_code=201)
def restore_drill(body: dict, request: Request, actor: Actor = Depends(require_permission('m16.admin')),
                    session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    backup = store.get('backup', str(body.get('backup_id', '')))
    settings = get_settings(request)
    source = (settings.data_dir / 'backups' / backup['filename']).resolve()
    if not source.is_relative_to(settings.data_dir / 'backups') or not source.is_file():
        raise DomainError(404, 'backup_file_missing', '备份文件不存在')
    target = settings.data_dir / 'restore-drills' / (new_id() + '.sqlite3')
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, target)
    started = time.monotonic()
    with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=1) as connection:
        connection.set_progress_handler(lambda: int(time.monotonic() - started > 2), 1000)
        result = connection.execute('PRAGMA quick_check').fetchone()[0]
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if result != 'ok' or not {'users', 'workspaces', 'resource_records'}.issubset(tables):
        raise DomainError(422, 'restore_integrity_failed', '恢复文件的完整性校验未通过')
    return store.create('restore-drill', {'name': '隔离恢复演练', 'backup_id': backup['id'],
        'status': 'completed', 'integrity': result, 'table_count': len(tables), 'live_database_modified': False}, visibility='private')
