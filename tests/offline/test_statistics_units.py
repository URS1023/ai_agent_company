"""Returned rows with different units cannot supply a meaningful grand total.

中文说明：返回行单位不一致时不得给出合计（否则是错误结论）。
隔离方式：直接调用统计纯函数，不连库、不调用模型。
边界：单位不一致必须拒绝求和并如实说明。
"""
import pytest
from app.modules.analytics.statistics import describe, enhanced_analysis


COLUMNS = [
    {'columnName': 'item', 'comment': '物料', 'dataType': 'varchar'},
    {'columnName': 'quantity', 'comment': '实际可用库存', 'dataType': 'decimal'},
    {'columnName': 'unit', 'comment': '计量单位', 'dataType': 'bigint'},
]


@pytest.mark.parametrize('units', [(12, 16), (12, None), (None, None)])
def test_mixed_or_missing_units_do_not_produce_total_trend_or_chart(units):
    rows = [{'item': 'oil', 'quantity': 2, 'unit': units[0]}, {'item': 'bar', 'quantity': 120, 'unit': units[1]}]
    stats, insights, analysis, charts = describe(rows, COLUMNS, truncated=False)
    assert 'sum' not in stats['columns']['quantity']
    assert 'mean' not in stats['columns']['quantity']
    assert 'sum' not in stats['columns']['unit']
    assert stats['columns']['quantity']['aggregationBlocked'] == 'mixed_or_unknown_units'
    assert '计量单位' in analysis and '合计' in analysis
    assert [c['chartType'] for c in charts] == ['table']
    enhanced = enhanced_analysis(rows, COLUMNS, stats, truncated=False)
    assert not enhanced['metrics']


def test_homogeneous_unit_quantity_keeps_precise_statistics():
    stats, _, _, _ = describe([{'item': 'A', 'quantity': '0.1', 'unit': 12},
                               {'item': 'B', 'quantity': '0.2', 'unit': 12}], COLUMNS, truncated=False)
    assert stats['columns']['quantity']['sum'] == '0.3'
    assert 'sum' not in stats['columns']['unit']
