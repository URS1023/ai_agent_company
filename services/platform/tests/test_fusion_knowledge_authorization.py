"""使用全新临时SQLite及模型替身验证融合模块的知识权限，不访问真实模型或业务源。"""

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.embeddings import EmbeddingClient
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, Notification, Task, TaskEvent
from agent_platform.modules.agents.models import AgentApp
from agent_platform.modules.agents.service import AgentService, KnowledgeAnswerStream
from agent_platform.modules.chat.service import ChatService
from agent_platform.modules.flows.models import Workflow, WorkflowRun
from agent_platform.modules.flows.service import WorkflowService
from agent_platform.modules.knowledge.evidence import authorize_evidence, content_sha256, knowledge_evidence
from agent_platform.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from agent_platform.modules.knowledge.service import KnowledgeService


TABLES = [KnowledgeSpace.__table__, KnowledgeDocument.__table__, KnowledgeChunk.__table__,
    Workflow.__table__, WorkflowRun.__table__, AgentApp.__table__]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'new-fusion.sqlite3').as_posix())
    Base.metadata.create_all(engine, tables=TABLES + [item.__table__ for item in (Task, TaskEvent, AuditEvent, Notification)])
    calls = {'embedding': [], 'model': [], 'streams': []}
    def embed(_self, texts, **kwargs):
        calls['embedding'].append(list(texts))
        return [[1.0, 0.0] for _ in texts]
    def complete(_self, messages, **kwargs):
        calls['model'].append(messages)
        return {'choices': [{'message': {'content': '温度上限75℃'}}]}
    monkeypatch.setattr(EmbeddingClient, 'embed', embed)
    monkeypatch.setattr(ChatService, 'complete', complete)
    actor = Actor('first-user', 'workspace-a', frozenset({'m03.*', 'm04.*', 'm08.*'}))
    with Session(engine) as session:
        yield session, actor, calls
    engine.dispose()


def data(environment):
    session, actor, calls = environment
    service = KnowledgeService(session, actor)
    space = service.create_space('设备标准')
    document = service.import_text(space['id'], '循环泵点检', '轴承温度上限75℃。')
    agent = AgentService(session, actor).create('点检助手', '', '', [space['id']], 3, 128, 0.3)
    flow = WorkflowService(session, actor).create('标准检索', '', {'steps': [
        {'type': 'kb', 'name': '检索', 'space_ids': [space['id']]},
        {'type': 'text', 'name': '输出', 'template': '{{检索.output}}'}]})
    session.commit()
    calls['embedding'].clear()
    return space, document, agent, flow


def restricted(actor, *permissions):
    return Actor(actor.user_id, actor.workspace_id, frozenset(permissions))


def test_service_search_checks_permission_before_embedding(environment):
    session, actor, calls = environment
    space, *_ = data(environment)
    with pytest.raises(DomainError) as denied:
        KnowledgeService(session, restricted(actor, 'm03.write')).search([space['id']], '上限')
    assert denied.value.status == 403 and calls['embedding'] == []


@pytest.mark.parametrize('operation', ['answer', 'stream', 'flow'])
def test_indirect_read_cannot_use_agent_or_flow_permission_instead_of_knowledge(environment, operation):
    session, actor, calls = environment
    _, _, agent, flow = data(environment)
    user = restricted(actor, 'm03.read', 'm03.write', 'm04.read', 'm04.write')
    with pytest.raises(DomainError) as denied:
        if operation == 'flow':
            WorkflowService(session, user).run(flow['id'], '温度多少')
        else:
            getattr(AgentService(session, user), operation)(agent['id'], '温度多少')
    assert denied.value.status == 403
    assert calls['model'] == calls['embedding'] == []
    assert list(session.scalars(select(WorkflowRun))) == []


@pytest.mark.parametrize('kind', ['agent', 'flow'])
def test_configuration_cannot_bind_foreign_or_unreadable_space(environment, kind):
    session, actor, _ = environment
    space, *_ = data(environment)
    user = restricted(actor, 'm03.write', 'm04.write')
    def create(subject, space_id):
        if kind == 'agent':
            return AgentService(session, subject).create('新应用', '', '', [space_id], 3, 128, 0.3)
        return WorkflowService(session, subject).create('新流程', '', {'steps': [
            {'type': 'kb', 'name': '检索', 'space_ids': [space_id]}]})
    with pytest.raises(DomainError) as denied:
        create(user, space['id'])
    assert denied.value.status == 403
    with pytest.raises(DomainError) as foreign:
        create(Actor('other', 'workspace-b', frozenset({'*'})), space['id'])
    assert foreign.value.status == 404


def test_answer_returns_exact_citation_and_immutable_source_hash(environment):
    session, actor, calls = environment
    space, document, agent, _ = data(environment)
    result = AgentService(session, actor).answer(agent['id'], '温度多少')
    assert result['answer'] == '温度上限75℃'
    assert result['citations'][0]['document_id'] == document['id']
    assert result['knowledge_sources']['space_ids'] == [space['id']]
    assert result['knowledge_sources']['chunks'][0]['content_sha256'] == content_sha256('轴承温度上限75℃。')
    assert len(calls['model']) == len(calls['embedding']) == 1


def test_agent_stream_includes_source_event_and_closes_upstream(environment, monkeypatch):
    session, actor, calls = environment
    _, _, agent, _ = data(environment)
    class Stream:
        closed = False
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"75"}}]}\n\n'
        def close(self):
            self.closed = True
    opened = Stream()
    monkeypatch.setattr(ChatService, 'open_stream', lambda *a, **kw: opened)
    stream = AgentService(session, actor).stream(agent['id'], '温度多少')
    first = next(stream).decode()
    assert first.startswith('event: knowledge_sources\ndata: ')
    evidence = json.loads(first.split('data: ', 1)[1])
    assert evidence['knowledge_sources']['chunks'] and evidence['citations']
    stream.close()
    assert opened.closed


