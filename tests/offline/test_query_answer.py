"""离线校验查询回答的完整性判定与取值渲染。

验证行为：流被截断或 markdown 未闭合时不算完整回答；实际查询值保留 null/0 并对不可信单元格做转义。
隔离方式：只调用 runtime.query_answer 的纯函数，不连库、不调用模型。
边界：null 与 0 必须区分展示；外部文本必须先转义再拼装。
"""
from app.modules.runtime.query_answer import incomplete_answer, query_values_answer


def test_incomplete_stream_and_dangling_markup_are_not_finished_answers():
    assert incomplete_answer({'finish_reason': 'length'}, '查询条件正确')
    assert incomplete_answer({'finish_reason': None}, '查询条件正确')
    assert incomplete_answer({'finish_reason': 'stop'}, '查询条件：\n\n**')
    assert not incomplete_answer({'finish_reason': 'stop'}, '```\n实际查询值：0\n```')
    assert not incomplete_answer({'finish_reason': 'stop'}, '**实际查询值：0**')


def test_actual_values_preserve_null_zero_and_escape_untrusted_cells():
    for value, expected in [(0, '实际查询值：0'), (None, '实际查询值：NULL'), ('<script>|x', '&lt;script&gt;&#124;x')]:
        answer = query_values_answer({'queryId': 'current', 'data': [{'value': value}]})
        assert expected in answer
    assert not query_values_answer({'data': [{'value': 123}]})
    assert not query_values_answer({'queryId': 'failed', 'success': False, 'data': [{'value': 123}]})
    assert '未返回记录' in query_values_answer({'queryId': 'current', 'data': []})
    assert '不能将其作为全量统计' in query_values_answer({'queryId': 'current', 'truncated': True, 'data': [{'value': 7}]})
