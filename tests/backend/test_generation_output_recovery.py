"""Repair model output, then execute only the validated SQL against SQLite fixtures.

中文说明：模型输出先修复、再只执行已校验的 SQL：空大屏设计能恢复并查到真实夹具行；
非法 SQL JSON 能恢复成可执行且已授权的查询。
隔离方式：monkeypatch + client 夹具（内存 SQLite），模型被替换为确定性响应。
边界：恢复只针对格式/语法，权限与范围校验不得放宽。
"""
import json
from sqlalchemy import select

from app.models import Run
from app.modules.reports import generation
from app.modules.reports.models import ReportGeneration
from app.providers import model
from test_analytics import API, source
from test_reports import REPORTS, fake_generated


def test_empty_dashboard_design_recovers_and_queries_real_fixture_rows(
    client, administrator, source, db, monkeypatch, approved_report_semantics,
):
    calls = []
    def respond(*args, **kwargs):
        calls.append(args)
        return {'content': '{}' if len(calls) == 1 else json.dumps(fake_generated(source['id']))}
    monkeypatch.setattr(model, 'chat_for_run', respond)
    read = generation.execute_query
    query_attempts = []
    def query(*args, **kwargs):
        query_attempts.append(len(calls))
        return read(*args, **kwargs)
    monkeypatch.setattr(generation, 'execute_query', query)
    response = client.post(REPORTS + '/screen/ai-generate', headers=administrator,
                           json={'theme': '部门金额', 'templateMode': 'none', 'datasourceId': source['id']})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert len(calls) == 2 and query_attempts and all(n == 2 for n in query_attempts)
    assert {r['department']: r['total'] for r in data['widgets'][0]['data']} == {'设备': 900, '财务': 200}
    assert db.get(Run, data['runId']).status == 'completed'
    assert db.scalar(select(ReportGeneration)).status == 'completed'


def test_invalid_sql_json_recovers_to_executable_authorized_query(client, administrator, source, monkeypatch):
    calls = []
    def respond(*args, **kwargs):
        calls.append(args)
        return {'content': '{}' if len(calls) == 1 else '{"sql":"SELECT COUNT(*) AS total FROM sales"}'}
    monkeypatch.setattr(model, 'chat_for_run', respond)
    response = client.post(API + '/query', headers=administrator,
                           json={'datasourceId': source['id'], 'query': '统计记录数'})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['success'] and result['data'] == [{'total': 5}]
    assert len(calls) == 2 and result['modelUsage']['sqlRepairs'] == 1
