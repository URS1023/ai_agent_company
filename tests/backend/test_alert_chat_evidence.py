"""Regressions from the 2026-09-22 device-alert conversation.

中文说明：2026-09-22 设备预警对话中暴露的问题回归（证据、权限与状态口径）。
验证行为：分页与计数真实；追问读取已保存的处理历史而不重跑流程；对话不改写已保存的预警状态；
报告分页按父级权限精确过滤；未知摘要显示为"未知"；历史计数仍算证据但被当前 0 覆盖。
隔离方式：client 夹具（内存 SQLite + TestClient）+ monkeypatch，不连真实业务库与模型。
"""
import json

import pytest
from sqlalchemy import select

from app.models import Run, ToolCall
from app.modules.alerts.models import AlertProblem, AlertReport
from app.modules.runtime import agent
from business_worker_fakes import isolated_dsh_worker
from test_alerts import business_node, equipment, policy
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute, graph, result

API = '/api/v1/ai/business-actions/alerts/overview'


@pytest.mark.parametrize('user_request', ['查看设备预警和仓库备件库存', '查看设备预警并生成一份 Excel'])
def test_alert_grounding_preserves_composite_business_and_file_delivery_text(
    client, administrator, db, isolated_dsh_worker, user_request,
):
    """纯预警事实模板不能吞掉多业务分析和文件交付说明。"""
    _, problem = saved_problem(client, administrator, db)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    prepared = isolated_dsh_worker.prepare(administrator, cid, user_request)
    assert isolated_dsh_worker.execute(prepared['run_id'], 'read-alert', 1, 'view_device_alerts',
                                      {'problem_id': problem.id})['status'] == 'success'
    answer = '预警记录已读取；其余业务数据和文件尚未完成，请查看已返回的结果卡片。'
    settled = isolated_dsh_worker.settle(prepared['run_id'], 'completed', answer)
    assert settled['content'] == answer


def saved_problem(client, administrator, db, *, count=1, device_code='COMP-01'):
    rule, _ = policy(client, administrator, equipment(client, administrator, code=device_code))
    flow = create_flow(client, administrator, graph(business_node(rule)))
    execution = result(execute(client, administrator, flow, {
        'data': [{'device': f'EQ-{i}', 'amount': 220} for i in range(count)]}))
    report = db.get(AlertReport, execution['result']['outputs']['end']['alertReportId'])
    problem = db.scalar(select(AlertProblem).where(AlertProblem.report_id == report.id))
    return report, problem


def test_overview_keeps_unknown_counts_and_the_requested_problem_page(client, administrator, db):
    report, _ = saved_problem(client, administrator, db, count=12)
    report.result_json = {**report.result_json, 'summary': {}}
    db.commit()
    reply = client.post(API, headers=administrator, json={'limit': 10})
    assert reply.status_code == 200, reply.text
    data = reply.json()['data']
    assert len(data['problems']) == data['summary']['returnedProblems'] == 10
    assert data['reports'][0]['rows'] is None and data['reports'][0]['issues'] is None
    assert data['reports'][0]['outcomeLabel'] == '发现问题'
    assert data['reports'][0]['executionStatusLabel'] == '执行完成'
    assert all(p['statusLabel'] == '待处理' for p in data['problems'])
    assert '未记录' in data['message']
    assert '已处置' not in data['message']


def test_problem_followup_reads_real_processing_history_without_rerunning(client, administrator, db):
    report, problem = saved_problem(client, administrator, db)
    handled = client.post(f'/api/v1/alerts/problems/{problem.id}/actions', headers=administrator,
        json={'version': problem.version, 'status': 'acknowledged', 'note': '现场复核已安排，尚未确认消除故障'})
    assert handled.status_code == 200, handled.text
    before = len(db.scalars(select(Run)).all())
    reply = client.post(API, headers=administrator, json={'problemId': problem.id})
    assert reply.status_code == 200, reply.text
    data = reply.json()['data']
    history = data['processingHistory']
    assert history['problemId'] == problem.id and history['currentStatus'] == 'acknowledged'
    assert history['events'][0]['previousStatus'] == 'open'
    assert history['events'][0]['status'] == 'acknowledged'
    assert history['events'][0]['note'] == '现场复核已安排，尚未确认消除故障'
    assert data['reports'][0]['id'] == report.id and len(data['problems']) == 1
    assert '现场复核已安排' in data['message'] and '已确认' in data['message']
    assert '设备当前实时状态' in data['message']
    assert len(db.scalars(select(Run)).all()) == before
    # New detail access must not bypass the report's workflow/source permissions.
    role = create_role(client, administrator, 'alert-evidence-reader', ['alerts.view'], 'self')
    create_user(client, administrator, [role['id']], username='alert-evidence-reader')
    limited = activate_user(client, 'alert-evidence-reader')
    denied = client.post(API, headers=limited, json={'problemId': problem.id})
    assert denied.status_code in {403, 404}


