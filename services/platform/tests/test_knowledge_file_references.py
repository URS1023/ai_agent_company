"""知识导入原件引用回归：仅本轮临时SQLite/文件和HTTPX MockTransport，不连接上游。"""

from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.core.context import Actor, get_actor
from agent_platform.core.database import Database
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.models import AuditEvent, FileEntry, Organization, ResourceRecord, ResourceReference, User, Workspace
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import Settings
from agent_platform.modules.files.router import router as files_router
from agent_platform.modules.integrations.router import router as integrations_router
from agent_platform.modules.integrations.service import INTEGRATION, SPACE


@pytest.fixture
def site(tmp_path):
    # 显式新SQLite绝对路径和新文件根；测试生命周期结束关闭所有连接，不读取现有.env。
    database_file = tmp_path / 'knowledge-file-references.sqlite3'
    settings = Settings(data_dir=tmp_path, database_url='sqlite:///' + database_file.as_posix(), environment='test')
    database = Database(settings)
    assert database.engine.url.get_backend_name() == 'sqlite'
    assert Path(database.engine.url.database).resolve() == database_file.resolve()
    database.initialize()
    app = FastAPI()
    app.state.settings, app.state.database = settings, database
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.url.host == '127.0.0.1' and request.url.port == 12080
        assert request.url.path == '/v1/datasets/dataset-new/document/create-by-text'
        assert request.headers['Authorization'] == 'Bearer dataset-only-key'
        return httpx.Response(200, json={'document': {'id': 'native-document-new', 'name': '设备操作规程',
            'indexing_status': 'waiting'}, 'batch': 'batch-new'})
    app.state.native_http_transport = httpx.MockTransport(upstream)
    class Vault:
        def get(self, ref, workspace_id, session=None):
            assert ref == 'dataset-ref' and workspace_id == 'ws-one'
            return 'dataset-only-key'
    app.state.secret_vault = Vault()
    app.include_router(integrations_router)
    app.include_router(files_router)
    @app.exception_handler(DomainError)
    def domain_error(request, error):
        return JSONResponse(status_code=error.status, content={'error': {'code': error.code, 'message': error.message}})
    actor = [Actor('user-one', 'ws-one', frozenset({'*'}), is_admin=True)]
    app.dependency_overrides[get_actor] = lambda: actor[0]
    with database.sessions.begin() as session:
        session.add(Organization(id='org-new', name='本轮独立企业'))
        session.flush()
        session.add_all([Workspace(id='ws-one', organization_id='org-new', name='甲车间'),
            Workspace(id='ws-two', organization_id='org-new', name='乙车间'),
            User(id='user-one', organization_id='org-new', username='new-one', display_name='甲用户', password_hash='unused'),
            User(id='user-two', organization_id='org-new', username='new-two', display_name='乙用户', password_hash='unused')])
        session.flush()
        store = RecordStore(session, actor[0])
        integration = store.create(INTEGRATION, {'name': '本轮Mock Dify', 'engine': 'dify',
            'base_url': 'http://127.0.0.1:12080/v1', 'console_url': 'http://127.0.0.1:12080',
            'dataset_credential_ref': 'dataset-ref', 'enabled': True})
        space = store.create(SPACE, {'name': '本轮规程', 'integration_id': integration['id'], 'native_id': 'dataset-new', 'enabled': True})
        first = FileService(session, actor[0], settings).store_bytes('甲车间规程.txt', '停机后检查设备。'.encode(), 'text/plain')
        second_actor = Actor('user-two', 'ws-two', frozenset({'*'}), is_admin=True)
        other = FileService(session, second_actor, settings).store_bytes('乙车间私有文件.txt', '其他车间不可泄露正文。'.encode(), 'text/plain')
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(app=app, database=database, settings=settings, client=client,
            actor=actor, space=space, file=first, other_file=other, calls=calls)
    database.dispose()


