"""Query-to-screen delivery keeps verified scope even when all model calls are unavailable."""
from datetime import datetime
import json

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.models import AuthIdentity, DataSource, Run, ToolCall, User
from app.ids import new_id
from app.modules.analytics.models import QueryHistory
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.business_ai.schemas import DashboardDraftInput
from app.modules.reports import query_delivery, service
from app.modules.reports.models import ReportGeneration
from app.modules.reports.schemas import ReportInput
from app.modules.runtime import service as runtime
from app.modules.business_ai.models import BusinessActionReceipt
from app.providers import model
from test_analytics import API, source


def setup_query(client, headers, db, *, request='帮我做成数字大屏'):
    user = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == 'workspace-admin')).user_id
    ctx = auth_context_from_tool({'db': db, 'workspace_id': headers['X-Workspace-Id'], 'user_id': user})
    source_id = db.scalar(select(DataSource.id).where(DataSource.name == '销售分析源'))
    if source_id is None:
        source_id = db.scalar(select(DataSource.id))
    original = client.post(API + '/query', headers=headers, json={'datasourceId': source_id,
        'mode': 'sql', 'query': 'SELECT id, department, amount FROM sales WHERE amount >= :floor ORDER BY id LIMIT 2',
        'parameters': {'floor': 100}}).json()['data']
    cid = client.post('/api/chat/conversations', headers=headers, json={}).json()['id']
    previous = runtime.create_run(db, workspace_id=ctx.workspace_id, conversation_id=cid, user_id=user)
    previous.status = 'completed'
    call = runtime.record_tool_call(db, workspace_id=ctx.workspace_id, run_id=previous.id,
                                    step_id=None, tool_version_id=None,
                                    tool_name='query_business_data', arguments={'query': '库存', 'mode': 'sql'})
    runtime.finish_tool_call(db, call, {'status': 'success', 'data': original})
    now = runtime.create_run(db, workspace_id=ctx.workspace_id, conversation_id=cid, user_id=user,
        context_snapshot={'user_request': request, 'datasource_ids': [source_id]})
    db.commit()
    payload = DashboardDraftInput(requestKey='query-delivery-regression', requirement=request,
                                  datasourceIds=[source_id], templateMode='none')
    return ctx, now, payload, original, call


def test_verified_queries_deliver_editable_report_without_model(client, administrator, source, db, monkeypatch):
    ctx, run, payload, old, call = setup_query(client, administrator, db)
    monkeypatch.setattr(model, 'chat_for_run', lambda *a, **kw: pytest.fail('Verified conversion must not need a model'))
    queries = query_delivery.verified_queries(ctx, payload, run.id)
    assert [q.id for q in queries] == [old['queryId']]
    generated = query_delivery.generate(ctx, payload, queries)
    data_widgets = [w for w in generated['widgets'] if w.get('query')]
    assert len(data_widgets) == 1 and data_widgets[0]['data'] == old['data']
    assert data_widgets[0]['query']['parameters'] == {'floor': 100}
    assert {h['key'] for h in data_widgets[0]['config']['option']['header']} == {'id', 'department', 'amount'}
    assert generated['sourceQueryIds'] == [old['queryId']]
    assert generated['generationMode'] == 'verified_query_layout'
    saved = service.save_report(ctx, ReportInput(reportName='查询结果大屏', templateMode='none',
        datasourceIds=[source['id']], content={'canvas': generated['canvas'], 'widgets': generated['widgets']}))
    detail = client.get('/api/v1/reports/' + saved.id, headers=administrator)
    assert detail.status_code == 200
    content = json.loads(detail.json()['data']['content'])
    assert next(w for w in content['widgets'] if w.get('query'))['data'] == old['data']
    refreshed = client.get(f'/api/v1/reports/{saved.id}/widgets/{data_widgets[0]["id"]}/data', headers=administrator)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['data']['data'] == old['data']
    assert db.get(Run, generated['runId']).status == 'completed'
    assert db.get(ReportGeneration, generated['generationId']).status == 'completed'


