"""平台基础行为回归；每项只使用本轮新建的临时SQLite与本机文件。"""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from agent_platform.core.models import ResourceRecord
from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.tasks import TaskService
from agent_platform.modules.workspace.router import task_scope
from agent_platform.core.settings import Settings
from agent_platform.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / 'owned-platform'))
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/setup', json={
            'username': 'test-admin', 'password': 'Isolated-test-passphrase-123!',
            'display_name': '隔离验证账号', 'workspace_name': '本次临时工作区',
        })
        assert response.status_code == 201, response.json()
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        yield client


def create_graph(client):
    response = client.post('/api/v1/ontologies', json={'name': '设备本体',
        'concepts': [{'name': '设备', 'properties': {'温度': 'number'}}, {'name': '车间', 'properties': {}}],
        'relations': [{'name': '安装于', 'source_type': '设备', 'target_type': '车间'}]})
    assert response.status_code == 201, response.json()
    ontology = response.json()
    response = client.post('/api/v1/graphs', json={'name': '设备图谱', 'ontology_id': ontology['id']})
    assert response.status_code == 201
    return ontology, response.json()


def test_graph_import_is_atomic_repeatable_and_exports_actual_nodes(client):
    _, graph = create_graph(client)
    url = '/api/v1/graphs/' + graph['id']
    body = {'nodes': [{'id': 'pump', 'name': '泵', 'type': '设备', 'properties': {'温度': 56}},
                       {'id': 'plant', 'name': '车间', 'type': '车间', 'properties': {}}],
            'edges': [{'source': 'pump', 'target': 'plant', 'type': '安装于'}]}
    first = client.post(url + '/imports', json=body)
    assert first.status_code == 201, first.json()
    assert client.post(url + '/imports', json=body).json()['entity_ids'] == first.json()['entity_ids']
    exported = client.post(url + '/exports').json()
    assert len(exported['nodes']) == 2 and len(exported['edges']) == 1
    invalid = {**body, 'nodes': [{'id': 'new', 'name': '不得残留', 'type': '设备', 'properties': {}}],
               'edges': [{'source': 'new', 'target': 'missing', 'type': '安装于'}]}
    assert client.post(url + '/imports', json=invalid).status_code == 422
    assert len(client.get(url).json()['nodes']) == 2
    assert client.post(url + '/quality-checks').json()['total'] == 0


def test_graph_path_starts_from_selected_node_before_applying_budget(client):
    _, graph = create_graph(client)
    url = '/api/v1/graphs/' + graph['id']
    for name in ('A无关设备', 'B无关设备'):
        assert client.post(url + '/entities', json={'name': name, 'type': '设备'}).status_code == 201
    pump = client.post(url + '/entities', json={'name': 'Z循环泵', 'type': '设备'}).json()
    plant = client.post(url + '/entities', json={'name': 'Y包装车间', 'type': '车间'}).json()
    client.post(url + '/relations', json={'source': pump['id'], 'target': plant['id'], 'type': '安装于'})
    query = client.post(url + '/queries', json={'entity_id': pump['id'], 'target_id': plant['id'], 'depth': 2, 'limit': 2})
    assert query.status_code == 200, query.json()
    assert query.json()['path'] == [pump['id'], plant['id']]


def test_document_validates_structure_and_idempotency_covers_content(client):
    invalid = client.post('/api/v1/document-jobs', json={'title': '错误输入', 'sections': [1]})
    assert invalid.status_code == 422
    headers = {'Idempotency-Key': 'document-stable-request'}
    body = {'title': '检查记录', 'sections': [{'title': '实测', 'rows': [['设备', '温度'], ['循环泵', 56]]}]}
    first = client.post('/api/v1/document-jobs', json=body, headers=headers)
    assert first.status_code == 201, first.json()
    repeated = client.post('/api/v1/document-jobs', json=body, headers=headers)
    assert repeated.json()['id'] == first.json()['id']
    body['sections'][0]['rows'][1][1] = 99
    assert client.post('/api/v1/document-jobs', json=body, headers=headers).status_code == 409


