"""用真实 HTTP 与临时 SQLite 核对历史快照撤权，不调用真实模型或业务库。"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import DataSource, IdempotencyClaim
from app.modules.analytics import metadata, queries
from app.modules.analytics.models import QueryHistory, SourceMetadata
from app.modules.reports.models import Report, ReportGeneration, ReportSourceImport
from app.modules.reports.source_reports import ensure_lynx_source_reports
from app.providers import model
from test_analytics import API, run_sql, source
from test_reports import REPORTS, create_report, fake_generated
from test_system_foundation import activate_user, create_role, create_user


def restrict_source(client, administrator, source, *, tables=None, columns=None, enabled=True):
    """通过正式编辑接口改变授权范围，允许其刷新临时库元数据。"""
    response = client.put(API + '/datasources/' + source['id'], headers=administrator, json={
        'name': source['name'], 'type': 'sqlite', 'databaseName': source['databaseName'],
        'version': source['version'], 'enabled': enabled,
        'allowedTables': ['sales'] if tables is None else tables,
        'allowedColumns': {} if columns is None else columns,
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def block_business_io(monkeypatch):
    """历史鉴权只能读平台记录，失败与成功读取均不能重新取数或调用模型。"""
    calls = []
    def reject(*args, **kwargs):
        calls.append(True)
        pytest.fail('读取留存结果不应连接业务数据或模型')
    monkeypatch.setattr(queries, 'execute_read', reject)
    monkeypatch.setattr(metadata, 'inspect_sql_source', reject)
    monkeypatch.setattr(metadata, 'inspect_elastic_source', reject)
    monkeypatch.setattr(model, 'chat_for_run', reject)
    return calls


def saved_report(client, administrator, source, result):
    """保留实际查询字段、SQL 与行，复现已保存大屏的静态副本。"""
    return create_report(client, administrator, datasourceIds=[source['id']], content={
        'widgets': [{'id': 'rows', 'component': 'JScrollBoard', 'x': 0, 'y': 0, 'w': 800, 'h': 500,
                     'data': deepcopy(result['data']), 'query': {'datasourceId': source['id'], 'sql': result['generatedSql']},
                     'config': {'dataType': 1, 'datasourceId': source['id'], 'queryId': result['queryId'],
                                'sql': result['generatedSql'], 'chartDataParsed': deepcopy(result['data']),
                                'snapshotOnly': True}}],
    })


@pytest.mark.parametrize('path', ['detail', 'json', 'xlsx', 'recent', 'source'])
@pytest.mark.parametrize('restriction', ['column', 'table'])
def test_query_snapshot_rechecks_current_table_and_column_scope(client, administrator, source, monkeypatch, path, restriction):
    result = run_sql(client, administrator, source).json()['data']
    restricted = restrict_source(client, administrator, source,
        tables=['restricted_data'] if restriction == 'table' else ['sales'],
        columns={'sales': ['id']} if restriction == 'column' else {})
    calls = block_business_io(monkeypatch)
    if path in {'json', 'xlsx'}:
        response = client.post(API + '/export', headers=administrator, json={'queryId': result['queryId'], 'format': path})
    else:
        suffix = {'detail': result['queryId'], 'recent': 'recent', 'source': 'datasource/' + restricted['id']}[path]
        response = client.get(API + '/history/' + suffix, headers=administrator)
    if path in {'recent', 'source'}:
        assert response.status_code == 200, response.text
        assert result['queryId'] not in {item['id'] for item in response.json()['data']}
    else:
        assert response.status_code in {403, 404}, response.text[:300]
        assert 'alert(1)' not in response.text
    assert calls == []


def test_allowed_history_survives_scope_narrowing_and_disabled_source(client, administrator, source, monkeypatch):
    result = run_sql(client, administrator, source, 'SELECT id FROM sales ORDER BY id').json()['data']
    restrict_source(client, administrator, source, columns={'sales': ['id']}, enabled=False)
    calls = block_business_io(monkeypatch)
    response = client.get(API + '/history/' + result['queryId'], headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()['data']['data'] == result['data']
    exported = client.post(API + '/export', headers=administrator, json={'queryId': result['queryId'], 'format': 'json'})
    assert exported.status_code == 200 and exported.json()['data'] == result['data']
    assert calls == []


@pytest.mark.parametrize('path', ['detail', 'list'])
def test_report_static_rows_recheck_revoked_columns(client, administrator, source, monkeypatch, path):
    result = run_sql(client, administrator, source).json()['data']
    report = saved_report(client, administrator, source, result)
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + ('/list' if path == 'list' else '/' + report['id']), headers=administrator)
    if path == 'list':
        assert response.status_code == 200, response.text
        assert report['id'] not in {item['id'] for item in response.json()['data']}
    else:
        assert response.status_code in {403, 404}, response.text
    assert calls == []


def test_report_revision_hides_revoked_values_and_cannot_restore_them(client, administrator, source, monkeypatch):
    result = run_sql(client, administrator, source).json()['data']
    report = saved_report(client, administrator, source, result)
    edited = client.put(REPORTS + '/' + report['id'], headers=administrator, json={
        'reportName': report['reportName'], 'version': report['version'], 'datasourceIds': [],
        'datasourceId': None, 'content': {'widgets': []},
    })
    assert edited.status_code == 200, edited.text
    current = edited.json()['data']
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + report['id'] + '/revisions', headers=administrator)
    assert response.status_code == 200, response.text
    revisions = response.json()['data']
    assert revisions[0]['available'] is True
    assert revisions[-1]['available'] is False and revisions[-1]['snapshot'] is None
    restored = client.post(REPORTS + '/' + report['id'] + '/restore', headers=administrator,
        json={'version': current['version'], 'revision': revisions[-1]['revision']})
    assert restored.status_code in {403, 404}, restored.text
    assert calls == []


def test_missing_cached_metadata_cannot_trigger_history_resync(client, administrator, source, db, monkeypatch):
    result = run_sql(client, administrator, source).json()['data']
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    cached = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == source['id']))
    db.delete(cached)
    db.commit()
    calls = block_business_io(monkeypatch)
    response = client.get(API + '/history/' + result['queryId'], headers=administrator)
    assert response.status_code in {403, 404}, response.text
    assert calls == []


@pytest.mark.parametrize('changed_scope', [False, True])
def test_failed_query_remains_visible_when_authorization_version_is_unchanged(client, administrator, source, db, monkeypatch, changed_scope):
    failed = run_sql(client, administrator, source, 'SELECT missing_field FROM sales')
    assert failed.status_code == 422, failed.text
    row = db.scalar(select(QueryHistory).where(QueryHistory.status == 'failed'))
    assert row is not None and not row.generated_sql
    if changed_scope:
        restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(API + '/history/recent', headers=administrator)
    assert response.status_code == 200, response.text
    assert any(item['id'] == row.id and item['status'] == 'failed' for item in response.json()['data']) is not changed_scope
    assert calls == []


@pytest.mark.parametrize('kind', ['history', 'report'])
def test_legacy_star_binding_cannot_hide_old_extra_columns(client, administrator, source, db, monkeypatch, kind):
    result = run_sql(client, administrator, source).json()['data']
    if kind == 'history':
        record = db.get(QueryHistory, result['queryId'])
        record.generated_sql = 'SELECT * FROM sales'
        db.commit()
        path = API + '/history/' + record.id
    else:
        result['generatedSql'] = 'SELECT * FROM sales'
        report = saved_report(client, administrator, source, result)
        path = REPORTS + '/' + report['id']
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(path, headers=administrator)
    assert response.status_code in {403, 404}, response.text
    assert calls == []


@pytest.mark.usefixtures('approved_report_semantics')
@pytest.mark.parametrize('legacy', [False, True])
def test_successful_generation_replay_never_requeries_and_legacy_also_rechecks_columns(client, administrator, source, db, monkeypatch, legacy):
    monkeypatch.setattr(model, 'chat_for_run', lambda *args, **kwargs: {'content': json.dumps(fake_generated(source['id']))})
    payload = {'theme': '展示设备金额', 'templateMode': 'none', 'datasourceId': source['id'], 'sessionId': 'replay-contract'}
    response = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()['data']
    if legacy:
        db.delete(db.scalar(select(IdempotencyClaim)))
        db.commit()
    with monkeypatch.context() as guarded:
        calls = block_business_io(guarded)
        replay = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
        assert replay.status_code == 200 and replay.json()['data']['generationId'] == result['generationId']
        assert replay.json()['data']['reused'] and calls == []
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    if legacy:
        db.delete(db.scalar(select(IdempotencyClaim)))
        db.commit()
    calls = block_business_io(monkeypatch)
    denied = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert denied.status_code in {403, 404}, denied.text
    assert calls == []


@pytest.mark.parametrize('inline_rows', [False, True])
def test_legacy_multisource_unbound_rows_fail_closed_but_empty_decoration_remains(client, administrator, source, db, monkeypatch, inline_rows):
    created = client.post(API + '/datasources', headers=administrator, json={
        'name': '多源隔离副本', 'type': 'sqlite', 'databaseName': source['databaseName'], 'allowedTables': ['sales'],
    })
    assert created.status_code == 201, created.text
    report = create_report(client, administrator, datasourceIds=[source['id'], created.json()['data']['id']],
                           content={'widgets': []})
    if inline_rows:
        # 模拟旧版本已保存但没有逐组件绑定的快照，只改一次性平台记录。
        record = db.get(Report, report['id'])
        record.content = {'canvas': {'width': 1920, 'height': 1080},
                          'widgets': [{'id': 'legacy', 'component': 'JScrollBoard', 'data': [{'note': 'private-note'}],
                                       'config': {}, 'x': 0, 'y': 0, 'w': 400, 'h': 300}]}
        db.commit()
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + report['id'], headers=administrator)
    if inline_rows:
        assert response.status_code in {403, 404}, response.text
    else:
        assert response.status_code == 200, response.text
    assert calls == []


@pytest.mark.usefixtures('approved_report_semantics')
def test_implicit_generation_replay_rechecks_original_source_after_access_moves(client, administrator, source, db, monkeypatch):
    permissions = ['home.view', 'dashboards.view', 'dashboards.execute', 'datasources.view', 'datasources.create']
    role = create_role(client, administrator, 'snapshot-author', permissions, 'workspace')
    create_user(client, administrator, [role['id']])
    author = activate_user(client)
    monkeypatch.setattr(model, 'chat_for_run', lambda *args, **kwargs: {'content': json.dumps(fake_generated(source['id']))})
    payload = {'theme': '展示设备金额', 'templateMode': 'none', 'sessionId': 'same-request-after-revocation'}
    generated = client.post(REPORTS + '/screen/ai-generate', headers=author, json=payload)
    assert generated.status_code == 200, generated.text
    result = generated.json()['data']
    created = client.post(API + '/datasources', headers=author, json={
        'name': '仅本人可见的新源', 'type': 'sqlite', 'databaseName': source['databaseName'], 'allowedTables': ['sales'],
    })
    assert created.status_code == 201, created.text
    changed = client.patch('/api/v1/system/roles/' + role['id'], headers=administrator,
        json={'version': role['version'], 'data_scope': 'self'})
    assert changed.status_code == 200, changed.text
    assert client.get(REPORTS + '/generations/' + result['generationId'], headers=author).status_code in {403, 404}
    calls = block_business_io(monkeypatch)
    replay = client.post(REPORTS + '/screen/ai-generate', headers=author, json=payload)
    assert replay.status_code in {403, 404}, replay.text
    assert calls == []
    assert len(db.scalars(select(ReportGeneration)).all()) == 1
    assert db.scalar(select(IdempotencyClaim)).status == 'completed'


@pytest.mark.usefixtures('approved_report_semantics')
def test_generation_detail_and_replay_reject_revoked_columns(client, administrator, source, monkeypatch):
    monkeypatch.setattr(model, 'chat_for_run', lambda *args, **kwargs: {'content': json.dumps(fake_generated(source['id']))})
    payload = {'theme': '展示设备金额', 'templateMode': 'none', 'datasourceId': source['id'], 'sessionId': 'column-scope'}
    response = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert response.status_code == 200, response.text
    identity = response.json()['data']['generationId']
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    detail = client.get(REPORTS + '/generations/' + identity, headers=administrator)
    assert detail.status_code in {403, 404}, detail.text
    replay = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert replay.status_code in {403, 404}, replay.text
    assert calls == []


@pytest.mark.parametrize('change', ['unchanged', 'value', 'sql', 'mapping', 'lineage', 'permission'])
def test_registered_catalog_reference_requires_unchanged_payload_and_visible_sources(client, administrator, source, db, monkeypatch, change):
    """固定素材证明只能来自平台映射，不能扩大成静态快照或 SQL 执行授权。"""
    datasource = db.get(DataSource, source['id'])
    datasource.name = 'variantorm'
    db.commit()
    imported = ensure_lynx_source_reports(db, administrator['X-Workspace-Id'], datasource.owner_user_id)
    assert imported['createdCount'] == 33
    db.commit()
    mapping = db.scalar(select(ReportSourceImport).where(ReportSourceImport.source_record_id == '29'))
    record = db.get(Report, mapping.report_id)
    if change in {'value', 'sql'}:
        content = deepcopy(record.content)
        target = next(widget for widget in content['widgets'] if widget.get('data') and widget.get('query', {}).get('sql'))
        if change == 'value':
            target['data'][0][next(iter(target['data'][0]))] = '未经授权的额外值'
        else:
            target['query']['sql'] = 'SELECT private_note FROM restricted_data'
        record.content = content
    elif change == 'mapping':
        mapping.source_content_sha256 = '0' * 64
    elif change == 'lineage':
        # 即使原有 lineage 完整不变，删去平台映射也不能继续享有素材例外。
        db.delete(mapping)
    db.commit()
    headers = administrator
    if change == 'permission':
        role = create_role(client, administrator, 'snapshot-no-source-view', ['home.view', 'dashboards.view'], 'workspace')
        create_user(client, administrator, [role['id']])
        headers = activate_user(client)
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + record.id, headers=headers)
    if change == 'unchanged':
        assert response.status_code == 200, response.text
        assert '仓库参考示例' in response.json()['data']['description']
        assert '不代表当前业务查询结果' in response.json()['data']['description']
        # 只读示例不会把源记录中的旧 SQL 变成可执行授权。
        rejected_sql = run_sql(client, headers, source, 'SELECT private_note FROM restricted_data')
        assert rejected_sql.status_code == 422, rejected_sql.text
    else:
        assert response.status_code in {403, 404}, response.text
    assert calls == []


@pytest.mark.parametrize('storage', ['echartsOption', 'config.option'])
def test_unbound_chart_option_cannot_hide_legacy_rows(client, administrator, source, db, monkeypatch, storage):
    """可渲染的数据可能只存在于图表 option，不能仅检查顶层 data。"""
    report = create_report(client, administrator, datasourceIds=[source['id']], content={'widgets': []})
    record = db.get(Report, report['id'])
    option = {'dataset': {'source': [{'note': '撤权后不能读'}]}}
    widget = {'id': 'legacy-option', 'component': 'JBar', 'config': {}, 'x': 0, 'y': 0, 'w': 400, 'h': 300}
    if storage == 'echartsOption':
        widget[storage] = option
    else:
        widget['config']['option'] = {'dataset': [option['dataset']]}
    record.content = {'widgets': [widget], 'canvas': {'width': 1920, 'height': 1080}}
    db.commit()
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + report['id'], headers=administrator)
    assert response.status_code in {403, 404}, response.text
    assert calls == []


@pytest.mark.usefixtures('approved_report_semantics')
def test_generation_with_lost_source_provenance_never_borrows_current_source(client, administrator, source, db, monkeypatch):
    """损坏的旧回执即使会自动选中新源，也不能把无来源内嵌行冒充当前授权结果。"""
    monkeypatch.setattr(model, 'chat_for_run', lambda *args, **kwargs: {'content': json.dumps(fake_generated(source['id']))})
    payload = {'theme': '展示设备金额', 'templateMode': 'none', 'sessionId': 'lost-source-proof'}
    response = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert response.status_code == 200, response.text
    identity = response.json()['data']['generationId']
    orphaned = {'generationId': identity, 'widgets': [{'component': 'JBar', 'data': [{'note': 'private'}]}]}
    generation = db.get(ReportGeneration, identity)
    generation.result_json = orphaned
    generation.request_json = {'session_id': payload['sessionId']}
    claim = db.scalar(select(IdempotencyClaim))
    claim.result_json = orphaned
    db.commit()
    calls = block_business_io(monkeypatch)
    assert client.get(REPORTS + '/generations/' + identity, headers=administrator).status_code == 403
    replay = client.post(REPORTS + '/screen/ai-generate', headers=administrator, json=payload)
    assert replay.status_code == 403, replay.text
    assert calls == [] and claim.status == 'completed'
