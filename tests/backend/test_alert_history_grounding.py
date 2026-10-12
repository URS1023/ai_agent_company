"""DSH 每轮预警回答必须使用本轮事实，历史回执不能证明今天已经处理。"""
from business_worker_fakes import isolated_dsh_worker
from test_alert_chat_evidence import saved_problem


def test_followup_cannot_claim_resolution_without_reading_the_problem(
    client, administrator, db, isolated_dsh_worker,
):
    _, problem = saved_problem(client, administrator, db)
    unsupported = '该液压问题已经解决，现场处理已经完成。'
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    first = isolated_dsh_worker.prepare(administrator, cid, '查看液压问题的处置进展')
    assert isolated_dsh_worker.execute(first['run_id'], 'read-first', 1, 'view_device_alerts',
                                      {'problem_id': problem.id})['status'] == 'success'
    assert '待处理' in isolated_dsh_worker.settle(first['run_id'], 'completed', unsupported)['content']
    second = isolated_dsh_worker.prepare(administrator, cid, '这个问题现在处理完成了吗？')
    missing = isolated_dsh_worker.settle(second['run_id'], 'completed', unsupported)
    assert missing['status'] == 'partial' and missing['error']['code'] == 'alert_evidence_missing'
    assert unsupported not in missing['content']
    third = isolated_dsh_worker.prepare(administrator, cid, '重新读取这个预警问题的当前处置记录')
    assert isolated_dsh_worker.execute(third['run_id'], 'read-current', 1, 'view_device_alerts',
                                      {'problem_id': problem.id})['status'] == 'success'
    current = isolated_dsh_worker.settle(third['run_id'], 'completed', unsupported)
    detail = isolated_dsh_worker.history(administrator, cid)
    assert current['status'] == detail['last_run']['status'] == 'completed'
    assert unsupported not in detail['messages'][-1]['content']
    assert '待处理' in detail['messages'][-1]['content']
