"""流程运行预约、逐步提交及未知结果隔离；不把数据库事务跨越模型或向量请求。"""

from __future__ import annotations

from datetime import timedelta, timezone
import hashlib
import json
import math
import time
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent_platform.core.embeddings import EmbeddingClient
from agent_platform.core.errors import DomainError
from agent_platform.core.tasks import TaskService
from agent_platform.modules.chat.service import ChatService
from agent_platform.modules.knowledge.evidence import authorize_evidence, knowledge_evidence, merge_evidence
from agent_platform.modules.knowledge.service import KnowledgeService

from .models import WorkflowRun, utcnow

UNKNOWN_MESSAGE = '执行结果尚未确认，请查看运行记录后人工核对；系统不会自动重试'
UNCERTAIN_ERRORS = {'chat_timeout', 'chat_unavailable', 'chat_upstream_failed', 'chat_invalid_response',
    'embedding_timeout', 'embedding_unavailable', 'embedding_upstream_failed', 'embedding_invalid_response',
    'workflow_deadline_exceeded', 'workflow_graph_unknown', 'workflow_event_sequence_invalid'}


def request_key(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 160
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise DomainError(422, 'invalid_idempotency_key', '请求编号格式不正确', {'idempotency_key': '请使用1至160位可见ASCII字符'})
    return value


class RunBudget:
    """一次运行共用单调时钟预算；每步只能使用剩余时间，不能重新获得90秒。"""

    def __init__(self, seconds: float = 90, clock=None):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('workflow budget must be positive and finite')
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + seconds

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise DomainError(504, 'workflow_deadline_exceeded', '本次运行已达到总时限，后续步骤未执行')
        return remaining


class DeadlineEmbeddings(EmbeddingClient):
    """沿知识库原检索逻辑注入剩余预算；向量请求前释放其读取事务。"""

    def __init__(self, session, budget):
        super().__init__()
        self.session, self.budget = session, budget

    def embed(self, texts):
        self.session.commit()
        return super().embed(texts, timeout_seconds=self.budget.remaining())


def sync_task(session, actor, record, status):
    """任务只投影引用、状态及进度；知识正文只保留在带来源授权的运行记录中。"""
    if record.task_id:
        if status != record.status:
            raise DomainError(409, 'workflow_task_state_invalid', '运行与任务状态不一致')
        TaskService(session, actor).project_workflow(record.id)


def reconcile_expired(session, actor, record):
    """已过固定时限却仍running的预约只改unknown；绝不接管进程或恢复派发。"""
    if record.status != 'running' or record.deadline_at is None:
        return
    deadline = record.deadline_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline > utcnow():
        return
    changed = session.execute(update(WorkflowRun).where(WorkflowRun.id == record.id,
        WorkflowRun.status == 'running', WorkflowRun.deadline_at <= utcnow()).values(
        status='unknown', error=UNKNOWN_MESSAGE, finished_at=utcnow()))
    if changed.rowcount:
        session.refresh(record)
        sync_task(session, actor, record, 'unknown')
        session.commit()


class WorkflowExecution:
    def __init__(self, service):
        self.service = service
        self.session, self.actor = service.session, service.actor
        self.budget = RunBudget(service.budget_seconds, service.clock)

    def _existing(self, workflow_id, key):
        if key is None:
            return None
        return self.session.scalar(select(WorkflowRun).where(WorkflowRun.workspace_id == self.actor.workspace_id,
            WorkflowRun.created_by == self.actor.user_id, WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.idempotency_key == key))

    def _admit_existing(self, record, fingerprint):
        authorize_evidence(self.session, self.actor, record.knowledge_sources, legacy_steps=record.steps)
        if record.request_hash != fingerprint:
            raise DomainError(409, 'idempotency_conflict', '该请求编号已用于其他输入或流程版本',
                {'idempotency_key': '请查看原运行，明确发起新运行时使用新的请求编号', 'run_id': record.id})
        reconcile_expired(self.session, self.actor, record)
        return record

    def reserve(self, workflow_id, user_input, key, *, structured_inputs=None):
        from .graph_runtime import normalize_definition, encode_inputs
        self.actor.require('m04.read')
        self.actor.require('m04.write')
        workflow = self.service.get(workflow_id)
        snapshot = normalize_definition(workflow.definition)
        steps = snapshot.get('steps', [])
        user_input = encode_inputs(snapshot, user_input, structured_inputs)
        fingerprint = hashlib.sha256(json.dumps({'workflow_id': workflow_id,
            'workflow_revision': workflow.revision, 'definition': snapshot, 'input': user_input},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        prior = self._existing(workflow_id, key)
        if prior is not None:
            return self._admit_existing(prior, fingerprint), False
        if not workflow.enabled:
            raise DomainError(409, 'workflow_disabled', '该工作流已停用')
        spaces = self.service._authorize_bindings(steps)
        record = WorkflowRun(id=str(uuid4()), workspace_id=self.actor.workspace_id, workflow_id=workflow_id,
            created_by=self.actor.user_id, input=user_input, steps=[], status='running',
            idempotency_key=key, request_hash=fingerprint, workflow_revision=workflow.revision,
            definition_snapshot=snapshot, current_step=0,
            deadline_at=utcnow() + timedelta(seconds=self.budget.remaining()),
            knowledge_sources=knowledge_evidence(self.actor, spaces))
        try:
            self.session.add(record)
            self.session.flush()
            task = TaskService(self.session, self.actor).create('platform_workflow', workflow.name,
                inputs={'run_id': record.id, 'workflow_id': workflow_id, 'workflow_revision': workflow.revision},
                idempotency_key='flow-run:' + record.id, access_permission='m04.read')
            record.task_id = task['id']
            TaskService(self.session, self.actor).transition(record.task_id, 'running')
            # 必须先提交预约；这只是跨网络边界，不是声明整条HTTP请求拥有一个事务。
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            prior = self._existing(workflow_id, key)
            if prior is None:
                raise
            return self._admit_existing(prior, fingerprint), False
        return record, True

    def step(self, record, step, variables):
        from .service import render
        kind = step['type']
        if kind == 'kb':
            found = KnowledgeService(self.session, self.actor,
                embeddings=DeadlineEmbeddings(self.session, self.budget)).search(
                step['space_ids'], render(step['query'], variables), step['top_k'])
            record.knowledge_sources = merge_evidence(record.knowledge_sources, found['knowledge_sources'])
            return '\n\n'.join(f"[{item['document_name']} 第{item['ordinal'] + 1}段] {item['content']}"
                for item in found['items']) or '（未检索到相关资料）'
        if kind == 'ai':
            prompt = render(step['prompt'], variables)
            self.session.commit()
            result = ChatService().complete([{'role': 'user', 'content': prompt}],
                max_tokens=step['max_tokens'], timeout_seconds=self.budget.remaining(),
                session=f'workflow-{record.id}-{record.current_step}')
            output = (result['choices'][0].get('message') or {}).get('content')
            if not isinstance(output, str):
                raise DomainError(502, 'chat_invalid_response', '模型未返回有效文本，结果需要核对')
            return output
        return render(step['template'], variables)

    def run(self, workflow_id, user_input, key, *, structured_inputs=None):
        from .graph_runtime import definition_format, GraphExecution, graph_summary
        record, fresh = self.reserve(workflow_id, user_input, request_key(key), structured_inputs=structured_inputs)
        if not fresh:
            return self.service.run_detail(record.id)
        run_id, task_id = record.id, record.task_id
        variables, trail, dispatched = {'input': user_input}, [], False
        is_graph = definition_format(record.definition_snapshot) == 'graph'
        try:
            if is_graph:
                dispatched = True
                GraphExecution(self.service, record, self.budget).run()
                dispatched = False
            for index, step in enumerate(record.definition_snapshot.get('steps', []), start=1):
                self.budget.remaining()
                record.current_step = index
                record.steps = trail + [{'index': index, 'name': step['name'], 'type': step['type'], 'status': 'dispatching'}]
                self.session.commit()
                dispatched = step['type'] in {'ai', 'kb'}
                output = self.step(record, step, variables)
                self.budget.remaining()
                if len(output) > 40_000:
                    raise DomainError(422, 'workflow_value_too_large', '步骤结果超过本次运行的文本预算')
                variables[f"steps.{step['name']}.output"] = output
                variables[f"{step['name']}.output"] = output
                trail.append({'index': index, 'name': step['name'], 'type': step['type'], 'output': output, 'status': 'ok'})
                record.steps = list(trail)
                # 来源在模型等待期间可能删除；保存已完成结果，但禁止继续派发下一步。
                authorize_evidence(self.session, self.actor, record.knowledge_sources, legacy_steps=record.steps)
                TaskService(self.session, self.actor).project_workflow(record.id, completed_step=index)
                self.session.commit()
                dispatched = False
            record.status = 'succeeded'
            if not is_graph:
                record.output = trail[-1]['output'] if trail else ''
            record.finished_at = utcnow()
            sync_task(self.session, self.actor, record, 'succeeded')
            self.session.commit()
        except SQLAlchemyError as exc:
            # 存储连接异常后不继续发SQL补偿；已提交预约仍保留，同键绝不再次执行。
            self.session.rollback()
            raise DomainError(503, 'workflow_state_unavailable', '执行状态暂不可读取，请按原请求编号核对运行记录',
                {'run_id': run_id, 'task_id': task_id, 'idempotency_key': key,
                    **({'process_cleanup': exc.workflow_cleanup} if hasattr(exc, 'workflow_cleanup') else {})}) from None
        except BaseException as exc:
            uncertain = dispatched and (not isinstance(exc, DomainError) or exc.code in UNCERTAIN_ERRORS)
            record.status = 'unknown' if uncertain else 'failed'
            record.error = UNKNOWN_MESSAGE if uncertain else (exc.message if isinstance(exc, DomainError) else '流程执行未完成')
            if is_graph:
                summary = dict(graph_summary(record) or {'index': 1, 'name': '图运行', 'type': 'graph_result',
                    'outputs': {}, 'event_count': 0, 'completed_nodes': 0, 'last_event_sequence': 0})
                summary.update(status=record.status, error=record.error,
                    error_code=exc.code if isinstance(exc, DomainError) else type(exc).__name__)
                record.steps = [summary]
            else:
                record.steps = trail + [{'index': record.current_step or len(trail) + 1,
                    'name': '执行中断', 'type': 'error', 'output': '', 'status': record.status,
                    'error': record.error, 'error_code': exc.code if isinstance(exc, DomainError) else type(exc).__name__}]
            record.finished_at = utcnow()
            sync_task(self.session, self.actor, record, record.status)
            self.session.commit()
            if not isinstance(exc, Exception):
                raise
        return self.service.run_detail(record.id)
