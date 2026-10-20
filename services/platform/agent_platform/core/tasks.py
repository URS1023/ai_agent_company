"""统一任务投影、持久事件与状态校验；原生执行仍由原引擎负责。"""

from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .context import Actor
from .errors import DomainError
from .models import Notification, Task, TaskEvent, now_iso
from .records import RecordStore, sanitized

TRANSITIONS = {
    'queued': {'running', 'cancelled', 'failed'},
    'running': {'waiting_review', 'succeeded', 'failed', 'cancel_requested', 'unknown'},
    'waiting_review': {'running', 'succeeded', 'failed', 'cancelled'},
    'cancel_requested': {'cancelled', 'succeeded', 'failed', 'unknown'},
    'unknown': {'running', 'succeeded', 'failed', 'cancelled'},
    'succeeded': set(), 'failed': set(), 'cancelled': set(),
}


TASK_PERMISSIONS = {'document': 'm17.read', 'graph-extraction': 'm09.read',
                    'native_workflow': 'm04.read', 'native_agent': 'm03.read',
                    'inspection': 'm11.read', 'maintenance_analysis': 'm10.read'}


def required_task_permission(row: Task) -> str | None:
    """归属用户也必须保留当前业务权限，不能借任务历史绕过撤权。"""
    return row.access_permission or TASK_PERMISSIONS.get(row.kind)


def task_dict(row: Task) -> dict:
    result = {key: getattr(row, key) for key in ('id', 'workspace_id', 'user_id', 'kind', 'title',
              'engine', 'status', 'inputs', 'outputs', 'error', 'source_run_id', 'revision', 'created_at', 'updated_at')}
    result['allowed_actions'] = ['cancel'] if row.status == 'queued' or (row.status == 'running' and row.engine == 'dify') else []
    return result


class TaskService:
    def __init__(self, session: Session, actor: Actor):
        self.session, self.actor = session, actor

    def _get(self, id: str) -> Task:
        row = self.session.scalar(select(Task).where(Task.id == id, Task.workspace_id == self.actor.workspace_id))
        if row is None:
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        permission = required_task_permission(row)
        if permission and not self.actor.can(permission) and not self.actor.is_admin and not self.actor.can('m16.admin'):
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        delegated = bool(row.access_permission and self.actor.can(row.access_permission))
        if row.user_id != self.actor.user_id and not self.actor.is_admin and not self.actor.can('m16.admin') and not delegated:
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        return row

    def get(self, id: str) -> dict:
        return task_dict(self._get(id))

    def create(self, kind: str, title: str, inputs: dict | None = None,
               idempotency_key: str | None = None, engine: str = 'platform', access_permission: str | None = None) -> dict:
        payload = inputs or {}
        fingerprint = sha256(json.dumps({'kind': kind, 'inputs': payload}, sort_keys=True,
                                         ensure_ascii=False, default=str).encode()).hexdigest()
        if idempotency_key:
            row = self.session.scalar(select(Task).where(
                Task.workspace_id == self.actor.workspace_id, Task.user_id == self.actor.user_id,
                Task.idempotency_key == idempotency_key))
            if row:
                if row.input_hash != fingerprint:
                    raise DomainError(409, 'idempotency_conflict', '同一请求编号不能用于不同输入')
                return task_dict(row)
        row = Task(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id, kind=kind,
                   title=title, engine=engine, inputs=sanitized(payload), idempotency_key=idempotency_key,
                   input_hash=fingerprint, access_permission=access_permission)
        self.session.add(row)
        self.session.flush()
        self.event(row.id, 'created', {'status': 'queued'})
        RecordStore(self.session, self.actor).audit('task.create', 'task', row.id, {'kind': kind, 'engine': engine})
        return task_dict(row)

    def transition(self, id: str, status: str, *, outputs: dict | None = None,
                   error: dict | str | None = None, source_run_id: str | None = None) -> dict:
        row = self._get(id)
        if status != row.status and status not in TRANSITIONS.get(row.status, set()):
            raise DomainError(409, 'task_state_conflict', '任务当前状态不支持此操作')
        if status == row.status:
            return task_dict(row)
        previous = row.status
        row.status, row.updated_at = status, now_iso()
        row.revision += 1
        if outputs is not None:
            row.outputs = sanitized(outputs)
        if error is not None:
            row.error = sanitized(error if isinstance(error, dict) else {'message': error})
        if source_run_id is not None:
            row.source_run_id = source_run_id
        self.event(id, 'state_changed', {'from': previous, 'status': status})
        if status in {'waiting_review', 'succeeded', 'failed', 'unknown'}:
            labels = {'waiting_review': '待复核', 'succeeded': '已完成', 'failed': '执行失败', 'unknown': '需要核对执行结果'}
            self.session.add(Notification(workspace_id=row.workspace_id, user_id=row.user_id,
                title=row.title + ' · ' + labels[status], resource_type='task', resource_id=id))
        self.session.flush()
        return task_dict(row)

    def event(self, id: str, type: str, data: dict) -> dict:
        self._get(id)
        row = TaskEvent(task_id=id, type=type, data=sanitized(data))
        self.session.add(row)
        self.session.flush()
        return {'sequence': row.sequence, 'task_id': id, 'type': type, 'data': row.data, 'created_at': row.created_at}
