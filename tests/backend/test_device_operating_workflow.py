"""验证设备运行类演示工作流的取数、判定与图结构（不依赖模型与数据库）。

验证行为：未知/停机/告警等状态不被折算成正常；演示数据拒绝未标注行与重复样本；
生成的流程图是有限、无模型调用且带显式结果路径的。
隔离方式：只加载 configurations/device_operations 下的分析函数与固定 fixtures，
不做 HTTP、不连库、不调用模型（脚本先手动插入仓库根到 sys.path）。
边界：重复样本、缺少标签的行必须报错而不是静默参与统计。
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from configurations.device_operations.analysis import analyze_operating_rows
from configurations.device_operations.fixtures import sample_rows
from configurations.device_operations.workflow import build_graph


def fixture_rows():
    assets,samples=sample_rows();lookup={a[0]:a for a in assets}
    return [{'device_id':s[0], 'device_name':lookup[s[0]][1], 'temperature_limit_c':lookup[s[0]][2],
        'rpm_limit':lookup[s[0]][3], **dict(zip(['observed_at','state','boot_at','uptime_seconds','rpm','temperature_c','is_demo','sample_batch'],s[1:]))} for s in samples]


def test_operating_metrics_preserve_unknown_stopped_and_warning_states():
    result=analyze_operating_rows(fixture_rows())
    assert result['deviceCount']==6 and result['sampleCount']==144
    assert result['statusCounts']=={'running':3,'fault':1,'stopped':1,'offline':1}
    assert len(result['data'])==24
    duration=next(row for row in result['data'] if row['检查项目']=='开机时长')
    assert duration['实测值']=='24 小时'
    assert '开机时间（北京时间）：2026-09-21 11:00:00' in duration['说明']
    assert result['data'][0]['实测值']=='运行'
    assert result['latest'][4]['rpm']==0
    assert result['latest'][5]['rpm'] is None and result['latest'][5]['temperatureC'] is None
    assert result['latest'][5]['uptimeHours'] is None
    assert sum(r['结果']=='预警' for r in result['data'])==3
    assert sum(r['结果']=='待确认' for r in result['data'])==4


def test_operating_demo_rejects_unlabelled_rows_and_duplicate_samples():
    import pytest
    rows=fixture_rows()
    with pytest.raises(ValueError, match='重复'): analyze_operating_rows(rows+[rows[0]])
    rows[0]['is_demo']=0
    with pytest.raises(ValueError, match='演示'): analyze_operating_rows(rows)


def test_workflow_is_finite_model_free_and_uses_explicit_result_path():
    from app.modules.workflows.schemas import FlowGraph
    from app.modules.workflows.graph import inspect_graph
    graph=FlowGraph.model_validate(build_graph('demo-source'))
    result=inspect_graph(graph)
    assert result['valid'], result
    assert not any(n.retry_config.retry_enabled for n in graph.nodes)
    assert not any(n.kind in {'llm','report-analysis','http-request','output-table'} for n in graph.nodes)
