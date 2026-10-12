"""Standalone local demo workflow: bounded read -> rule analysis -> saved business report."""
import inspect
from .analysis import analyze_operating_rows

BATCH = 'demo_20260922_v1'
TABLES = ['demo_device_assets', 'demo_device_telemetry']
SQL = '''SELECT a.device_id, a.device_name, a.temperature_limit_c, a.rpm_limit,
 t.observed_at, t.state, t.boot_at, t.uptime_seconds, t.rpm, t.temperature_c, t.is_demo
FROM demo_device_assets a JOIN demo_device_telemetry t ON t.device_id=a.device_id
WHERE a.is_demo=1 AND t.is_demo=1 AND t.sample_batch=:sample_batch
ORDER BY a.device_id,t.observed_at'''


def build_graph(source_id, version=1):
    code = inspect.getsource(analyze_operating_rows) + '\n\ndef main(rows):\n    return analyze_operating_rows(rows)\n'
    nodes = [
        {'id': 'start', 'kind': 'start', 'title': '开始 · 设备运行演示', 'config': {'variables': []}},
        {'id': 'read', 'kind': 'database-query', 'title': '读取演示设备与 24 小时采样', 'config': {
            'datasourceId': source_id, 'datasourceVersion': version, 'query': SQL,
            'parameters': {'sample_batch': BATCH}, 'maxRows': 200}},
        {'id': 'analyze', 'kind': 'code', 'title': '分析状态、开机时长、转速和温度',
         'inputs': {'rows': {'nodeId': 'read', 'path': '$.data'}},
         'config': {'code': code, 'timeoutSeconds': 15}},
        {'id': 'report', 'kind': 'business-report', 'title': '保存设备运行分析 · 演示数据',
         'inputs': {'input': {'nodeId': 'analyze', 'path': '$.data.result.data'}},
         'config': {'kind': 'alert', 'title': '设备运行状态分析（演示数据、示例阈值）', 'aiAnalysis': False}},
        {'id': 'end', 'kind': 'end', 'title': '结束 · 查看逐项分析与采样趋势', 'config': {}},
    ]
    for i,node in enumerate(nodes):
        node['position']={'x': i*360, 'y': 100}
        node.setdefault('inputs', {})
    return {'nodes': nodes, 'edges': [{'id': left['id']+'_'+right['id'], 'source': left['id'],
        'target': right['id'], 'port': 'out', 'sourceSide':'right','targetSide':'left'} for left,right in zip(nodes,nodes[1:])]}
