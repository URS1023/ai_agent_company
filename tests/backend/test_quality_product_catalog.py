"""验证质量产品目录的可见性、授权与重建规则。

验证行为：目录不跨工作区泄露；不可用工作流被过滤；读取要求数据源权限；
普通成员看不到被隐藏的配置；目录重建与已复核来源一致且不重新绑定数据源。
隔离方式：以真实配置夹具 + HTTP 客户端（conftest 内存 SQLite）验证权限与错误文案，
重建路径直接调用 product_catalog 纯逻辑，不连真实业务库。
边界：有损文本（乱码）的来源快照必须拒绝重建；缺工作流时给出可操作且脱敏的错误。
"""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.errors import AppError, NotFoundError, PermissionErrorEx
from app.modules.quality import product_catalog


@pytest.fixture
def catalogue_config(tmp_path, monkeypatch):
    from app.modules.alerts import card_images
    monkeypatch.setattr(card_images, 'image_map', lambda *args: {})
    path = tmp_path / 'quality_catalog.json'
    config = {'workspaceId': 'workspace-a', 'datasourceId': 'source-a', 'sourceLabel': '导入副本', 'snapshotAt': '2026-09-10',
              'products': [{'id': 'p1', 'name': '石灰', 'code': 'p1', 'hasRequirements': True}],
              'entries': [{'workflowId': 'allowed', 'productId': 'p1', 'category': 'incoming'},
                          {'workflowId': 'denied', 'productId': 'p1', 'category': 'process'}]}
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(product_catalog, 'CATALOG_PATH', path)
    monkeypatch.setattr(product_catalog, 'require_permission', lambda *args: None)
    monkeypatch.setattr(product_catalog, 'access_policy', lambda ctx: SimpleNamespace(administrator=False), raising=False)
    return config


def test_catalogue_never_leaks_another_workspace(catalogue_config, monkeypatch):
    def unexpected(*args):
        raise AssertionError('Must not read source or workflows of another workspace')
    monkeypatch.setattr(product_catalog, 'get_source', unexpected)
    monkeypatch.setattr(product_catalog, 'get_workflow', unexpected)
    result = product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-b'))
    assert result['products'] == result['entries'] == []


@pytest.mark.parametrize('failure', [PermissionErrorEx, NotFoundError])
def test_catalogue_filters_unavailable_workflows(catalogue_config, monkeypatch, failure):
    checked = []
    monkeypatch.setattr(product_catalog, 'get_source', lambda ctx, key: checked.append(key))
    def flow(ctx, key):
        if key == 'denied':
            raise failure('不可访问')
        return SimpleNamespace(name='石灰来料质检', status='published', published_revision=1)
    monkeypatch.setattr(product_catalog, 'get_workflow', flow)
    result = product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-a'))
    assert checked == ['source-a']
    assert [entry['workflowId'] for entry in result['entries']] == ['allowed']
    assert result['snapshotAt'] == '2026-09-10'


def test_catalogue_requires_source_access(catalogue_config, monkeypatch):
    def denied(*args):
        raise PermissionErrorEx('未授权数据源')
    monkeypatch.setattr(product_catalog, 'get_source', denied)
    with pytest.raises(PermissionErrorEx):
        product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-a'))


def test_products_endpoint_requires_login(client):
    assert client.get('/api/v1/quality/products').status_code == 401


def test_configured_product_flows_are_scoped_in_every_read():
    root = Path(__file__).resolve().parents[2]
    catalogue = json.loads((root / 'configurations/quality_catalog.json').read_text(encoding='utf-8'))
    assert len({product['id'] for product in catalogue['products']}) == 16
    assert sum(product['hasRequirements'] for product in catalogue['products']) == 7
    assert len(catalogue['entries']) == 12
    assert sum(entry['formCount'] for entry in catalogue['entries']) == 3148
    assert len(catalogue['pendingEntries']) == 1
    assert catalogue['pendingEntries'][0]['formCount'] == 2
    assert '类别' in catalogue['pendingEntries'][0]['reason']
    for entry in catalogue['entries']:
        graph = json.loads((root / 'configurations/szh_new/product_workflows' / (entry['workflowId'] + '.json')).read_text(encoding='utf-8'))['graph']
        assert not {'code', 'database-query', 'subflow'} & {node['kind'] for node in graph['nodes']}
        reads = [node for node in graph['nodes'] if node['kind'] == 'read-values']
        assert len(reads) == 4
        category = ('incoming', 'process', 'finished').index(entry['category']) + 1
        for node in reads:
            config = node['config']
            assert config['parameterSources'] == {'category': 'fixed', 'formNumber': 'input', 'qualityTemplate': 'fixed'}
            assert config['parameters']['category'] == category
            assert config['parameters']['qualityTemplate'] == entry['templateId']
        start = next(node for node in graph['nodes'] if node['kind'] == 'start')
        assert [variable['variable'] for variable in start['config']['variables']] == ['formNumber']


@pytest.mark.parametrize('invalid', ['{', '[]', '{"workspaceId":"workspace-a","entries":null}'])
def test_invalid_catalogue_has_actionable_sanitized_error(catalogue_config, invalid):
    product_catalog.CATALOG_PATH.write_text(invalid, encoding='utf-8')
    with pytest.raises(AppError) as caught:
        product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-a'))
    assert caught.value.status_code == 503
    assert caught.value.code == 'quality_catalog_invalid'
    assert '管理员' in caught.value.message
    assert 'workspace-a' not in caught.value.message


