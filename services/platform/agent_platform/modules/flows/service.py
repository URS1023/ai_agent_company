"""统一工作流目录与运行读取；旧顺序步骤和Graphon原生图分别保存，执行器不互相冒充。"""

from __future__ import annotations

import re

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError

from ..knowledge.service import KnowledgeService
from ..knowledge.evidence import authorize_evidence
from .models import Workflow, WorkflowRun
from .graph_runtime import definition_format, normalize_definition, read_events, run_graph_fields

STEP_TYPES = ('kb', 'ai', 'text')
MAX_STEPS = 12
VARIABLE = re.compile(r'\{\{\s*([a-zA-Z0-9_\-\.\u4e00-\u9fa5]+)\s*\}\}')
MAX_VALUE_CHARS = 40_000


def render(template: str, variables: dict[str, str]) -> str:
    """替换占位且先累计长度；防止重复引用上一步输出造成指数级文本膨胀。"""
    parts, cursor, size = [], 0, 0
    for match in VARIABLE.finditer(template):
        literal = template[cursor:match.start()]
        replacement = variables.get(match.group(1).strip(), match.group(0))
        size += len(literal) + len(replacement)
        if size > MAX_VALUE_CHARS:
            raise DomainError(422, 'workflow_value_too_large', '步骤文本超过本次运行的文本预算')
        parts.extend((literal, replacement))
        cursor = match.end()
    tail = template[cursor:]
    if size + len(tail) > MAX_VALUE_CHARS:
        raise DomainError(422, 'workflow_value_too_large', '步骤文本超过本次运行的文本预算')
    return ''.join(parts) + tail


def validate(definition: dict) -> list[dict]:
    steps = definition.get('steps') if isinstance(definition, dict) else None
    if not isinstance(steps, list) or not steps:
        raise DomainError(422, 'steps_required', '请至少添加一个步骤', {'steps': '必填'})
    if len(steps) > MAX_STEPS:
        raise DomainError(422, 'steps_too_many', f'步骤不能超过 {MAX_STEPS} 个')
    cleaned: list[dict] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise DomainError(422, 'step_invalid', f'第 {index} 个步骤格式不正确')
        kind = step.get('type')
        if kind not in STEP_TYPES:
            raise DomainError(422, 'step_type_invalid', f'第 {index} 个步骤类型不支持')
        name = str(step.get('name') or f'步骤{index}').strip()[:60] or f'步骤{index}'
        item: dict = {'type': kind, 'name': name}
        if kind == 'kb':
            space_ids = step.get('space_ids')
            if not isinstance(space_ids, list) or not space_ids:
                raise DomainError(422, 'step_space_required', f'第 {index} 步需要选择知识空间')
            item['space_ids'] = [str(x) for x in space_ids][:10]
            item['query'] = str(step.get('query') or '{{input}}')[:2000]
            item['top_k'] = max(1, min(int(step.get('top_k') or 3), 10))
        elif kind == 'ai':
            item['prompt'] = str(step.get('prompt') or '{{input}}')[:20000]
            item['max_tokens'] = max(64, min(int(step.get('max_tokens') or 1200), 4096))
        else:
            item['template'] = str(step.get('template') or '')[:20000]
        cleaned.append(item)
    return cleaned


