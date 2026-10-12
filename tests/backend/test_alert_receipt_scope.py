"""DSH 后续预警范围读取失败，必须使先前范围失去完整答复资格。"""
import pytest
from sqlalchemy import select

from app.models import ToolCall
from business_worker_fakes import isolated_dsh_worker
from test_alert_chat_evidence import saved_problem


@pytest.mark.parametrize('failure', ['invalid_problem_id', 'missing_problem'])
@pytest.mark.parametrize('model_unavailable', [False, True])
def test_failed_second_problem_read_cannot_deliver_the_first_problem_as_success(
    client, administrator, db, isolated_dsh_worker, failure, model_unavailable,
):
    _, first_problem = saved_problem(client, administrator, db)
    second_problem_id = 'invalid-id' if failure == 'invalid_problem_id' else '0' * 26
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    prepared = isolated_dsh_worker.prepare(administrator, cid, '先查看第一个预警问题，再核实第二个问题的处置进展；第二个未读到请明确说明。')
    first = isolated_dsh_worker.execute(prepared['run_id'], 'first-problem', 1, 'view_device_alerts',
                                       {'problem_id': first_problem.id})
    second = isolated_dsh_worker.execute(prepared['run_id'], 'second-problem', 2, 'view_device_alerts',
                                        {'problem_id': second_problem_id})
    assert first['status'] == 'success' and second['status'] == 'failed'
    state = 'failed' if model_unavailable else 'completed'
    error = {'code': 'model_unavailable', 'message': '模型暂不可用'} if model_unavailable else None
    settled = isolated_dsh_worker.settle(prepared['run_id'], state, '两个问题都已经处理完成。', error)
    detail = isolated_dsh_worker.history(administrator, cid)
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == prepared['run_id'])
                      .order_by(ToolCall.started_at, ToolCall.id)).all()
    assert len(calls) == 2 and calls[0].status == 'completed' and calls[1].status == 'failed'
    if failure == 'invalid_problem_id':
        assert calls[1].error_code == 'schema_validation_error'
    else:
        assert calls[1].error_code != 'schema_validation_error'
    expected = 'failed' if model_unavailable else 'partial'
    assert settled['status'] == detail['last_run']['status'] == expected
    answer = detail['messages'][-1]['content']
    assert '未全部完成' in answer and '尚未核实' in answer
    assert '已经处理完成' not in answer and '已读取保存的设备预警记录' not in answer
