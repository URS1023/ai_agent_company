"""粘贴入口与原件索引共用生命周期，仅新SQLite及模型替身。"""

from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.modules.knowledge.ingestion import KnowledgeIngestion
from agent_platform.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from agent_platform.modules.knowledge.service import KnowledgeService


@pytest.fixture
def site(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'new-text.sqlite3').as_posix())
    Base.metadata.create_all(engine, tables=[item.__table__ for item in (
        KnowledgeSpace, KnowledgeDocument, KnowledgeChunk)])
    actor = Actor('writer', 'new-workspace', frozenset({'m08.*'}))
    with Session(engine, expire_on_commit=False) as session:
        class FakeEmbeddings:
            def __init__(self):
                self.calls = []
            def embed(self, texts, **kwargs):
                assert not session.in_transaction()
                self.calls.append((list(texts), kwargs))
                return [[1.0, 0.0] for _ in texts]
        embeddings = FakeEmbeddings()
        service = KnowledgeService(session, actor, embeddings, settings=Settings(data_dir=tmp_path))
        space = service.create_space('本轮标准')
        session.commit()
        yield session, actor, service, space, embeddings, engine
    engine.dispose()


def test_text_is_persisted_before_model_and_returns_full_document_dto(site):
    session, actor, service, space, embeddings, engine = site
    original_embed = embeddings.embed
    def inspect_before_model(texts, **kwargs):
        with Session(engine) as observer:
            document = observer.scalar(select(KnowledgeDocument))
            assert document.status == 'indexing' and document.source == 'paste'
            assert observer.scalar(select(func.count()).select_from(KnowledgeChunk)) > 0
        return original_embed(texts, **kwargs)
    embeddings.embed = inspect_before_model
    document = service.import_text(space['id'], '贴文标准', '设备温度上限75℃。')
    assert document['status'] == 'ready' and document['source_file'] is None
    assert document['error'] is None and document['error_code'] is None
    assert document['parser']['name'] == 'platform-text'
    assert document['created_at'].endswith('+00:00')
    assert 0 < embeddings.calls[0][1]['timeout_seconds'] <= 60


def test_text_model_failure_survives_outer_rollback_and_only_explicit_index_retries(site):
    session, actor, service, space, embeddings, engine = site
    attempts = []
    def fail(texts, **kwargs):
        attempts.append(texts)
        raise DomainError(503, 'embedding_unavailable', '向量模型未连接')
    embeddings.embed = fail
    document = service.import_text(space['id'], '失败保留', '必须保留的用户正文')
    assert document['status'] == 'failed' and document['error_code'] == 'embedding_unavailable'
    session.rollback()
    assert service.document(document['id'])['status'] == 'failed'
    assert service.chunks(document['id'])['items'][0]['content'] == '必须保留的用户正文'
    assert service.search([space['id']], '正文')['items'] == [] and len(attempts) == 1
    embeddings.embed = lambda texts, **kwargs: [[1, 0] for _ in texts]
    retried = KnowledgeIngestion(service, service.settings).index(document['id'])
    assert retried['status'] == 'ready' and retried['error'] is None


def test_text_uses_same_batch_limit_and_cross_batch_dimension_check(site):
    session, actor, service, space, embeddings, engine = site
    text = '\n\n'.join(('第' + str(index) + '段 ') + '有' * 550 for index in range(65))
    document = service.import_text(space['id'], '多块标准', text)
    assert document['status'] == 'ready'
    assert [len(texts) for texts, _ in embeddings.calls] == [32, 32, 1]
    calls = []
    def changed_dimension(texts, **kwargs):
        calls.append(1)
        return [[1.0, 0.0] if len(calls) == 1 else [1.0] for _ in texts]
    embeddings.embed = changed_dimension
    failed = service.import_text(space['id'], '维度变化', text)
    assert failed['status'] == 'failed' and failed['error_code'] == 'embedding_invalid_response'
    assert len(calls) == 2
    assert all(not row.vector for row in session.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.document_id == failed['id'])))


@pytest.mark.parametrize('name,text', [('', 'content'), ('long' * 61, 'content'), ('名称', ''),
    ('名称', '文' * 200001), ('名称', '\n\n'.join('文' * 300 for _ in range(501)))],
    ids=['empty-name', 'long-name', 'empty-text', 'text-limit', 'chunk-limit'])
