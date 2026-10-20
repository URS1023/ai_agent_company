"""统一任务投影、持久事件与状态校验；原生执行仍由原引擎负责。"""

from hashlib import sha256
import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .context import Actor
from .errors import DomainError
from .models import Notification, Task, TaskEvent, now_iso
from .records import RecordStore, sanitized
from .task_evidence import authorize_task_evidence

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
                    'inspection': 'm11.read', 'quality_case': 'm11.read',
                    'maintenance_analysis': 'm10.read', 'maintenance_work_order': 'm10.read',
                    'energy_analysis': 'm10.read'}


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
        self._authorize(row)
        return row

    def _authorize(self, row: Task) -> None:
        if row.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        permission = required_task_permission(row)
        if permission and not self.actor.can(permission) and not self.actor.is_admin and not self.actor.can('m16.admin'):
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        delegated = bool(row.access_permission and self.actor.can(row.access_permission))
        if row.user_id != self.actor.user_id and not self.actor.is_admin and not self.actor.can('m16.admin') and not delegated:
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        authorize_task_evidence(self.session, self.actor, row)

    def get(self, id: str) -> dict:
        return task_dict(self._get(id))

    def visible(self, rows):
        """列表与详情使用同一授权；先过滤再分页，避免泄露无权任务总数。"""
        for row in rows:
            try:
                self._authorize(row)
            except DomainError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            yield row

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
                return self.get(row.id)
        row = Task(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id, kind=kind,
                   title=title, engine=engine, inputs=sanitized(payload, preserve_business_value=True), idempotency_key=idempotency_key,
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
            row.outputs = sanitized(outputs, preserve_business_value=True)
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
        row = TaskEvent(task_id=id, type=type, data=sanitized(data, preserve_business_value=True))
        self.session.add(row)
        self.session.flush()
        return {'sequence': row.sequence, 'task_id': id, 'type': type, 'data': row.data, 'created_at': row.created_at}

    def project_workflow(self, run_id: str, *, completed_step: int | None = None) -> dict:
        """仅供流程执行器内部同步状态，不暴露路由，也不读取已撤权的知识正文。

        状态和输出只能来自绑定的运行记录。公共get/list仍执行完整来源鉴权；
        这里额外核对工作区、发起人、任务种类和双向ID，避免将普通任务变成写入目标。
        """
        from ..modules.flows.models import WorkflowRun
        self.actor.require('m04.read')
        run = self.session.get(WorkflowRun, run_id)
        task = self.session.get(Task, run.task_id) if run is not None and run.task_id else None
        if (run is None or task is None or run.workspace_id != self.actor.workspace_id
                or task.workspace_id != run.workspace_id or task.kind != 'platform_workflow'
                or task.user_id != run.created_by or task.inputs.get('run_id') != run.id
                or task.inputs.get('workflow_id') != run.workflow_id):
            raise DomainError(409, 'workflow_task_binding_invalid', '运行与任务的关联不一致')
        status = run.status
        if (status not in {'running', 'succeeded', 'failed', 'unknown'}
                or (status != task.status and status not in TRANSITIONS.get(task.status, set()))):
            raise DomainError(409, 'task_state_conflict', '任务当前状态不支持此操作')
        if completed_step is not None and (status != 'running' or not any(
                item.get('index') == completed_step and item.get('status') == 'ok' for item in run.steps)):
            raise DomainError(409, 'workflow_step_not_completed', '该步骤尚未完成')
        if task.status == status and completed_step is None:
            return task_dict(task)
        previous = task.status
        error = None
        if status in {'failed', 'unknown'}:
            error = {'code': 'workflow_result_unknown' if status == 'unknown' else 'workflow_failed',
                'message': '执行结果尚未确认，请核对运行记录' if status == 'unknown' else '流程执行失败，请查看运行记录'}
        values = {'status': status, 'updated_at': now_iso(), 'revision': task.revision + 1,
            'source_run_id': run.id, 'outputs': {'run_id': run.id,
                'completed_steps': sum(item.get('status') == 'ok' for item in run.steps)}, 'error': error}
        changed = self.session.execute(update(Task).where(Task.id == task.id,
            Task.workspace_id == run.workspace_id, Task.revision == task.revision).values(**values))
        if not changed.rowcount:
            raise DomainError(409, 'task_state_conflict', '任务状态已变化，请读取最新运行记录')
        self.session.refresh(task)
        data = {'run_id': run.id, 'step': completed_step} if completed_step is not None else {'from': previous, 'status': status}
        self.session.add(TaskEvent(task_id=task.id,
            type='workflow_step_completed' if completed_step is not None else 'state_changed', data=data))
        if status != previous and status in {'succeeded', 'failed', 'unknown'}:
            label = {'succeeded': '已完成', 'failed': '执行失败', 'unknown': '需要核对执行结果'}[status]
            self.session.add(Notification(workspace_id=task.workspace_id, user_id=task.user_id,
                title=task.title + ' · ' + label, resource_type='task', resource_id=task.id))
        self.session.flush()
        return task_dict(task)