def test_task_owner_does_not_bypass_revoked_business_permission(client):
    from agent_platform.core.models import Task
    me = client.get('/api/v1/me').json()
    workspace = me['workspace']['id']
    owner = Actor(user_id=me['user']['id'], workspace_id=workspace,
                  permissions=frozenset({'m01.read', 'm11.read'}), roles=())
    with client.app.state.database.sessions.begin() as session:
        task = TaskService(session, owner).create('inspection', '私有判定上下文', access_permission='m11.read')
        assert TaskService(session, owner).get(task['id'])['id'] == task['id']
        revoked = Actor(user_id=owner.user_id, workspace_id=workspace, permissions=frozenset({'m01.read'}), roles=())
        with pytest.raises(DomainError) as denied:
            TaskService(session, revoked).get(task['id'])
        assert denied.value.status == 404
        assert list(session.scalars(select(Task).where(*task_scope(revoked)))) == []


def test_graph_constraints_revision_and_publication_snapshot(client):
    _, graph = create_graph(client)
    url = '/api/v1/graphs/' + graph['id']
    invalid = client.post(url + '/entities', json={'name': '泵', 'type': '设备', 'properties': {'不存在': 1}})
    assert invalid.status_code == 422
    node = client.post(url + '/entities', json={'name': '循环泵', 'type': '设备', 'properties': {'温度': 68.4}}).json()
    plant = client.post(url + '/entities', json={'name': '包装车间', 'type': '车间', 'properties': {}}).json()
    edge = client.post(url + '/relations', json={'source': node['id'], 'target': plant['id'], 'type': '安装于'})
    assert edge.status_code == 201, edge.json()
    detail = client.get(url).json()
    assert len(detail['nodes']) == 2 and len(detail['edges']) == 1
    assert detail['revision'] == 4
    published = client.post(url + '/publications', json={'revision': detail['revision']})
    assert published.status_code == 200, published.json()
    updated = client.patch(url + '/entities/' + node['id'], json={'revision': node['revision'], 'properties': {'温度': 65.0}})
    assert updated.status_code == 200
    conflict = client.patch(url + '/entities/' + node['id'], json={'revision': node['revision'], 'properties': {'温度': 60.0}})
    assert conflict.status_code == 409
    with client.app.state.database.sessions() as session:
        snapshot = session.scalar(select(ResourceRecord).where(ResourceRecord.kind == 'graph-publication'))
        old = next(n for n in snapshot.data['snapshot']['nodes'] if n['id'] == node['id'])
        assert old['properties']['温度'] == 68.4
    connected = client.delete(url + '/entities/' + node['id'] + '?revision=2')
    assert connected.status_code == 409


def test_structured_extraction_requires_explicit_review(client):
    ontology, graph = create_graph(client)
    job = client.post('/api/v1/extraction-jobs', json={'name': '台账映射', 'ontology_id': ontology['id'],
        'entity_type': '设备', 'name_field': '设备名称', 'field_mapping': {'温度': 'temperature'},
        'records': [{'设备名称': '样本设备', 'temperature': 51.2}]}).json()
    assert job['status'] == 'pending_review'
    url = '/api/v1/extraction-jobs/' + job['id'] + '/reviews'
    assert client.post(url, json={'graph_id': graph['id'], 'revision': job['revision']}).status_code == 422
    assert client.get('/api/v1/graphs/' + graph['id']).json()['nodes'] == []
    approved = client.post(url, json={'decision': 'approve', 'graph_id': graph['id'], 'revision': job['revision']})
    assert approved.status_code == 200, approved.json()
    assert approved.json()['status'] == 'approved'
    task = client.get('/api/v1/tasks/' + job['task_id']).json()
    assert task['status'] == 'succeeded'


