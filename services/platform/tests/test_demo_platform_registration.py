"""演示装配只检查正式DTO与SQL授权；不调用平台或业务数据库。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.demo_data.platform import analysis_query, table_schema
from agent_platform.modules.data.schemas import SourceInput
from agent_platform.modules.data.sql_safety import authorize_sql
from agent_platform.modules.manufacturing.schemas import AnalysisInput


def test_analysis_uses_parameterized_device_and_bounded_two_periods():
    query = analysis_query('aic_demo_food_telemetry', 'SP-JZ-001')
    columns = ['采集时间', '轴承温度_C', '负载率_pct', '生产模式', '设备编号', '运行状态']
    safe = authorize_sql(query['sql'], provider='mysql', tables=[{'tableName': 'aic_demo_food_telemetry',
        'columns': [{'columnName': c} for c in columns]}], parameters=query['parameters'], max_rows=10000, database='szh_new_food')
    assert safe.tables == ('aic_demo_food_telemetry',)
    assert safe.parameters['device'] == 'SP-JZ-001'
    config = AnalysisInput(device_id='device', datasource_id='source', **query)
    assert config.condition_fields == ['mode']
    assert config.rows is None


def test_registration_schema_only_contains_explicit_imported_tables():
    plan = {'assets': [{'database': 'szh_new_food', 'table': 'aic_demo_food_telemetry',
                       'columns': [{'name': '设备编号', 'sql_type': 'VARCHAR(80)'}]},
                      {'database': 'szh_new', 'table': 'aic_demo_assets', 'columns': []}]}
    tables = table_schema(plan, 'szh_new_food')
    source = SourceInput(name='食品演示', provider='mysql', schema_snapshot=tables,
        connection_config={'host': '127.0.0.1', 'database': 'szh_new_food', 'username': 'root', 'tls': False})
    assert [t['tableName'] for t in source.schema_snapshot] == ['aic_demo_food_telemetry']
    assert len(tables[0]['columns']) == 4
