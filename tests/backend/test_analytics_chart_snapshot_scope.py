"""用真实 HTTP 验证无对象字段名的旧图表快照不能绕过撤权。"""
from __future__ import annotations

import pytest

from test_analytics import run_sql, source
from test_analytics_snapshot_authorization import block_business_io, restrict_source
from test_reports import REPORTS, create_report


@pytest.mark.parametrize('shape', ['matrix', 'series'])
def test_legacy_star_chart_values_require_provable_columns(client, administrator, source, monkeypatch, shape):
    # 模拟原先没有表列限制的可读快照，之后管理员再收窄权限。
    source = restrict_source(client, administrator, source, tables=[])
    result = run_sql(client, administrator, source).json()['data']
    first = result['data'][0]
    option = ({'dataset': {'source': [['id', 'note'], [first['id'], first['note']]]}}
              if shape == 'matrix' else {'series': [{'type': 'bar', 'data': [first['note']]}]})
    report = create_report(client, administrator, datasourceIds=[source['id']], content={
        'widgets': [{'id': 'legacy-shape', 'component': 'JBar', 'x': 0, 'y': 0, 'w': 400, 'h': 300,
                     'query': {'sql': 'SELECT * FROM sales'},
                     'config': {'datasourceId': source['id'], 'option': option}}],
    })
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + report['id'], headers=administrator)
    assert response.status_code in {403, 404}, response.text
    assert calls == []


@pytest.mark.parametrize('shape,expected', [
    ('object', 200), ('header', 200), ('dimensions', 200), ('explicit-series', 200), ('count-series', 200),
    ('no-header', 403), ('forbidden-header', 403), ('conflicting-dimensions', 403), ('extra-cell', 403),
])
def test_chart_column_evidence_preserves_safe_shapes_without_guessing_by_width(client, administrator, source, monkeypatch, shape, expected):
    """保留对象行、可验证矩阵和显式 SQL 系列；一列碰巧匹配也不能替代字段证明。"""
    source = restrict_source(client, administrator, source, tables=[])
    run_sql(client, administrator, source)
    datasets = {
        'object': {'source': [{'id': 1}]},
        'header': {'source': [['id'], [1]], 'sourceHeader': True},
        'dimensions': {'source': [[1]], 'dimensions': [{'name': 'id', 'type': 'number'}], 'sourceHeader': False},
        'no-header': {'source': [[1]]},
        'forbidden-header': {'source': [['note'], ['不允许读取']]},
        'conflicting-dimensions': {'source': [['note'], ['不允许读取']], 'dimensions': ['id']},
        'extra-cell': {'source': [['id'], [1, '不允许读取']]},
    }
    sql = 'SELECT * FROM sales'
    if shape.endswith('series'):
        sql = 'SELECT id FROM sales' if shape == 'explicit-series' else 'SELECT COUNT(*) AS total FROM sales'
        option = {'series': [{'type': 'bar', 'data': [1 if shape == 'explicit-series' else 5]}]}
    else:
        option = {'dataset': datasets[shape]}
    report = create_report(client, administrator, datasourceIds=[source['id']], content={
        'widgets': [{'id': 'column-proof', 'component': 'JBar', 'x': 0, 'y': 0, 'w': 400, 'h': 300,
                     'query': {'sql': sql}, 'config': {'datasourceId': source['id'], 'option': option}}],
    })
    restrict_source(client, administrator, source, columns={'sales': ['id']})
    calls = block_business_io(monkeypatch)
    response = client.get(REPORTS + '/' + report['id'], headers=administrator)
    assert response.status_code == expected, response.text
    assert calls == []
