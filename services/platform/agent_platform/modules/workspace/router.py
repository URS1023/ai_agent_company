"""首页使用真实业务汇总，任务操作委托实际执行者，不允许任意改状态。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...core.context import Actor, get_actor, require_permission
from ...core.database import Base, get_session
from ...core.errors import DomainError
from ...core.models import Notification, ResourceRecord, Task, TaskEvent, now_iso
from ...core.records import RecordStore
from ...core.tasks import TASK_PERMISSIONS, TaskService, task_dict
from .quality_overview import first_inspection_trend
from .activity import recent_activities

router = APIRouter(prefix='/api/v1', tags=['工作台与任务'])


def task_scope(actor):
    conditions = [Task.workspace_id == actor.workspace_id]
    if not actor.is_admin and not actor.can('m16.admin'):
        delegated = [f'm{n:02d}.read' for n in range(1, 18) if actor.can(f'm{n:02d}.read')]
        blocked_kinds = [kind for kind, permission in TASK_PERMISSIONS.items() if not actor.can(permission)]
        own = and_(Task.user_id == actor.user_id,
                   or_(Task.access_permission.in_(delegated), and_(Task.access_permission.is_(None), Task.kind.not_in(blocked_kinds))))
        conditions.append(or_(own, Task.access_permission.in_(delegated)))
    return conditions


@router.get('/tasks')
def tasks(q: str = '', status: str = '', kind: str = '', limit: int = 50, offset: int = 0,
          actor: Actor = Depends(require_permission('m01.read')), session: Session = Depends(get_session)):
    conditions = task_scope(actor)
    if status:
        conditions.append(Task.status == status)
    if kind:
        conditions.append(Task.kind == kind)
    if q:
        conditions.append(Task.title.contains(q, autoescape=True))
    rows = session.scalars(select(Task).where(*conditions).order_by(Task.created_at.desc(), Task.id)
                           .execution_options(yield_per=100))
    visible = list(TaskService(session, actor).visible(rows))
    begin, size = max(offset, 0), min(max(limit, 1), 200)
    return {'items': [task_dict(r) for r in visible[begin:begin + size]], 'total': len(visible)}


@router.get('/tasks/{id}')
def task(id: str, actor: Actor = Depends(require_permission('m01.read')), session: Session = Depends(get_session)):
    return TaskService(session, actor).get(id)


@router.post('/tasks/{id}/cancellations')
def cancel_task(id: str, request: Request, actor: Actor = Depends(require_permission('m01.write')),
                  session: Session = Depends(get_session)):
    service = TaskService(session, actor)
    row = service.get(id)
    if row['status'] in {'cancelled', 'succeeded', 'failed'}:
        return row
    if row['status'] == 'queued':
        return service.transition(id, 'cancelled')
    if row['engine'] == 'dify':
        from ..integrations.service import IntegrationService
        IntegrationService(session, actor, request).cancel_task(id)
        return service.get(id)
    raise DomainError(409, 'task_not_cancellable', '该任务当前不能停止，请查看执行详情')


@router.get('/tasks/{id}/events')
def events(id: str, after: int = 0, actor: Actor = Depends(require_permission('m01.read')),
           session: Session = Depends(get_session)):
    TaskService(session, actor).get(id)
    rows = list(session.scalars(select(TaskEvent).where(TaskEvent.task_id == id, TaskEvent.sequence > max(after, 0))
                                .order_by(TaskEvent.sequence).limit(200)))
    return {'items': [{'sequence': r.sequence, 'type': r.type, 'data': r.data, 'created_at': r.created_at} for r in rows],
            'next_cursor': rows[-1].sequence if rows else after, 'has_more': len(rows) == 200}


@router.post('/tasks/{id}/inputs')
def task_inputs(id: str, body: dict, request: Request, actor: Actor = Depends(require_permission('m01.write')),
                  session: Session = Depends(get_session)):
    row = TaskService(session, actor).get(id)
    if row['status'] != 'waiting_review':
        raise DomainError(409, 'task_not_waiting', '该任务当前不等待补充信息')
    handler = getattr(request.app.state, 'task_input_handlers', {}).get(row['kind'])
    if handler is None:
        raise DomainError(409, 'native_input_required', '请打开原任务页面完成此步骤')
    return handler(session, actor, row, body.get('inputs', {}))


@router.post('/confirmations/{id}/decisions')
def confirmation(id: str, body: dict, request: Request, actor: Actor = Depends(require_permission('m01.write')),
                   session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    row = store.get('confirmation', id)
    if row.get('status') in {'approved', 'rejected'}:
        return row
    if body.get('decision') not in {'approve', 'reject'}:
        raise DomainError(422, 'decision_required', '请选择确认或拒绝')
    handler = getattr(request.app.state, 'confirmation_handlers', {}).get(row.get('action'))
    if body['decision'] == 'approve' and handler is None:
        raise DomainError(409, 'action_not_available', '此动作需在原业务页面确认')
    outputs = handler(session, actor, row) if body['decision'] == 'approve' else None
    return store.update('confirmation', id, {'status': 'approved' if body['decision'] == 'approve' else 'rejected',
                        'decided_by': actor.user_id, 'note': str(body.get('note', '')), 'outputs': outputs}, body.get('revision', 0))


@router.get('/notifications')
def notifications(actor: Actor = Depends(require_permission('m01.read')), session: Session = Depends(get_session)):
    rows = list(session.scalars(select(Notification).where(Notification.workspace_id == actor.workspace_id,
                  Notification.user_id == actor.user_id).order_by(Notification.created_at.desc()).limit(100)))
    visible = []
    for row in rows:
        if row.resource_type == 'task':
            try:
                TaskService(session, actor).get(row.resource_id)
            except DomainError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
        visible.append(row)
    return {'items': [{k: getattr(r, k) for k in ('id', 'title', 'resource_type', 'resource_id', 'read_at', 'created_at')} for r in visible],
            'total': len(visible), 'unread': sum(r.read_at is None for r in visible)}


@router.post('/notifications/{id}/receipts')
def receipt(id: str, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    row = session.scalar(select(Notification).where(Notification.id == id, Notification.workspace_id == actor.workspace_id,
                                                   Notification.user_id == actor.user_id))
    if row is None:
        raise DomainError(404, 'notification_not_found', '通知不存在')
    if row.resource_type == 'task':
        TaskService(session, actor).get(row.resource_id)
    row.read_at = row.read_at or now_iso()
    return {'id': row.id, 'read_at': row.read_at}


@router.get('/preferences')
def preferences(actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    rows = RecordStore(session, actor).list('preferences', include_archived=True)['items']
    row = next((r for r in rows if r['owner_id'] == actor.user_id), None)
    return row or {'revision': 0, 'favorites': [], 'subscriptions': [], 'display': {}}


@router.patch('/preferences')
def patch_preferences(body: dict, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    rows = store.list('preferences', include_archived=True)['items']
    row = next((r for r in rows if r['owner_id'] == actor.user_id), None)
    clean = {k: body[k] for k in ('favorites', 'subscriptions', 'display') if k in body}
    if row:
        return store.update('preferences', row['id'], clean, body.get('revision', 0))
    if body.get('revision', 0) != 0:
        raise DomainError(409, 'revision_conflict', '偏好已更新，请刷新')
    return store.create('preferences', clean, visibility='private')


def scoped_count(session, name, actor, permission, *extra):
    if not actor.can(permission) or name not in Base.metadata.tables:
        return 0
    table = Base.metadata.tables[name]
    rules = [table.c.workspace_id == actor.workspace_id, *extra]
    if 'archived' in table.c:
        rules.append(table.c.archived.is_(False))
    return session.scalar(select(func.count()).select_from(table).where(*rules)) or 0


@router.get('/workbench')
def workbench(actor: Actor = Depends(require_permission('m01.read')), session: Session = Depends(get_session)):
    pending_tasks = [*task_scope(actor), Task.status.not_in(['succeeded', 'failed', 'cancelled'])]
    pending = list(TaskService(session, actor).visible(session.scalars(
        select(Task).where(*pending_tasks).order_by(Task.created_at.desc(), Task.id)
        .execution_options(yield_per=100))))
    counts = {
        'devices': scoped_count(session, 'manufacturing_devices', actor, 'm10.read'),
        'inspections': scoped_count(session, 'manufacturing_inspections', actor, 'm11.read'),
        'incidents': scoped_count(session, 'manufacturing_incidents', actor, 'm10.read'),
        'pending_reviews': 0,
        'tasks': len(pending),
        'artifacts': RecordStore(session, actor).list('artifact', limit=1)['total'] if actor.can('m17.read') else 0,
    }
    table = Base.metadata.tables.get('manufacturing_inspections')
    if table is not None and actor.can('m11.read'):
        counts['pending_reviews'] = session.scalar(select(func.count()).select_from(table).where(
            table.c.workspace_id == actor.workspace_id, table.c.status.in_(['pending_review', 'awaiting_review', 'waiting_review']))) or 0
    trend = first_inspection_trend(session, actor)
    activities = recent_activities(session, actor)
    return {'counts': counts, 'tasks': [task_dict(r) for r in pending[:8]],
            'recent_artifacts': RecordStore(session, actor).list('artifact', limit=6)['items'] if actor.can('m17.read') else [],
            'quality_trend': trend['items'], 'quality_trend_status': trend['status'], 'activities': activities,
            'applications': RecordStore(session, actor).list('application', limit=8)['items'] if actor.can('m13.read') else []}


@router.post('/resource-searches')
def resource_search(body: dict, actor: Actor = Depends(require_permission('m01.read')), session: Session = Depends(get_session)):
    q = str(body.get('q', '')).strip()
    if not q:
        raise DomainError(422, 'search_required', '请输入搜索关键词')
    result = []
    store = RecordStore(session, actor)
    for kind, module in [('application', 'm13'), ('artifact', 'm17'), ('ontology', 'm09'), ('graph', 'm09'), ('knowledge_space', 'm08')]:
        if actor.can(module + '.read'):
            result.extend({'type': kind, **r} for r in store.list(kind, q=q, limit=20)['items'])
    return {'items': result[:100], 'total': len(result)}
