"""Pure checks for bounded provenance, without model or database calls.

中文说明：有界溯源检查：回答只能引用本轮实际取到的字段与值范围。
隔离方式：纯函数 + 离线夹具，不调用模型、不连数据库。
边界：范围之外的字段/数值不得作为证据出现。
"""
import json

import pytest

from app.modules.runtime.business_grounding import (
    business_answer_error, business_history_note, claims_business_query,
)
from app.modules.runtime.agent import _tool_payload_for_message
from app.providers.tool.base import ToolResult


@pytest.mark.parametrize('text', [
    '已为您查询到出库记录，共 10 条。',
    '根据数据库查询结果，很遗憾无法确定退货去向。',
    '查询到出库记录的完整字段信息了！',
    '根据完整数据显示，有 2 条记录了退货信息。',
    'I have queried the database. Query results show 10 rows.',
    '本轮还没查询，但是查询到完整记录了。',
])
def test_current_execution_claim_requires_receipt(text):
    assert claims_business_query(text)
    assert business_answer_error(text, has_current_receipt=False)
    assert not business_answer_error(text, has_current_receipt=True)


@pytest.mark.parametrize('text', [
    '我之前声称已查询，其实没有查询，这些结论需要撤回。',
    '本轮尚未核实，不能声称已查询到退货去向。',
    '解释一下：出库执行与退货单据并非同一类记录。',
    '上一轮查询返回了 5 行，这是历史结果。',
])
def test_historical_and_negative_claims_are_not_fresh_receipts(text):
    assert not business_answer_error(text, has_current_receipt=False)


def test_large_query_message_keeps_provenance_without_copying_rows_into_history():
    result = ToolResult(status='success', data={'success': True, 'queryId': 'q-proof',
        'datasourceId': 'source-proof', 'truncated': True,
        'data': [{'private': 'private-value-' * 5000}],
        'columns': [{'columnName': 'value'}]})
    message_result = _tool_payload_for_message(result)
    assert message_result['data']['full_result_available_in_run_trace']
    note = business_history_note([
        {'id': 'call', 'type': 'tool_call', 'name': 'query_business_data', 'arguments': {'query': '库存'}},
        {'id': 'call', 'type': 'tool_result', 'tool': 'query_business_data', 'result': message_result},
    ], '2026-09-20')
    assert 'q-proof' in note['content'] and 'source-proof' in note['content']
    assert 'private-value' not in note['content']
    assert len(note['content']) < 2000


def test_legacy_truncated_receipt_is_unknown_and_does_not_invent_a_count():
    note = business_history_note([
        {'id': 'call', 'type': 'tool_call', 'name': 'query_business_data', 'arguments': {'query': '库存'}},
        {'id': 'call', 'type': 'tool_result', 'tool': 'query_business_data',
         'result': {'status': 'success', 'data': {'full_result_available_in_run_trace': True}}},
    ], 'past')
    evidence = json.loads(note['content'].split(']', 1)[1])['queries'][0]
    assert evidence['returnedRows'] is None and evidence['queryId'] is None
    assert evidence['fullResultInTrace']