@pytest.mark.parametrize('unavailable', [False, True])
def test_chat_does_not_rewrite_saved_alert_status_or_lose_it_on_model_failure(
    client, administrator, db, isolated_dsh_worker, unavailable,
):
    """DSH 原生结算保留实际预警事实，模型失败也不丢失已经读取的处置记录。"""
    _, problem = saved_problem(client, administrator, db)
    false_claim = '所有报告已处置，液压故障持续恶化，立即停机。'
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    prepared = isolated_dsh_worker.prepare(administrator, cid, '查看这个预警问题的详细处置进展')
    evidence = isolated_dsh_worker.execute(prepared['run_id'], 'read-alert', 1, 'view_device_alerts', {'problem_id': problem.id})
    assert evidence['status'] == 'success'
    state = 'failed' if unavailable else 'completed'
    error = {'code': 'model_unavailable', 'message': '模型暂不可用'} if unavailable else None
    settled = isolated_dsh_worker.settle(prepared['run_id'], state, false_claim, error)
    detail = isolated_dsh_worker.history(administrator, cid)
    assert detail['last_run']['status'] == state
    assert settled['status'] == state
    assert false_claim not in settled['content'] and false_claim not in detail['messages'][-1]['content']
    assert '待处理' in detail['messages'][-1]['content']
    assert '未找到处置变更记录' in detail['messages'][-1]['content']
    if unavailable:
        assert settled['error']['code'] == 'model_unavailable'


def test_report_page_is_exact_scoped_and_checks_parent_permission(client, administrator, db):
    report, problem = saved_problem(client, administrator, db, count=2)
    unrelated, _ = saved_problem(client, administrator, db, device_code='OTHER')
    first = client.post(API, headers=administrator,
        json={'reportId': report.id, 'limit': 1}).json()['data']
    second = client.post(API, headers=administrator,
        json={'reportId': report.id, 'limit': 1, 'offset': 1}).json()['data']
    assert first['summary']['problemsMayHaveMore'] is True
    assert second['summary']['problemsMayHaveMore'] is False
    assert first['summary']['reportsMayHaveMore'] is False
    assert second['summary']['returnedProblems'] == 1
    assert {row['reportId'] for row in first['problems'] + second['problems']} == {report.id}
    assert first['problems'][0]['id'] != second['problems'][0]['id']
    assert [row['id'] for row in second['reports']] == [report.id]
    mismatch = client.post(API, headers=administrator,
        json={'reportId': unrelated.id, 'problemId': problem.id})
    assert mismatch.status_code == 422
    role = create_role(client, administrator, 'alert-report-reader', ['alerts.view'], 'self')
    create_user(client, administrator, [role['id']], username='alert-report-reader')
    limited = activate_user(client, 'alert-report-reader')
    denied = client.post(API, headers=limited, json={'reportId': report.id})
    assert denied.status_code in {403, 404}


def test_handling_history_pages_are_real_events_and_keep_the_current_state(client, administrator, db):
    _, problem = saved_problem(client, administrator, db)
    version = problem.version
    for status, note in [('acknowledged', '已安排现场复核'), ('resolved', '复核完成，已确认恢复')]:
        handled = client.post(f'/api/v1/alerts/problems/{problem.id}/actions', headers=administrator,
            json={'version': version, 'status': status, 'note': note})
        assert handled.status_code == 200, handled.text
        version = handled.json()['data']['version']
    first = client.post(API, headers=administrator,
        json={'problemId': problem.id, 'limit': 1}).json()['data']
    second = client.post(API, headers=administrator,
        json={'problemId': problem.id, 'limit': 1, 'offset': 1}).json()['data']
    assert first['processingHistory']['hasMore'] is True
    assert first['processingHistory']['nextOffset'] == 1
    assert second['processingHistory']['hasMore'] is False
    assert second['processingHistory']['nextOffset'] is None
    assert first['processingHistory']['events'][0]['status'] == 'acknowledged'
    assert second['processingHistory']['events'][0]['status'] == 'resolved'
    assert first['problems'][0]['status'] == second['problems'][0]['status'] == 'resolved'
    assert first['processingHistory']['currentNote'] == '复核完成，已确认恢复'
    assert '本页不是完整历史' in first['message']