def imports(site, file_id=None):
    return site.client.post('/api/v1/knowledge-spaces/' + site.space['id'] + '/document-imports',
        json={'file_id': file_id or site.file['id']})


def local_counts(site):
    with site.database.sessions() as session:
        return (session.scalar(select(func.count()).select_from(ResourceRecord).where(ResourceRecord.kind == 'knowledge_document')),
            session.scalar(select(func.count()).select_from(ResourceReference).where(ResourceReference.source_type == 'knowledge_document')))


def test_import_preserves_source_snapshot_and_prevents_deleting_referenced_original(site):
    response = imports(site)
    assert response.status_code == 201, response.text
    document = response.json()
    assert document['file_id'] == site.file['id'] and document['source_sha256'] == site.file['sha256']
    assert document['source_revision'] == site.file['revision'] and document['native']['indexing_status'] == 'waiting'
    assert document['native_document_id'] == 'native-document-new' and document['batch'] == 'batch-new'
    expected = [{'type': 'knowledge_document', 'id': document['id']}]
    preview = site.client.post('/api/v1/files/' + site.file['id'] + '/deletion-previews')
    assert preview.status_code == 200
    assert preview.json()['can_delete'] is False and preview.json()['references'] == expected
    deletion = site.client.delete('/api/v1/files/' + site.file['id'], params={'revision': site.file['revision']})
    assert deletion.status_code == 409 and deletion.json()['error']['code'] == 'file_in_use'
    with site.database.sessions() as session:
        references = FileService(session, site.actor[0], site.settings).references(site.file['id'])
        assert references == expected
        assert session.get(FileEntry, site.file['id']).deleted is False
    assert local_counts(site) == (1, 1) and len(site.calls) == 1


@pytest.mark.parametrize('mode', ['upstream_failed', 'native_id_missing', 'reference_insert_failed'])
def test_import_failure_rolls_back_local_document_audit_and_reference(site, mode):
    if mode == 'upstream_failed':
        site.app.state.native_http_transport = httpx.MockTransport(lambda request: site.calls.append(request) or httpx.Response(503))
    elif mode == 'native_id_missing':
        site.app.state.native_http_transport = httpx.MockTransport(lambda request: site.calls.append(request) or httpx.Response(200, json={'document': {}}))
    def reject_reference(mapper, connection, target):
        if target.source_type == 'knowledge_document':
            raise DomainError(409, 'test_reference_insert_failed', '本轮引用写入失败')
    if mode == 'reference_insert_failed': event.listen(ResourceReference, 'before_insert', reject_reference)
    try:
        response = imports(site)
    finally:
        if mode == 'reference_insert_failed': event.remove(ResourceReference, 'before_insert', reject_reference)
    assert response.status_code in (409, 502, 503)
    assert local_counts(site) == (0, 0)
    with site.database.sessions() as session:
        audit = session.scalars(select(AuditEvent).where(AuditEvent.resource_type == 'knowledge_document')).all()
        assert not audit and session.get(FileEntry, site.file['id']).deleted is False
    assert len(site.calls) == 1


def test_cross_workspace_file_and_references_are_not_exposed_or_sent_upstream(site):
    result = imports(site, site.other_file['id'])
    assert result.status_code == 404 and result.json()['error']['code'] == 'file_not_found'
    assert site.other_file['name'] not in result.text and '其他车间不可泄露正文' not in result.text
    assert not site.calls and local_counts(site) == (0, 0)
    first = imports(site)
    assert first.status_code == 201
    site.actor[0] = Actor('user-two', 'ws-two', frozenset({'*'}), is_admin=True)
    hidden = site.client.post('/api/v1/files/' + site.file['id'] + '/deletion-previews')
    assert hidden.status_code == 404 and first.json()['id'] not in hidden.text
    blocked = imports(site, site.other_file['id'])
    assert blocked.status_code == 404 and len(site.calls) == 1
