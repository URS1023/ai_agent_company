"""隔离核对 DSH 巡检校验入口与导出精度/范围，不运行真实模型。"""
from __future__ import annotations

import io
import sqlite3

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.models import Run
from app.modules.analytics.models import SourceMetadata
from test_analytics import API, run_sql, source
from test_data_profile import profile_fixture
from test_dsh_query_execution import dsh_fixture, execute


@pytest.fixture
def inspection_fixture(dsh_fixture, db):
    """向独立业务夹具加入两张最小真实巡检表，并同步已知的字段快照。"""
    with sqlite3.connect(dsh_fixture.path) as connection:
        connection.execute('CREATE TABLE t_devicemanagement (devicemanagementId INTEGER, isDeleted INTEGER)')
        connection.executemany('INSERT INTO t_devicemanagement VALUES (?, ?)', [(1, 0), (2, 1), (3, None)])
        connection.execute('CREATE TABLE t_deviceinspectionmaintenance (deviceinspectionmaintenanceId INTEGER, '
                           'c_deviceId INTEGER, c_inspectionStatus INTEGER, c_recordType INTEGER, isDeleted INTEGER)')
        connection.executemany('INSERT INTO t_deviceinspectionmaintenance VALUES (?, ?, ?, ?, ?)',
                               [(1, 1, 1, 1, 0), (2, 2, 0, 1, 0)])
    snapshot = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == dsh_fixture.source.id))
    tables = [
        {'tableName': 't_devicemanagement', 'columns': [{'columnName': name, 'dataType': 'INTEGER'}
            for name in ['devicemanagementId', 'isDeleted']]},
        {'tableName': 't_deviceinspectionmaintenance', 'columns': [{'columnName': name, 'dataType': 'INTEGER'}
            for name in ['deviceinspectionmaintenanceId', 'c_deviceId', 'c_inspectionStatus', 'c_recordType', 'isDeleted']]},
    ]
    snapshot.tables_json = [*snapshot.tables_json, *tables]
    dsh_fixture.source.connection_config = {**dsh_fixture.source.connection_config,
        'allowedTables': [*dsh_fixture.source.connection_config['allowedTables'], *(item['tableName'] for item in tables)]}
    db.commit()
    return dsh_fixture


@pytest.mark.parametrize('user_request,sql', [
    ('统计有效设备', 'SELECT COUNT(*) AS device_count FROM t_devicemanagement'),
    ('统计已巡检设备', 'SELECT COUNT(DISTINCT c_deviceId) AS 已巡检设备 FROM t_deviceinspectionmaintenance WHERE c_recordType IS NOT NULL'),
    ('统计已巡检设备', "SELECT COUNT(DISTINCT COALESCE(c_deviceId,'')) AS 已巡检设备 FROM t_deviceinspectionmaintenance WHERE c_inspectionStatus=1"),
])
def test_dsh_rejects_known_inspection_semantic_errors_before_execution(inspection_fixture, db, user_request, sql):
    run = db.get(Run, inspection_fixture.context['run_id'])
    run.context_snapshot = {**run.context_snapshot, 'user_request': user_request}
    db.commit()
    result = execute(inspection_fixture, query=sql, task='按原始要求查询设备数量')
    assert result.status == 'failed', result.data
    assert result.error_code == 'validation_error'
    assert result.metadata['business_data_queried'] is False
    assert inspection_fixture.calls == []


def test_dsh_valid_inspection_query_and_explicit_sql_remain_usable(inspection_fixture, db):
    run = db.get(Run, inspection_fixture.context['run_id'])
    run.context_snapshot = {**run.context_snapshot, 'user_request': '统计有效设备'}
    db.commit()
    result = execute(inspection_fixture, query='SELECT COUNT(*) AS device_count FROM t_devicemanagement WHERE isDeleted=0')
    assert result.status == 'success' and result.data['data'] == [{'device_count': 1}]
    sql = 'SELECT COUNT(*) AS device_count FROM t_devicemanagement'
    run.context_snapshot = {**run.context_snapshot, 'user_request': sql}
    db.commit()
    explicit = execute(inspection_fixture, query=sql, task='')
    assert explicit.status == 'success' and explicit.data['data'] == [{'device_count': 3}]
    assert len(inspection_fixture.calls) == 2


def test_xlsx_preserves_bigint_and_small_integer_types(client, administrator, source):
    result = run_sql(client, administrator, source,
        'SELECT 1234567890123456789 AS device_id, 42 AS quantity FROM sales LIMIT 1').json()['data']
    response = client.post(API + '/export', headers=administrator, json={'queryId': result['queryId'], 'format': 'xlsx'})
    assert response.status_code == 200, response.text[:100]
    workbook = load_workbook(io.BytesIO(response.content))
    sheet = workbook['查询结果']
    assert sheet['A2'].value == '1234567890123456789' and sheet['A2'].data_type == 's'
    assert sheet['B2'].value == 42 and sheet['B2'].data_type == 'n'
    assert all(cell.font.name == 'SimSun' for page in workbook for row in page for cell in row if cell.value is not None)
    assert sheet['A1'].font.bold is True and sheet['A1'].font.color.rgb == '00FFFFFF'


@pytest.mark.parametrize('format', ['csv', 'html', 'markdown'])
def test_exports_show_partial_scope_when_analysis_is_disabled(client, administrator, source, format):
    result = run_sql(client, administrator, source, maxRows=2, needAnalysis=False).json()['data']
    assert result['truncated'] is True and result['analysis'] == ''
    response = client.post(API + '/export', headers=administrator, json={'queryId': result['queryId'], 'format': format})
    assert response.status_code == 200, response.text
    text = response.content.decode('utf-8-sig')
    assert '已截断' in text and '2 行' in text and result['queryId'] in text
    if format == 'html':
        assert "SimSun,'宋体',serif" in text