def test_admin_sees_missing_workflow_and_pending_scope(catalogue_config, monkeypatch):
    catalogue_config['pendingEntries'] = [{'templateName': '待确认模板', 'reason': '检验类别缺失', 'formCount': 2}]
    product_catalog.CATALOG_PATH.write_text(json.dumps(catalogue_config), encoding='utf-8')
    monkeypatch.setattr(product_catalog, 'access_policy', lambda ctx: SimpleNamespace(administrator=True), raising=False)
    monkeypatch.setattr(product_catalog, 'get_source', lambda *args: None)
    monkeypatch.setattr(product_catalog, 'get_workflow', lambda *args: (_ for _ in ()).throw(NotFoundError('missing')))
    result = product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-a'))
    assert result['entries'] == []
    assert result['issues'][0]['code'] == 'workflow_unavailable'
    assert result['issues'][0]['count'] == 2
    assert result['pendingEntries'][0]['formCount'] == 2


def test_regular_user_does_not_learn_hidden_configuration(catalogue_config, monkeypatch):
    monkeypatch.setattr(product_catalog, 'get_source', lambda *args: None)
    monkeypatch.setattr(product_catalog, 'get_workflow', lambda *args: (_ for _ in ()).throw(PermissionErrorEx('private')))
    result = product_catalog.catalogue(SimpleNamespace(workspace_id='workspace-a'))
    assert result['entries'] == []
    assert result.get('issues', []) == []
    assert result.get('pendingEntries', []) == []
    assert 'denied' not in json.dumps(result)


def test_catalogue_rebuild_matches_reviewed_source_without_rebinding():
    from scripts.rebuild_quality_catalog import build_catalogue, workflow_scopes
    root = Path(__file__).resolve().parents[2]
    catalogue = json.loads((root / 'configurations/quality_catalog.json').read_text(encoding='utf-8'))
    source = json.loads((root / 'configurations/szh_new/quality_source_snapshot.json').read_text(encoding='utf-8'))
    rebuilt = build_catalogue(source, catalogue, workflow_scopes(root / 'configurations/szh_new/product_workflows'))
    assert rebuilt == catalogue
    assert {(p['id'], p['name'], p['code']) for p in catalogue['products']} == {
        (p['id'], p['name'], p['code']) for p in source['products']}
    assert sum(e['formCount'] for e in catalogue['entries'] + catalogue['pendingEntries']) == sum(r['formCount'] for r in source['form_groups'])
    changed_binding = {**catalogue, 'workspaceId': 'other-workspace', 'datasourceId': 'other-source'}
    rebuilt = build_catalogue(source, changed_binding, {})
    assert rebuilt['workspaceId'] == 'other-workspace' and rebuilt['datasourceId'] == 'other-source'
    assert rebuilt['entries'] == []
    assert sum(e['formCount'] for e in rebuilt['pendingEntries']) == 3150


def test_catalogue_rebuild_rejects_lossy_source_text():
    from scripts.rebuild_quality_catalog import build_catalogue
    root = Path(__file__).resolve().parents[2]
    catalogue = json.loads((root / 'configurations/quality_catalog.json').read_text(encoding='utf-8'))
    source = json.loads((root / 'configurations/szh_new/quality_source_snapshot.json').read_text(encoding='utf-8'))
    source['products'][0]['name'] = '石\ufffd'
    with pytest.raises(ValueError, match='Lossy text decoding'):
        build_catalogue(source, catalogue, {})


def test_catalogue_endpoint_reports_missing_workflows_using_real_authorization(client, administrator, db, tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.ids import new_id
    from app.models import AuthIdentity, DataSource
    from app.modules.workflows.models import Workflow
    owner = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == 'workspace-admin')).user_id
    workspace = administrator['X-Workspace-Id']
    source = DataSource(id=new_id(), workspace_id=workspace, owner_user_id=owner,
                        name='隔离目录来源', provider='mysql', connection_config={},
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    flow = Workflow(id=new_id(), workspace_id=workspace, owner_user_id=owner, name='目录流程', graph={})
    db.add_all([source, flow])
    db.commit()
    path = tmp_path / 'catalogue.json'
    monkeypatch.setattr(product_catalog, 'CATALOG_PATH', path)
    config = {'workspaceId': workspace, 'datasourceId': source.id, 'sourceLabel': '隔离样例', 'snapshotAt': '2026-09-20',
              'products': [{'id': 'p', 'name': '产品', 'code': 'P', 'hasRequirements': True}],
              'entries': [{'workflowId': flow.id, 'productId': 'p', 'category': 'incoming'},
                          {'workflowId': new_id(), 'productId': 'p', 'category': 'process'}]}
    path.write_text(json.dumps(config), encoding='utf-8')
    response = client.get('/api/v1/quality/products', headers=administrator)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert [entry['workflowId'] for entry in data['entries']] == [flow.id]
    assert data['issues'][0]['count'] == 1
    path.write_text('{invalid', encoding='utf-8')
    response = client.get('/api/v1/quality/products', headers=administrator)
    assert response.status_code == 503
    assert response.json()['code'] == 'quality_catalog_invalid'