def test_private_graph_read_grant_does_not_authorize_mutation(client):
    _, graph = create_graph(client)
    role = client.post('/api/v1/roles', json={'name': '图谱编辑', 'permissions': ['m09.read', 'm09.write']}).json()
    member = client.post('/api/v1/members', json={'username': 'graph-member', 'password': 'Isolated-member-pass-123!',
        'display_name': '图谱成员', 'role_ids': [role['id']]}).json()
    grant = client.post('/api/v1/resource-grants', json={'resource_type': 'graph', 'resource_id': graph['id'],
        'user_id': member['user_id'], 'actions': ['read']})
    assert grant.status_code == 201, grant.json()
    other = TestClient(client.app)
    try:
        logged = other.post('/api/v1/auth/login', json={'username': 'graph-member', 'password': 'Isolated-member-pass-123!'})
        assert logged.status_code == 200
        other.headers['X-CSRF-Token'] = logged.json()['csrf_token']
        assert other.get('/api/v1/graphs/' + graph['id']).status_code == 200
        write = other.post('/api/v1/graphs/' + graph['id'] + '/entities', json={'name': '越权设备', 'type': '设备', 'properties': {}})
        assert write.status_code == 403
        client.delete('/api/v1/resource-grants/' + grant.json()['id'])
        assert other.get('/api/v1/graphs/' + graph['id']).status_code == 404
    finally:
        other.close()


@pytest.mark.parametrize('format,entry', [('docx', 'word/document.xml'), ('xlsx', 'xl/workbook.xml'), ('pptx', 'ppt/presentation.xml')])
def test_office_generation_returns_real_file_and_revocation_is_enforced(client, format, entry):
    generated = client.post('/api/v1/document-jobs', json={'title': '质检追溯', 'format': format,
        'sections': [{'title': '判定', 'text': '来源为本轮隔离验证', 'rows': [['样本', '结果'], ['SP-01', '不合格']]}]})
    assert generated.status_code == 201, generated.json()
    assert generated.json()['status'] == 'succeeded'
    file_id = generated.json()['outputs']['file']['id']
    link = client.post('/api/v1/files/' + file_id + '/download-links').json()
    content = client.get(link['url'])
    assert content.status_code == 200
    with ZipFile(BytesIO(content.content)) as zipped:
        assert entry in zipped.namelist()
    assert client.get(link['url'].replace('signature=', 'signature=bad')).status_code == 403
    assert client.post('/api/v1/auth/logout').status_code == 200
    assert client.get(link['url']).status_code == 401


def test_uploaded_file_owner_scope_and_reference_delete_guard(client):
    uploaded = client.post('/api/v1/files', files={'file': ('../../notes.txt', '本次验证文本'.encode(), 'text/plain')})
    assert uploaded.status_code == 201, uploaded.json()
    file = uploaded.json()
    assert file['name'] == 'notes.txt'
    generated = client.post('/api/v1/document-jobs', json={'title': '引用来源', 'format': 'docx',
        'source_file_ids': [file['id']], 'sections': [{'text': '已关联原始资料'}]})
    assert generated.status_code == 201
    preview = client.post('/api/v1/files/' + file['id'] + '/deletion-previews').json()
    assert preview['can_delete'] is False
    assert client.delete('/api/v1/files/' + file['id'] + '?revision=1').status_code == 409


def test_backup_and_restore_drill_never_replace_live_database(client):
    backup = client.post('/api/v1/backup-jobs')
    assert backup.status_code == 201, backup.json()
    result = client.post('/api/v1/restore-drills', json={'backup_id': backup.json()['id']})
    assert result.status_code == 201, result.json()
    assert result.json()['integrity'] == 'ok'
    assert result.json()['live_database_modified'] is False
    assert client.get('/api/v1/me').status_code == 200


def test_workspace_selection_rotates_csrf_and_isolates_resources(client):
    _, graph = create_graph(client)
    workspace = client.post('/api/v1/workspaces', json={'name': '另一个本地工作区'}).json()
    old_token = client.headers['X-CSRF-Token']
    switched = client.post('/api/v1/workspace-selections', json={'workspace_id': workspace['id']})
    assert switched.status_code == 200
    assert switched.json()['csrf_token'] != old_token
    assert client.get('/api/v1/graphs/' + graph['id']).status_code == 404
    denied = client.post('/api/v1/ontologies', json={'name': '无CSRF', 'concepts': [{'name': '设备'}]})
    assert denied.status_code == 403