def test_saved_report_without_summary_is_presented_as_unknown(client, administrator, db):
    report, _ = saved_problem(client, administrator, db)
    report.result_json = {key: value for key, value in report.result_json.items() if key != 'summary'}
    db.commit()
    reply = client.post(API, headers=administrator, json={'reportId': report.id})
    assert reply.status_code == 200, reply.text
    data = reply.json()['data']
    assert data['reports'][0]['rows'] is None and data['reports'][0]['issues'] is None
    assert data['reports'][0]['outcome'] == 'issues'
    assert data['problems'][0]['status'] == 'open'
    assert '未记录' in data['message']


@pytest.mark.parametrize('alerts_first', [True, False])
def test_mixed_alert_and_workflow_reads_preserve_handling_facts_in_stream_and_reply(
    client, administrator, db, isolated_dsh_worker, alerts_first,
):
    """预警与报告工具的先后顺序不会把原始报告完成状态改成人工已处置。"""
    _, problem = saved_problem(client, administrator, db)
    reads = [('view_device_alerts', {'problem_id': problem.id}),
             ('query_workflow_results', {'kind': 'alert', 'limit': 5, 'history': True})]
    if not alerts_first:
        reads.reverse()
    false_claim = '所有报告已处置，液压故障持续恶化，立即停机。'
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    prepared = isolated_dsh_worker.prepare(administrator, cid, '查看这个预警问题的处置进展，并核对历史巡检报告')
    for sequence, (name, arguments) in enumerate(reads, 1):
        result = isolated_dsh_worker.execute(prepared['run_id'], f'read-{sequence}', sequence, name, arguments)
        assert result['status'] == 'success', result
    settled = isolated_dsh_worker.settle(prepared['run_id'], 'completed', false_claim)
    published = isolated_dsh_worker.append_event(prepared['run_id'], 'message.replace', {'content': settled['content']})
    detail = isolated_dsh_worker.history(administrator, cid)
    assert detail['last_run']['status'] == 'completed'
    answer = detail['messages'][-1]['content']
    assert false_claim not in published['data']['content'] and false_claim not in answer
    assert '待处理' in answer and '未找到处置变更记录' in answer
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == prepared['run_id'])).all()
    assert {call.tool_name for call in calls} == {'view_device_alerts', 'query_workflow_results'}
    assert all(call.status == 'completed' for call in calls)
    assert all(call.result_json['status'] == 'success' for call in calls)


@pytest.mark.parametrize('summary, expected_rows, expected_issues', [
    ({'totalRows': 1, 'issueCount': 1, 'normalCount': 0, 'criticalCount': 1}, 1, 1),
    ({'rows': 0, 'issues': 0, 'totalRows': 1, 'issueCount': 1}, 0, 0),
    ({'rows': 0, 'issueCount': 1}, 0, 1),
])
def test_legacy_report_counts_remain_evidence_and_current_zero_takes_precedence(
    client, administrator, db, summary, expected_rows, expected_issues,
):
    report, problem = saved_problem(client, administrator, db)
    report.result_json = {**report.result_json, 'summary': summary}
    problem.note = '已联系现场确认，等待反馈'
    db.commit()
    reply = client.post(API, headers=administrator, json={'problemId': problem.id})
    assert reply.status_code == 200, reply.text
    data = reply.json()['data']
    assert data['reports'][0]['rows'] == expected_rows
    assert data['reports'][0]['issues'] == expected_issues
    assert data['processingHistory']['events'] == []
    assert data['problems'][0]['status'] == 'open'
    assert '已联系现场确认，等待反馈' in data['message']
    assert '不能据此认定现场从未处理' in data['message']


@pytest.mark.parametrize('finding, expected_severity, expected_label', [
    ({'field': 'arm_hydraulic_pressure', 'level': 'critical', 'value': 29.4}, 'critical', '严重'),
    ({'severity': 'warning', 'level': 'critical'}, 'warning', '警告'),
    ({}, '', '未记录'),
])
def test_legacy_problem_severity_is_preserved_without_inventing_a_rule(
    client, administrator, db, finding, expected_severity, expected_label,
):
    _, problem = saved_problem(client, administrator, db)
    problem.finding = {'message': '现场记录待核实', **finding}
    db.commit()
    reply = client.post(API, headers=administrator, json={'problemId': problem.id})
    assert reply.status_code == 200, reply.text
    data = reply.json()['data']
    assert data['problems'][0]['severity'] == expected_severity
    assert data['problems'][0]['ruleName'] == ''
    assert f'原始问题等级：{expected_label}' in data['message']