def test_history_uses_saved_sources_after_workflow_binding_changes(environment):
    session, actor, calls = environment
    _, _, _, flow = data(environment)
    service = WorkflowService(session, actor)
    result = service.run(flow['id'], '温度多少')
    frozen = result['knowledge_sources']
    assert frozen['chunks'] and '75' in result['output']
    service.update(flow['id'], '已改为文本', '', {'steps': [{'type': 'text', 'name': '新输出', 'template': '公开内容'}]})
    calls['embedding'].clear()
    user = restricted(actor, 'm04.read')
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, user).run_detail(result['id'])
    assert denied.value.status == 403
    assert WorkflowService(session, user).runs(flow['id']) == {'items': [], 'total': 0}
    assert service.run_detail(result['id'])['knowledge_sources'] == frozen
    assert calls['embedding'] == []


def test_revoked_knowledge_does_not_hide_normal_text_history(environment):
    session, actor, _ = environment
    _, _, _, flow = data(environment)
    service = WorkflowService(session, actor)
    hidden = service.run(flow['id'], '温度多少')
    service.update(flow['id'], '文本', '', {'steps': [{'type': 'text', 'name': '输出', 'template': '{{input}}'}]})
    normal = service.run(flow['id'], '公开文本')
    user_service = WorkflowService(session, restricted(actor, 'm04.read'))
    listing = user_service.runs(flow['id'])
    assert listing['total'] == 1 and [item['id'] for item in listing['items']] == [normal['id']]
    assert user_service.run_detail(normal['id'])['output'] == '公开文本'
    assert hidden['id'] not in str(listing)


@pytest.mark.parametrize('change', ['document_deleted', 'chunk_changed', 'space_foreign'])
def test_history_rejects_lost_or_changed_knowledge_source(environment, change):
    session, actor, calls = environment
    space, document, _, flow = data(environment)
    service = WorkflowService(session, actor)
    result = service.run(flow['id'], '温度多少')
    if change == 'document_deleted':
        KnowledgeService(session, actor).delete_document(document['id'])
    elif change == 'chunk_changed':
        session.get(KnowledgeChunk, result['knowledge_sources']['chunks'][0]['chunk_id']).content = '被修改的内容'
    else:
        session.get(KnowledgeSpace, space['id']).workspace_id = 'other-workspace'
    session.flush()
    calls['embedding'].clear()
    with pytest.raises(DomainError) as denied:
        service.run_detail(result['id'])
    assert denied.value.status == 403 and calls['embedding'] == []


def test_old_knowledge_history_is_not_reconstructed_from_current_definition(environment):
    session, actor, _ = environment
    *_, flow = data(environment)
    row = WorkflowRun(workspace_id=actor.workspace_id, workflow_id=flow['id'], status='succeeded', input='',
        steps=[{'type': 'kb', 'output': '旧敏感结果'}], output='旧敏感结果', created_by=actor.user_id)
    session.add(row)
    session.flush()
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).run_detail(row.id)
    assert denied.value.status == 403


def test_empty_retrieval_keeps_space_dependency_and_current_permission(environment):
    session, actor, calls = environment
    space = KnowledgeService(session, actor).create_space('空知识空间')
    service = WorkflowService(session, actor)
    flow = service.create('空检索', '', {'steps': [{'type': 'kb', 'name': '检索', 'space_ids': [space['id']]}]})
    result = service.run(flow['id'], '尚无资料')
    assert result['knowledge_sources']['space_ids'] == [space['id']]
    assert result['knowledge_sources']['chunks'] == []
    assert calls['embedding'] == []
    with pytest.raises(DomainError):
        WorkflowService(session, restricted(actor, 'm04.read')).run_detail(result['id'])


def test_cross_workspace_run_read_never_returns_frozen_content(environment):
    session, actor, _ = environment
    *_, flow = data(environment)
    result = WorkflowService(session, actor).run(flow['id'], '温度多少')
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, Actor('other', 'workspace-b', frozenset({'*'}))).run_detail(result['id'])
    assert denied.value.status == 404


def test_malformed_evidence_is_denied_and_plain_legacy_run_remains_accessible(environment):
    session, actor, _ = environment
    for snapshot in [{}, {'schema_version': 1, 'workspace_id': 'other', 'space_ids': [], 'chunks': []},
                     {'schema_version': 1, 'workspace_id': actor.workspace_id, 'space_ids': [], 'chunks': [{'chunk_id': 'fake'}]}]:
        with pytest.raises(DomainError) as denied:
            authorize_evidence(session, actor, snapshot)
        assert denied.value.status == 403
    authorize_evidence(session, restricted(actor, 'm04.read'), None, legacy_steps=[{'type': 'text', 'output': '普通文本'}])
    authorize_evidence(session, restricted(actor, 'm04.read'), knowledge_evidence(actor, []))


def test_unknown_legacy_shape_is_not_treated_as_proven_plain_text(environment):
    session, actor, _ = environment
    for steps in [None, [], {}, [{'type': 'error'}], [{'output': '未知来源'}]]:
        with pytest.raises(DomainError):
            authorize_evidence(session, actor, None, legacy_steps=steps)


def test_stream_cancelled_before_first_event_closes_already_open_connection():
    opened = type('Opened', (), {'closed': False, 'close': lambda self: setattr(self, 'closed', True)})()
    stream = KnowledgeAnswerStream(opened, {'citations': []})
    stream.close()
    assert opened.closed