class WorkflowService:
    def __init__(self, session: Session, actor: Actor, *, budget_seconds: float = 90, clock=None) -> None:
        self.session = session
        self.actor = actor
        self.budget_seconds, self.clock = budget_seconds, clock

    def _authorize_bindings(self, steps: list[dict]) -> list[str]:
        """保存与执行都按当前主体核知识依赖；文本和纯模型流程不额外要求知识权限。"""
        spaces = list(dict.fromkeys(space for step in steps if step['type'] == 'kb' for space in step['space_ids']))
        KnowledgeService(self.session, self.actor).authorize_spaces(spaces)
        return spaces

    # ---- 定义 ----
    def list(self) -> dict:
        rows = self.session.scalars(select(Workflow).where(Workflow.workspace_id == self.actor.workspace_id)
                                    .order_by(Workflow.updated_at.desc())).all()
        items = [{'id': r.id, 'name': r.name, 'description': r.description, 'enabled': r.enabled,
                  'revision': r.revision, 'format': definition_format(r.definition),
                  'step_count': len((r.definition or {}).get('graph', {}).get('nodes', []))
                    if definition_format(r.definition) == 'graph' else len((r.definition or {}).get('steps', [])),
                  'updated_at': utc_storage_iso(r.updated_at)} for r in rows]
        return {'items': items, 'total': len(items)}

    def get(self, workflow_id: str) -> Workflow:
        row = self.session.get(Workflow, workflow_id)
        if not row or row.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'workflow_not_found', '工作流不存在或已删除')
        return row

    def detail(self, workflow_id: str) -> dict:
        row = self.get(workflow_id)
        return {'id': row.id, 'name': row.name, 'description': row.description,
                'enabled': row.enabled, 'revision': row.revision,
                'definition': row.definition, 'updated_at': utc_storage_iso(row.updated_at)}

    def create(self, name: str, description: str, definition: dict) -> dict:
        name = name.strip()
        if not name:
            raise DomainError(422, 'name_required', '请填写工作流名称', {'name': '必填'})
        cleaned = normalize_definition(definition)
        self._authorize_bindings(cleaned.get('steps', []))
        row = Workflow(workspace_id=self.actor.workspace_id, name=name, description=description.strip(),
                       definition=cleaned, created_by=self.actor.user_id)
        self.session.add(row)
        self.session.flush()
        return self.detail(row.id)

    def update(self, workflow_id: str, name: str, description: str, definition: dict,
               *, expected_revision: int | None = None) -> dict:
        row = self.get(workflow_id)
        name = name.strip()
        if not name:
            raise DomainError(422, 'name_required', '请填写工作流名称', {'name': '必填'})
        cleaned = normalize_definition(definition)
        self._authorize_bindings(cleaned.get('steps', []))
        if definition_format(row.definition) == 'graph' or definition_format(cleaned) == 'graph':
            if type(expected_revision) is not int or expected_revision < 1:
                raise DomainError(422, 'workflow_revision_required', '请先读取最新工作流再保存', {'revision': '缺少版本号'})
        if expected_revision is not None:
            changed = self.session.execute(update(Workflow).where(Workflow.id == workflow_id,
                Workflow.workspace_id == self.actor.workspace_id, Workflow.revision == expected_revision).values(
                name=name, description=description.strip(), definition=cleaned, revision=expected_revision + 1))
            if not changed.rowcount:
                raise DomainError(409, 'revision_conflict', '工作流已被其他操作修改，请读取最新版本',
                    {'revision': '当前版本已变化'})
            self.session.refresh(row)
            return self.detail(workflow_id)
        row.name = name
        row.description = description.strip()
        row.definition = cleaned
        row.revision += 1
        self.session.flush()
        return self.detail(workflow_id)

    def delete(self, workflow_id: str) -> dict:
        row = self.get(workflow_id)
        self.session.delete(row)
        return {'deleted': True, 'id': workflow_id}

    # ---- 运行 ----
    def runs(self, workflow_id: str, *, idempotency_key: str | None = None) -> dict:
        """按当前来源权限过滤；按请求编号恢复时还限定发起人，不泄露他人的编号。"""
        from .execution import reconcile_expired, request_key
        self.get(workflow_id)
        statement = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.workspace_id == self.actor.workspace_id)
        if idempotency_key is not None:
            statement = statement.where(WorkflowRun.created_by == self.actor.user_id,
                WorkflowRun.idempotency_key == request_key(idempotency_key))
        rows = self.session.scalars(statement.order_by(WorkflowRun.started_at.desc())).all()
        visible = []
        for row in rows:
            # 状态回收只核绑定，不读取正文；撤权不能使孤立任务永久running。
            reconcile_expired(self.session, self.actor, row)
            try:
                authorize_evidence(self.session, self.actor, row.knowledge_sources, legacy_steps=row.steps)
            except DomainError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            visible.append(row)
        items = [{'id': r.id, 'status': r.status, 'input': r.input, 'output': r.output, 'error': r.error,
                  'task_id': r.task_id, 'idempotency_key': r.idempotency_key, 'current_step': r.current_step,
                  'workflow_revision': r.workflow_revision, **run_graph_fields(r),
                  'started_at': utc_storage_iso(r.started_at),
                  'finished_at': utc_storage_iso(r.finished_at) if r.finished_at else None} for r in visible[:50]]
        return {'items': items, 'total': len(visible)}

    def run_detail(self, run_id: str) -> dict:
        row = self.session.get(WorkflowRun, run_id)
        if not row or row.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'run_not_found', '运行记录不存在')
        from .execution import reconcile_expired
        reconcile_expired(self.session, self.actor, row)
        authorize_evidence(self.session, self.actor, row.knowledge_sources, legacy_steps=row.steps)
        return {'id': row.id, 'workflow_id': row.workflow_id, 'status': row.status, 'input': row.input,
                **run_graph_fields(row),
                'steps': row.steps, 'output': row.output, 'error': row.error,
                'knowledge_sources': row.knowledge_sources,
                'task_id': row.task_id, 'idempotency_key': row.idempotency_key, 'request_hash': row.request_hash,
                'workflow_revision': row.workflow_revision, 'definition_snapshot': row.definition_snapshot,
                'current_step': row.current_step,
                'deadline_at': utc_storage_iso(row.deadline_at) if row.deadline_at else None,
                'started_at': utc_storage_iso(row.started_at),
                'finished_at': utc_storage_iso(row.finished_at) if row.finished_at else None}

    def run_events(self, run_id: str, *, after: int = 0, limit: int = 100) -> dict:
        """详情权限和冻结来源复核通过后，分页读取该Run绑定的原生事件。"""
        self.actor.require('m04.read')
        self.run_detail(run_id)
        row = self.session.get(WorkflowRun, run_id)
        return read_events(self.session, self.actor, row, after=after, limit=min(max(limit, 1), 200))

    def run(self, workflow_id: str, user_input: str = '', *, idempotency_key: str | None = None,
            inputs: dict | None = None) -> dict:
        """提交预约后执行；同键只读取原运行，不恢复已中断或未知的外部请求。"""
        from .execution import WorkflowExecution
        return WorkflowExecution(self).run(workflow_id, user_input, idempotency_key, structured_inputs=inputs)
