"""Bounded local MySQL access. Never accepts a remote destination or arbitrary SQL UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time

import pymysql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
TABLE_KEYS = {
    't_qualityinspectiontemplate': 'qualityInspectionTemplateId',
    't_qitemplatedetails': 'qITemplateDetailsId',
    't_incomingmaterialqcform': 'incomingMaterialQcFormId',
    't_qualityinspectionitemdetails': 'qualityInspectionItemDetailsId',
    't_incomingmaterialqcitemdetails': 'incomingMaterialQcItemDetailsId',
    't_incomingmaterialqcitemdetails2': 'incomingMaterialQcItemDetails2Id',
    't_zhijianxiangmutongjimingxi': 'zhijianxiangmutongjimingxiId',
    't_dinspectionmaintenanceplan': 'dInspectionMaintenancePlanId',
    't_inspectiondevice': 'inspectionDeviceId',
    't_deviceinspectionmaintenance': 'deviceInspectionMaintenanceId',
    't_deviceinspectiondetails': 'deviceInspectionDetailsId',
    't_equipmentmaintenancelog': 'equipmentMaintenanceLogId',
    't_shebeirunhuabaoyangmingxibiao': 'shebeirunhuabaoyangmingxibiaoId',
    't_xunjianjihuarunhuabaoyang': 'xunjianjihuarunhuabaoyangId',
}


class DataError(RuntimeError):
    pass


def identifier(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise DataError('字段名称无效')
    return '`' + value + '`'


def local_listener():
    command = "$c=Get-NetTCPConnection -State Listen -LocalPort 3306 -ErrorAction Stop; $ids=@($c.OwningProcess | Select-Object -Unique); if($ids.Count -ne 1){throw 'Ambiguous listener'}; Get-CimInstance Win32_Process -Filter ('ProcessId='+$ids[0]) | Select-Object ProcessId,Name | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True,
                            text=True, timeout=8, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    value = json.loads(result.stdout)
    if value['Name'].lower() != 'mysqld.exe':
        raise DataError('3306 端口不是本机 MySQL 服务，已停止连接')
    return value


def check_destination(host, port, database, expected='szh_new'):
    if host not in {'localhost', '127.0.0.1', '::1'} or int(port or 3306) != 3306 or database != expected:
        raise DataError('本工具只允许连接本机 3306 的 ' + expected)


def check_identity(row, database):
    if (str(row['host']).lower() != os.environ.get('COMPUTERNAME', '').lower()
            or int(row['port']) != 3306 or row['db'] != database
            or not Path(row['datadir']).is_dir()):
        raise DataError('数据库服务归属核对失败，已停止连接')


def load_connection_and_flows():
    from app.config import settings
    from app.models import DataSource
    from app.modules.analytics.connections import connection_password
    from app.modules.workflows.models import DeviceWorkflowBinding, Workflow, WorkflowRevision

    local_listener()
    url = make_url(settings.DATABASE_URL)
    check_destination(url.host, url.port, url.database, 'company_agent')
    engine = create_engine(url, connect_args={'connect_timeout': 3, 'read_timeout': 5, 'write_timeout': 5,
                                            'init_command': 'SET SESSION MAX_EXECUTION_TIME=3000'}, hide_parameters=True)
    try:
        with Session(engine) as db:
            identity = db.execute(text('SELECT @@hostname host,@@port port,@@datadir datadir,DATABASE() db')).mappings().one()
            check_identity(identity, 'company_agent')
            config = json.loads((ROOT / 'configurations/quality_catalog.json').read_text('utf-8'))
            source = db.get(DataSource, config['datasourceId'])
            if not source or source.status != 'active' or source.provider != 'mysql':
                raise DataError('本地工作流数据源未启用')
            cfg = source.connection_config
            check_destination(cfg.get('host'), cfg.get('port'), cfg.get('databaseName'))
            ids = {entry['workflowId'] for entry in config['entries']}
            ids.update(db.scalars(select(DeviceWorkflowBinding.workflow_id).where(
                DeviceWorkflowBinding.workspace_id == source.workspace_id,
                DeviceWorkflowBinding.domain.in_(['alert', 'alert_maintenance', 'alert_repair']))).all())
            rows = db.execute(select(Workflow.id, Workflow.name, Workflow.published_revision, WorkflowRevision.snapshot).join(
                WorkflowRevision, (WorkflowRevision.workflow_id == Workflow.id) &
                (WorkflowRevision.revision == Workflow.published_revision)).where(
                Workflow.id.in_(ids), Workflow.workspace_id == source.workspace_id, Workflow.status == 'published')).all()
            flows = []
            for key, name, revision, snapshot in rows:
                nodes = snapshot.get('graph', {}).get('nodes', [])
                sources = {n.get('config', {}).get('datasourceId') for n in nodes if n.get('config', {}).get('datasourceId')}
                if sources != {source.id}:
                    continue
                forms = next((n for n in nodes if n['id'] == 'forms'), None)
                device = next((n for n in nodes if n['id'] == 'devices'), None)
                monitor = next((n for n in nodes if n.get('config', {}).get('toolName') == 'collect_device_monitoring_snapshot'), None)
                if forms:
                    flows.append({'id': key, 'name': name, 'revision': revision, 'kind': 'quality',
                                  'parameters': forms['config']['parameters'], 'graph': snapshot['graph']})
                elif device and monitor:
                    flows.append({'id': key, 'name': name, 'revision': revision,
                                  'kind': monitor['config']['arguments']['category'],
                                  'filters': device['config']['filters'], 'graph': snapshot['graph']})
            credentials = {'host': '127.0.0.1', 'port': 3306, 'database': 'szh_new',
                           'user': cfg.get('username') or cfg.get('user'), 'password': connection_password(db, source)}
            return credentials, sorted(flows, key=lambda f: (f['kind'], f['name']))
    finally:
        engine.dispose()


class LocalDatabase:
    def __init__(self, credentials, *, budget=180):
        check_destination(credentials['host'], credentials['port'], credentials['database'])
        self.credentials, self.budget = credentials, budget
        self.connection = None
        self.calls = self.explains = 0
        self.columns = {}
        self.prototypes = {}

    def __enter__(self):
        local_listener()
        self.started = time.monotonic()
        self.connection = pymysql.connect(**self.credentials, charset='utf8mb4', autocommit=False,
            connect_timeout=3, read_timeout=5, write_timeout=5, local_infile=False,
            cursorclass=pymysql.cursors.DictCursor)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute('SET SESSION MAX_EXECUTION_TIME=3000')
                cursor.execute('SET SESSION innodb_lock_wait_timeout=3')
                cursor.execute('SELECT @@hostname host,@@port port,@@datadir datadir,DATABASE() db')
                check_identity(cursor.fetchone(), 'szh_new')
            return self
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *args):
        try:
            self.connection.rollback()
        finally:
            self.connection.close()

    def query(self, sql, params=(), *, explain=True, limit=1000):
        self.calls += 1
        if self.calls > self.budget or time.monotonic() - self.started > 60:
            raise DataError('本批数据操作已达到数量或时间上限，请缩小生成范围')
        with self.connection.cursor() as cursor:
            if explain and sql.lstrip().upper().startswith('SELECT'):
                cursor.execute('EXPLAIN ' + sql, params)
                plan = cursor.fetchall()
                self.explains += 1
                if sum(int(row.get('rows') or 0) for row in plan) > 100000:
                    raise DataError('读取范围过大，请缩小选择范围')
            cursor.execute(sql, params)
            rows = cursor.fetchmany(limit + 1) if cursor.description else []
            if len(rows) > limit:
                raise DataError('返回数据超出单批范围，已停止')
            return rows

    def schema(self, table):
        if table not in TABLE_KEYS:
            raise DataError('不支持写入该数据表')
        if table not in self.columns:
            statuses = self.query('SHOW TABLE STATUS LIKE %s', (table,), explain=False)
            if len(statuses) != 1 or statuses[0]['Name'] != table or statuses[0]['Engine'] != 'InnoDB':
                raise DataError('目标表不支持事务回滚，已停止')
            self.columns[table] = {r['Field']: r for r in self.query('SHOW COLUMNS FROM ' + identifier(table), explain=False)}
        return self.columns[table]

    def required_defaults(self, table):
        schema = self.schema(table)
        fields = [name for name, column in schema.items() if column['Null'] == 'NO' and column['Default'] is None
                  and 'auto_increment' not in column['Extra'] and name not in {TABLE_KEYS[table], 'createdOn'}]
        if table not in self.prototypes:
            rows = self.query('SELECT ' + ','.join(map(identifier, fields)) + ' FROM ' + identifier(table)
                              + ' ORDER BY autoId LIMIT 1') if fields else []
            self.prototypes[table] = rows[0] if rows else {}
        return dict(self.prototypes[table])

    def insert(self, table, values):
        schema = self.schema(table)
        if not set(values) <= schema.keys() or not str(values.get(TABLE_KEYS[table], '')).startswith('GUISIM1-'):
            raise DataError('只允许写入本工具生成的测试记录')
        names = list(values)
        self.query('INSERT INTO ' + identifier(table) + ' (' + ','.join(map(identifier, names)) + ') VALUES ('
                   + ','.join(['%s'] * len(names)) + ')', tuple(values[n] for n in names), explain=False)