def test_invalid_text_is_rejected_before_saving_or_dispatching(site, name, text):
    session, actor, service, space, embeddings, engine = site
    with pytest.raises(DomainError):
        service.import_text(space['id'], name, text)
    assert not embeddings.calls
    assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0


def test_direct_text_service_requires_write_permission(site):
    session, actor, service, space, embeddings, engine = site
    readonly = KnowledgeService(session, Actor(actor.user_id, actor.workspace_id, frozenset({'m08.read'})),
        embeddings, settings=service.settings)
    with pytest.raises(DomainError) as error:
        readonly.import_text(space['id'], '无权提交', 'content')
    assert error.value.status == 403 and not embeddings.calls


def test_sql_error_after_reserved_text_does_not_query_failure_compensation(site):
    session, actor, service, space, embeddings, engine = site
    failed, statements_after_error = [False], []
    def fail(texts, **kwargs):
        failed[0] = True
        raise OperationalError('synthetic', {}, RuntimeError('new temp only'))
    def observe(connection, cursor, statement, parameters, context, executemany):
        if failed[0]:
            statements_after_error.append(statement)
    embeddings.embed = fail
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with pytest.raises(OperationalError):
            service.import_text(space['id'], 'SQL异常', '保留正文预约')
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert not statements_after_error
    with Session(engine) as observer:
        assert observer.scalar(select(KnowledgeDocument)).status == 'indexing'
        assert observer.scalar(select(KnowledgeChunk)).content == '保留正文预约'


def test_index_batches_share_one_deadline_and_do_not_dispatch_after_it(site, monkeypatch):
    from types import SimpleNamespace
    session, actor, service, space, embeddings, engine = site
    timeline, remaining = [0.0], []
    monkeypatch.setattr('agent_platform.modules.knowledge.ingestion.time',
        SimpleNamespace(monotonic=lambda: timeline[0]))
    def slow(texts, **kwargs):
        remaining.append(kwargs['timeout_seconds'])
        timeline[0] += 31
        return [[1, 0] for _ in texts]
    embeddings.embed = slow
    text = '\n\n'.join('第' + str(index) + '段' + '有' * 550 for index in range(65))
    document = service.import_text(space['id'], '单个总预算', text)
    assert document['status'] == 'failed' and document['error_code'] == 'document_index_timeout'
    assert remaining == [60, 29]
    assert all(not row.vector for row in session.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.document_id == document['id'])))


@pytest.mark.parametrize('model_fails', [False, True], ids=['ready', 'failed'])
def test_original_text_http_fields_and_201_return_full_status_dto(site, monkeypatch, model_fails):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_platform.core.context import get_actor
    from agent_platform.core.database import get_session
    from agent_platform.core.embeddings import EmbeddingClient
    from agent_platform.modules.knowledge.router import router
    session, actor, service, space, embeddings, engine = site
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_actor] = lambda: actor
    def request_session():
        with Session(engine, expire_on_commit=False) as request_session:
            try:
                yield request_session
                request_session.commit()
            except Exception:
                request_session.rollback()
                raise
    app.dependency_overrides[get_session] = request_session
    calls = []
    def response(self, texts, **kwargs):
        calls.append(texts)
        if model_fails:
            raise DomainError(503, 'embedding_unavailable', '模型未连接')
        return [[1, 0] for _ in texts]
    monkeypatch.setattr(EmbeddingClient, 'embed', response)
    with TestClient(app) as client:
        result = client.post('/api/v1/kb/spaces/' + space['id'] + '/documents',
            json={'name': '保留原API', 'text': '正文温度75℃', 'source': 'paste'})
        assert result.status_code == 201
        document = result.json()
        assert document['name'] == '保留原API' and document['source'] == 'paste'
        assert document['status'] == ('failed' if model_fails else 'ready')
        assert document['source_file'] is None and document['chunk_count'] == 1
        assert document['error_code'] == ('embedding_unavailable' if model_fails else None)
        assert client.get('/api/v1/kb/documents/' + document['id']).json()['status'] == document['status']
        assert len(calls) == 1
