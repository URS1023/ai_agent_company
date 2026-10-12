"""Deleted knowledge bases must disappear from both user-facing dashboards.

中文说明：已删除的知识库必须同时从两个面向用户的看板消失（不能只在某一处隐藏）。
隔离方式：client 夹具（内存 SQLite + TestClient），不连业务库、不调用模型。
"""
from datetime import datetime

from app.ids import new_id
from app.models import KnowledgeBase, KnowledgeDocument


def test_deleted_knowledge_is_excluded_from_home_and_operations(client, administrator, db):
    now = datetime.utcnow()
    owner_id = client.get('/api/auth/me', headers=administrator).json()['user']['id']
    records = []
    for status in ('active', 'deleted'):
        base = KnowledgeBase(
            id=new_id(), workspace_id=administrator['X-Workspace-Id'], owner_user_id=owner_id,
            name=f'overview-{status}', status=status, created_at=now, updated_at=now,
        )
        records.append(base)
        db.add(base)
        db.add(KnowledgeDocument(
            id=new_id(), workspace_id=base.workspace_id, knowledge_base_id=base.id,
            file_id=new_id(), status='failed', error_summary=f'{status} document',
            created_at=now, updated_at=now,
        ))
    db.commit()

    home = client.get('/api/v1/platform/overview', headers=administrator)
    assert home.status_code == 200, home.text
    payload = home.json()
    payload = payload.get('data', payload)
    assert next(row['value'] for row in payload['metrics'] if row['key'] == 'knowledge') == 1

    dashboard = client.get('/api/v1/operations/business?domain=knowledge', headers=administrator)
    assert dashboard.status_code == 200, dashboard.text
    data = dashboard.json()['data']
    assert [row['id'] for row in data['availableObjects']] == [records[0].id]
    assert next(row['value'] for row in data['metrics'] if row['key'] == 'documents') == 1
    assert data['topItems'][0]['path'] == f'/knowledge?id={records[0].id}'
    assert [row['detail'] for row in data['issues']] == ['active document']
    assert data['issues'][0]['path'] == f'/knowledge?id={records[0].id}'
    assert client.get(
        f'/api/v1/operations/business?domain=knowledge&objectId={records[1].id}',
        headers=administrator,
    ).status_code == 404