@pytest.mark.parametrize('question', ['不要生成大屏', '怎么生成大屏？', '生成库存趋势大屏，增加同比',
                                     '帮我做成数字大屏，只要三个组件', '生成深色大屏'])
def test_conversion_never_drops_extra_requirements(client, administrator, source, db, question):
    ctx, run, payload, old, call = setup_query(client, administrator, db, request=question)
    assert query_delivery.verified_queries(ctx, payload, run.id) == []


def test_changed_source_is_not_reused(client, administrator, source, db):
    ctx, run, payload, old, call = setup_query(client, administrator, db)
    row = db.get(QueryHistory, old['queryId'])
    row.datasource_version -= 1
    db.commit()
    with pytest.raises(ValidationError, match='来源配置已变化'):
        query_delivery.verified_queries(ctx, payload, run.id)
    assert not db.scalars(select(ReportGeneration)).all()


def test_foreign_query_or_latest_failure_cannot_fall_back_to_older_success(client, administrator, source, db):
    ctx, run, payload, old, call = setup_query(client, administrator, db)
    original_owner = db.get(QueryHistory, old['queryId']).owner_user_id
    other = User(id=new_id(), display_name='Other owner', status='active',
                 created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(other)
    db.flush()
    db.get(QueryHistory, old['queryId']).owner_user_id = other.id
    db.commit()
    assert query_delivery.verified_queries(ctx, payload, run.id) == []
    db.get(QueryHistory, old['queryId']).owner_user_id = original_owner
    later = runtime.record_tool_call(db, workspace_id=ctx.workspace_id, run_id=run.id,
        step_id=None, tool_version_id=None, tool_name='query_business_data', arguments={'query': 'different query'})
    runtime.finish_tool_call(db, later, {'status': 'failed'}, error_code='model_error', error_summary='failed')
    db.commit()
    assert query_delivery.verified_queries(ctx, payload, run.id) == []


def test_tool_conversion_delivers_real_saved_report_and_reloads_receipt(client, administrator, source, db, monkeypatch):
    """直接验收正式业务网关与保存回执，不再模拟已移除的旧聊天模型入口。"""
    from app.providers.tool.gateway import ToolGateway, build_context

    ctx, run, payload, old, call = setup_query(client, administrator, db)
    monkeypatch.setattr(model, 'chat_for_run', lambda *a, **kw: pytest.fail('Do not redesign a verified query'))
    context = build_context(db=db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                            run_id=run.id, selected_datasource_ids=[source['id']])
    args = {'requirement': '帮我做成数字大屏', 'template_mode': 'none'}
    result = ToolGateway(db).execute(ctx.workspace_id, 'create_business_dashboard', args, context)
    assert result.status == 'success', result.to_dict()
    db.expire_all()
    receipt = db.scalar(select(BusinessActionReceipt))
    assert receipt.status == 'completed'
    assert receipt.result_json['provenance']['generationMode'] == 'verified_query_layout'
    assert receipt.result_json['provenance']['sourceQueryIds'] == [old['queryId']]
    report = client.get('/api/v1/reports/' + receipt.resource_id, headers=administrator).json()['data']
    assert report['reportName'] == '查询结果数字大屏'
    content = json.loads(report['content'])
    widget = next(w for w in content['widgets'] if w.get('query'))
    assert widget['data'] == old['data']
    refreshed = client.get(f'/api/v1/reports/{receipt.resource_id}/widgets/{widget["id"]}/data', headers=administrator)
    assert refreshed.status_code == 200 and refreshed.json()['data']['data'] == old['data'], refreshed.text
    repeated = ToolGateway(db).execute(ctx.workspace_id, 'create_business_dashboard', args, context)
    assert repeated.status == 'success' and repeated.data['reused'] is True
    assert repeated.data['reportId'] == receipt.resource_id
    assert len(db.scalars(select(BusinessActionReceipt)).all()) == 1
