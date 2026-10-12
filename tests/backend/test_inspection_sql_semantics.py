"""验证质检汇总 SQL 的语义校验与「只修一次」的纠正策略。

验证行为：汇总语句拒绝 null 占位与「非完成」标记；字符展示函数的语法问题只允许一次执行前修复；
被禁止的函数不会因为展示修复通道而被放行。
隔离方式：用 sqlglot 解析真实 SQL 文本 + SimpleNamespace 桩模型，不执行 SQL、不连库。
边界：非法函数始终被拒；修复只发生一次，重复修复不会持续放宽校验。
"""
import json
import pytest
from sqlglot import parse_one
from types import SimpleNamespace
from app.modules.analytics.inspection_semantics import validate_inspection_aggregation
from app.modules.analytics.sql_safety import SqlFieldValidationError
from app.providers import model
from test_analytics import source


def test_inspection_aggregate_rejects_null_placeholder_and_noncompletion_flag():
    invalid=["SELECT COUNT(DISTINCT COALESCE(m.c_deviceId,'')) AS 已巡检设备 FROM t_deviceinspectionmaintenance m",
             "SELECT COUNT(DISTINCT m.c_deviceId) AS 已巡检设备 FROM t_deviceinspectionmaintenance m WHERE m.c_recordType IS NOT NULL"]
    for sql in invalid:
        with pytest.raises(SqlFieldValidationError):
            validate_inspection_aggregation(SimpleNamespace(tree=parse_one(sql,read='mysql')),'近期已巡检设备')
    valid="SELECT COUNT(DISTINCT m.c_deviceId) AS 已巡检设备 FROM t_deviceinspectionmaintenance m WHERE m.c_inspectionStatus=1"
    validate_inspection_aggregation(SimpleNamespace(tree=parse_one(valid,read='mysql')),'已巡检设备')


def test_character_display_function_gets_one_preexecution_repair(client,administrator,source,monkeypatch):
    calls=[]
    def respond(*a,**kw):
        calls.append(True)
        sql="SELECT CHR(65) AS label FROM sales" if len(calls)==1 else "SELECT 'A' AS label FROM sales"
        return {'content':json.dumps({'sql':sql,'explanation':'显示标签'})}
    monkeypatch.setattr(model,'chat_for_run',respond)
    response=client.post('/api/v1/analytics/query',headers=administrator,json={'datasourceId':source['id'],'query':'显示记录标签'})
    assert response.status_code==200,response.text
    assert len(calls)==2 and response.json()['data']['modelUsage']['sqlRepairs']==1


def test_forbidden_function_does_not_gain_the_display_repair_path(client,administrator,source,monkeypatch):
    calls=[]
    def respond(*a,**kw):
        calls.append(True)
        return {'content':json.dumps({'sql':"SELECT LOAD_FILE('/etc/passwd') FROM sales"})}
    monkeypatch.setattr(model,'chat_for_run',respond)
    response=client.post('/api/v1/analytics/query',headers=administrator,json={'datasourceId':source['id'],'query':'显示记录标签'})
    assert response.status_code==422
    assert len(calls)==1
