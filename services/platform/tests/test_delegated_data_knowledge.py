"""新临时SQLite/CSV、内存ASGI与向量替身；不访问企业源或真实模型。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import FileEntry, ResourceGrant, ResourceReference, Role
from agent_platform.modules.chat.native_models import NativeChatTurn, NativeConversation
from agent_platform.modules.chat.native_sources import authorize_conversation_sources
from agent_platform.modules.data import models as data_models
from agent_platform.modules.data.row_policies import RowPolicyService
from agent_platform.modules.data.schemas import RowPolicyInput, RowPolicyPatch
from agent_platform.modules.data.service import DataService
from agent_platform.modules.identity.models import ServiceAccess, ServiceIdentity
from agent_platform.modules.integrations import delegated_knowledge as knowledge
from agent_platform.modules.integrations.delegated_contract import OPERATIONS, PREFIX, delegated_tool_spec
from agent_platform.modules.integrations.delegated_sources import authorize_delegated_source
from agent_platform.modules.integrations.delegated_tools import execute_tool, parameters
from agent_platform.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from agent_platform.modules.knowledge.service import KnowledgeService

from test_delegated_business_tools import site as base_site, call, human


@pytest.fixture
def site(base_site, monkeypatch):
    """沿原证书/用户签名/Turn夹具授权新工具，向量始终替换为内存函数。"""
    base_site.app.state.settings = base_site.settings
    with base_site.db.sessions.begin() as session:
        for key in ('human-role', 'engine-role'):
            row = session.get(Role, key)
            row.permissions = [*row.permissions, 'm08.read']
        identity = session.get(ServiceIdentity, 'engine-service')
        identity.permissions = [*identity.permissions, 'm08.read']
        access = session.get(ServiceAccess, 'engine-access')
        access.scopes = [*access.scopes, 'm08.read']
        session.add_all([
            KnowledgeSpace(id='space', workspace_id='ws', name='设备规程', created_by='human'),
            KnowledgeSpace(id='foreign-space', workspace_id='other-ws', name='其他企业资料', created_by='other-human'),
        ])
        session.add(KnowledgeDocument(id='document', workspace_id='ws', space_id='space', name='泵温度标准',
            source='paste', status='ready', created_by='human', chunk_count=2))
        session.add_all([KnowledgeChunk(id='chunk-' + str(index), workspace_id='ws', space_id='space',
            document_id='document', ordinal=index, content=text, vector=vector,
            source_location={'kind': 'text', 'paragraph': index + 1})
            for index, (text, vector) in enumerate([('轴承温度超过75℃需现场核查。', [1, 0]), ('振动超过标准需复核。', [0, 1])])])
    class MockEmbeddings:
        """只记录显式时限，绝不读取真实provider配置。"""
        def __init__(self):
            self.calls = []
        def embed(self, texts, *, timeout_seconds=None):
            self.calls.append((texts, timeout_seconds))
            assert timeout_seconds is None or 0 < timeout_seconds <= 15
            return [[1.0, 0.0] for _ in texts]
    base_site.vectors = MockEmbeddings()
    monkeypatch.setattr(knowledge, 'EmbeddingClient', lambda: base_site.vectors)
    yield base_site


@pytest.fixture
def saved(site, monkeypatch):
    """本夹具仅一次三行新CSV查询；之后禁止再次打开这个临时源。"""
    with site.db.sessions.begin() as session:
        actor = human(session)
        admin = Actor('human', 'ws', frozenset({'*'}), ('admin',), is_admin=True)
        data = DataService(session, actor, site.settings)
        dataset = data.import_csv({'name': '两条产线', 'csv_text': 'sample_id,line,value\nA1,A,10\nA2,A,12\nB1,B,99\n'})
        rules = [{'subject_type': 'user', 'subject_ids': ['human'],
                  'conditions': [{'field': 'line', 'operator': 'eq', 'value': 'A'}]}]
        policy = RowPolicyService(session, admin).create(dataset['datasource_id'],
            RowPolicyInput(table_name=dataset['table_name'], mode='rules', rules=rules).model_dump())
        result = data.execute_query({'datasource_id': dataset['datasource_id'],
            'sql': f'SELECT sample_id,value FROM "{dataset["table_name"]}" ORDER BY sample_id',
            'parameters': {}, 'max_rows': 10})
        site.saved_id, site.policy_id, site.source_id = result['id'], policy.id, dataset['datasource_id']
    monkeypatch.setattr(DataService, '_path', lambda *_args: pytest.fail('已保存工具不得重开企业源'))
    monkeypatch.setattr(DataService, 'execute_query', lambda *_args, **_kwargs: pytest.fail('工具不得执行新SQL'))
    return site


def test_saved_query_page_is_authorized_and_does_not_return_sql_or_hidden_counts(saved):
    response = call(saved, 'get_saved_query', {'id': saved.saved_id, 'page': '2', 'limit': '1'})
    assert response.status_code == 200, response.text
    assert response.json()['rows'] == [{'sample_id': 'A2', 'value': '12'}]
    assert response.json()['returned_count'] == 1 and response.json()['source_truncated'] is False
    assert not {'sql', 'parameters', 'total', 'has_more', 'plan'} & set(response.json())
    listing = call(saved, 'list_saved_queries', {'limit': '1'})
    assert listing.status_code == 200 and set(listing.json()) == {'items', 'returned_count'}
    assert listing.json()['items'][0]['id'] == saved.saved_id
    assert not {'result', 'rows', 'sql', 'parameters'} & set(listing.json()['items'][0])
    with saved.db.sessions() as session:
        source = session.get(NativeChatTurn, 'turn').sources[0]
        assert source['resource'] == {'kind': 'saved_query', 'id': saved.saved_id}
        assert source['queries'][0]['id'] == saved.saved_id and source['queries'][0]['scope_hash']
        authorize_delegated_source(session, human(session), source)
        stripped = {**source, 'queries': []}
        with pytest.raises(DomainError):
            authorize_delegated_source(session, human(session), stripped)


@pytest.mark.parametrize('revocation', ['rows', 'columns'])
def test_saved_query_history_and_directory_recheck_scope_without_new_sql(saved, revocation):
    response = call(saved, 'get_saved_query', {'id': saved.saved_id})
    assert response.status_code == 200
    with saved.db.sessions.begin() as session:
        if revocation == 'rows':
            row = session.get(data_models.RowPolicy, saved.policy_id)
            rules = deepcopy(row.rules)
            rules[0]['conditions'][0]['value'] = 'B'
            admin = Actor('human', 'ws', frozenset({'*'}), is_admin=True)
            RowPolicyService(session, admin).patch(row.id,
                RowPolicyPatch(revision=row.revision, mode='rules', rules=rules).model_dump())
        else:
            row = session.get(data_models.DataSource, saved.source_id)
            schema = deepcopy(row.schema_snapshot)
            next(c for c in schema[0]['columns'] if c['columnName'] == 'value')['allowed_roles'] = ['another-role']
            row.schema_snapshot = schema
        session.flush()
        with pytest.raises(DomainError):
            authorize_conversation_sources(session, human(session), session.get(NativeConversation, 'conversation'))
    assert call(saved, 'get_saved_query', {'id': saved.saved_id}).status_code in {403, 422}
    listing = call(saved, 'list_saved_queries', {'limit': '1'})
    assert listing.json() == {'items': [], 'returned_count': 0}


def test_knowledge_search_records_precise_evidence_and_uses_turn_remaining_budget(site):
    with site.db.sessions.begin() as session:
        session.get(NativeChatTurn, 'turn').deadline_at = datetime.now(timezone.utc) + timedelta(seconds=7)
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '轴承温度', 'top_k': '1'})
    assert response.status_code == 200, response.text
    assert set(response.json()) == {'items', 'returned_count'}
    assert response.json()['items'][0]['content'] == '轴承温度超过75℃需现场核查。'
    assert len(site.vectors.calls) == 1 and 0 < site.vectors.calls[0][1] <= 5
    with site.db.sessions() as session:
        turn = session.get(NativeChatTurn, 'turn')
        source = turn.sources[0]
        assert set(source) == {'version', 'workspace_id', 'resource', 'queries', 'files'}
        assert source['resource']['kind'] == 'knowledge_search'
        evidence = source['resource']['evidence']
        assert evidence['space_ids'] == ['space'] and evidence['chunks'][0]['chunk_id'] == 'chunk-0'
        assert len(evidence['chunks'][0]['content_sha256']) == 64
        assert '轴承温度' not in str(source)
        authorize_conversation_sources(session, human(session), session.get(NativeConversation, 'conversation'))
    assert len(site.vectors.calls) == 1


@pytest.mark.parametrize('change', ['document_deleted', 'chunk_changed', 'permission', 'original_revision', 'original_grant'])
def test_knowledge_history_rechecks_document_original_and_current_permission(site, change):
    if change.startswith('original'):
        with site.db.sessions.begin() as session:
            session.add(FileEntry(id='original', workspace_id='ws', owner_id='other-human', visibility='private',
                name='原始规程.txt', storage_key='not-opened', content_type='text/plain', size=20, sha256='f' * 64))
            session.add(ResourceGrant(workspace_id='ws', user_id='human', resource_type='file', resource_id='original', actions=['read']))
            session.add(ResourceReference(workspace_id='ws', source_type='kb_document', source_id='document',
                target_type='file', target_id='original'))
            document = session.get(KnowledgeDocument, 'document')
            document.file_id, document.source_sha256, document.source_revision = 'original', 'f' * 64, 1
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度', 'top_k': '1'})
    assert response.status_code == 200, response.text
    with site.db.sessions.begin() as session:
        if change == 'document_deleted':
            session.delete(session.get(KnowledgeDocument, 'document'))
        elif change == 'chunk_changed':
            session.get(KnowledgeChunk, 'chunk-0').content = '事后改变的正文'
        elif change == 'permission':
            role = session.get(Role, 'human-role')
            role.permissions = [value for value in role.permissions if value != 'm08.read']
        elif change == 'original_revision':
            session.get(FileEntry, 'original').revision += 1
        else:
            session.delete(session.scalar(select(ResourceGrant).where(ResourceGrant.resource_id == 'original')))
        session.flush()
        with pytest.raises(DomainError):
            authorize_conversation_sources(session, human(session), session.get(NativeConversation, 'conversation'))
    assert len(site.vectors.calls) == 1


def test_empty_knowledge_hit_still_freezes_space_permission(site):
    with site.db.sessions.begin() as session:
        session.get(KnowledgeDocument, 'document').status = 'failed'
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'})
    assert response.status_code == 200 and response.json() == {'items': [], 'returned_count': 0}
    assert site.vectors.calls == []
    with site.db.sessions() as session:
        source = session.get(NativeChatTurn, 'turn').sources[0]
        assert source['resource']['evidence']['space_ids'] == ['space']
        assert source['resource']['evidence']['chunks'] == []
        with pytest.raises(DomainError):
            authorize_delegated_source(session, Actor('human', 'ws', frozenset({'m02.read'})), source)


def test_knowledge_space_page_has_no_document_counts_or_foreign_names(site):
    response = call(site, 'list_knowledge_spaces', {'page': '1', 'limit': '1'})
    assert response.status_code == 200 and response.json()['returned_count'] == 1
    assert response.json()['items'][0]['name'] == '设备规程'
    assert not {'document_count', 'total', 'has_more'} & set(response.json())
    assert '其他企业' not in response.text
    assert call(site, 'search_platform_knowledge', {'space_id': 'foreign-space', 'query': '温度'}).status_code == 404
    assert site.vectors.calls == []


def test_candidate_overflow_stops_before_embedding_and_default_search_stays_compatible(site):
    with site.db.sessions() as session:
        service = KnowledgeService(session, human(session), site.vectors, settings=site.settings)
        with pytest.raises(DomainError) as error:
            service.search(['space'], '温度', candidate_limit=1)
        assert error.value.code == 'knowledge_candidate_budget_exceeded' and site.vectors.calls == []
        assert service.search(['space'], '温度')['items'][0]['chunk_id'] == 'chunk-0'


def test_tool_candidate_limit_is_enforced_before_model_and_source_commit(site, monkeypatch):
    monkeypatch.setattr(knowledge, 'MAX_CANDIDATES', 1)
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'})
    assert response.status_code == 422 and site.vectors.calls == []
    with site.db.sessions() as session:
        assert session.get(NativeChatTurn, 'turn').sources == []


@pytest.mark.parametrize('target', ['human-role', 'engine-role', 'original', 'cancel'])
def test_permission_revoked_during_embedding_is_rechecked_before_returning_text(site, target):
    original = site.vectors.embed
    def change_while_waiting(texts, **kwargs):
        with site.db.sessions.begin() as guard:
            if target in {'human-role', 'engine-role'}:
                row = guard.get(Role, target)
                row.permissions = [value for value in row.permissions if value != 'm08.read']
            elif target == 'original':
                guard.get(KnowledgeChunk, 'chunk-0').content = '等待期间来源被修改'
            else:
                guard.get(NativeChatTurn, 'turn').status = 'cancel_requested'
        return original(texts, **kwargs)
    site.vectors.embed = change_while_waiting
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'})
    assert response.status_code == 403, response.text
    assert '超过75' not in response.text and len(site.vectors.calls) == 1
    with site.db.sessions() as session:
        assert session.get(NativeChatTurn, 'turn').sources == []


def test_response_arriving_after_monotonic_deadline_is_discarded(site, monkeypatch):
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr(knowledge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = site.vectors.embed
    def delayed(texts, **kwargs):
        result = original(texts, **kwargs)
        clock[0] += 200
        return result
    site.vectors.embed = delayed
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'})
    assert response.status_code == 504 and '超过75' not in response.text
    assert len(site.vectors.calls) == 1
    with site.db.sessions() as session:
        assert session.get(NativeChatTurn, 'turn').sources == []


def test_expired_turn_and_embedding_failure_never_retry_or_append_sources(site):
    with site.db.sessions.begin() as session:
        session.get(NativeChatTurn, 'turn').deadline_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    response = call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'})
    assert response.status_code == 504 and site.vectors.calls == []
    with site.db.sessions.begin() as session:
        session.get(NativeChatTurn, 'turn').deadline_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    def fail(texts, **kwargs):
        site.vectors.calls.append((texts, kwargs))
        raise DomainError(504, 'embedding_timeout', '测试替身超时')
    site.vectors.embed = fail
    assert call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'}).status_code == 504
    assert len(site.vectors.calls) == 1
    with site.db.sessions() as session:
        assert session.get(NativeChatTurn, 'turn').sources == []


@pytest.mark.parametrize('operation,args', [
    ('search_platform_knowledge', {'space_id': 'space', 'query': 'x', 'top_k': '6'}),
    ('search_platform_knowledge', {'space_id': 'space', 'query': ' '}),
    ('search_platform_knowledge', {'space_id': 'space', 'query': 'x', 'timeout_seconds': '60'}),
    ('get_saved_query', {'id': 'query', 'sql': 'SELECT 1'}),
])
def test_new_tool_parameters_cannot_supply_sql_identity_or_budget(operation, args):
    with pytest.raises(DomainError):
        parameters(operation, args)


def test_four_new_tools_are_filtered_by_current_engine_permissions():
    actor = Actor('engine', 'ws', frozenset({'m02.read', 'm06.read', 'm08.read'}))
    paths = delegated_tool_spec(actor)['paths']
    assert set(paths) == {PREFIX + '/' + name for name in (
        'list_saved_queries', 'get_saved_query', 'list_knowledge_spaces', 'search_platform_knowledge')}
    assert len(OPERATIONS) == 19
    assert all(set(methods) == {'get'} for methods in paths.values())


def test_knowledge_receipt_cannot_strip_evidence_or_mix_other_space(site):
    assert call(site, 'search_platform_knowledge', {'space_id': 'space', 'query': '温度'}).status_code == 200
    with site.db.sessions() as session:
        source = session.get(NativeChatTurn, 'turn').sources[0]
        for change in ({'evidence': {}}, {'id': 'foreign-space'}):
            bad = deepcopy(source)
            bad['resource'].update(change)
            with pytest.raises(DomainError):
                authorize_delegated_source(session, human(session), bad)
