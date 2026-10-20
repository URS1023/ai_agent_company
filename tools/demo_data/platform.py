"""把已导入的本机演示数据装配为平台资源；所有写入复用正式业务API。

账号只从本机加密交付文件读取。注册阶段不查询MySQL；检验阶段须另行显式执行。
每阶段独立收据，失败不重试，不把成功的创建请求重复派发。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from cryptography.fernet import Fernet
import httpx
import psutil

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'http://127.0.0.1:8080'
DATA = ROOT / '.local/mysql-demo'


def verify_platform():
    """按监听、完整命令、工作目录及平台数据目录核验当前Windows进程。"""
    listeners = {c.pid for c in psutil.net_connections(kind='tcp')
                 if c.status == 'LISTEN' and c.laddr.port == 8080}
    if len(listeners) != 1:
        raise RuntimeError('platform_listener_not_unique')
    process = psutil.Process(listeners.pop())
    command = process.cmdline()
    expected = [str(ROOT / '.venv/Scripts/python.exe'), '-m', 'uvicorn',
                'agent_platform.main:create_app', '--factory', '--host', '127.0.0.1', '--port', '8080']
    if command != expected or Path(process.cwd()).resolve() != ROOT:
        raise RuntimeError('platform_process_unverified')
    environment = process.environ()
    if environment.get('AGENT_DATABASE_URL') != 'sqlite:///' + (ROOT / '.local/platform/platform.sqlite3').as_posix():
        raise RuntimeError('platform_database_unverified')
    return process


class PlatformAPI:
    """单阶段固定本机目标、顺序请求与结果账本，不保存会话或密码。"""

    def __init__(self, stage: str, *, max_requests=120, seconds=300):
        self.process = verify_platform()
        self.born = self.process.create_time()
        self.receipt = DATA / f'platform-{stage}.json'
        self.state = {'status': 'running', 'stage': stage, 'requests': 0, 'operations': {},
                      'model_requests': 0, 'platform_pid': self.process.pid}
        DATA.mkdir(exist_ok=True)
        with self.receipt.open('x', encoding='utf-8') as stream:
            json.dump(self.state, stream)
        self.maximum, self.deadline = max_requests, time.monotonic() + seconds
        self.client = httpx.Client(base_url=ORIGIN, timeout=15, follow_redirects=False, trust_env=False)

    def save(self):
        self.receipt.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')

    def call(self, method, path, body=None, *, label=None):
        if self.state['requests'] >= self.maximum or time.monotonic() > self.deadline:
            raise RuntimeError('platform_stage_budget_exhausted')
        if not self.process.is_running() or self.process.create_time() != self.born:
            raise RuntimeError('platform_owner_changed')
        self.state['requests'] += 1
        self.state['current'] = {'method': method, 'path': path}
        self.save()
        response = self.client.request(method, '/api/v1' + path, json=body)
        if response.status_code >= 400:
            error = response.json().get('error', {})
            self.state['failure'] = {'status': response.status_code, 'code': error.get('code'),
                                     'message': error.get('message'), 'fields': error.get('fields')}
            self.save()
            raise RuntimeError('platform_request_failed')
        result = response.json()
        if label:
            self.state['operations'][label] = result
            self.save()
        return result

    def login(self):
        cipher = Fernet((ROOT / '.local/desktop-access.key').read_bytes())
        accounts = json.loads(cipher.decrypt((ROOT / '.local/desktop-access.enc').read_bytes()))
        account = next(v for v in accounts.values() if v['url'] == ORIGIN)
        me = self.call('POST', '/auth/login', {k: account[k] for k in ('username', 'password')})
        self.client.headers['x-csrf-token'] = me['csrf_token']
        self.state['workspace_id'] = me['workspace']['id']
        self.save()

    def finish(self, error=None):
        self.client.close()
        self.state.update(status='failed' if error else 'passed', finished_at=datetime.now(timezone.utc).isoformat())
        if error:
            self.state['error_type'] = type(error).__name__
        self.save()


def table_schema(plan, database):
    """只登记本次数据包新表，旧业务表和管理账本不自动授权给平台。"""
    tables = {}
    for asset in plan['assets']:
        if asset['database'] != database:
            continue
        tables[asset['table']] = {'tableName': asset['table'], 'columns': [
            {'columnName': c['name'], 'dataType': c['sql_type']} for c in asset['columns']] + [
            {'columnName': '__aic_import_id', 'dataType': 'CHAR(64)'},
            {'columnName': '__aic_source_row', 'dataType': 'BIGINT'},
            {'columnName': '__aic_source_kind', 'dataType': 'VARCHAR(20)'}]}
    return list(tables.values())


def analysis_query(table, device_code):
    """取两天相同生产状态的原始数据；保留日期、工况和阶段供业务判定复核。"""
    return {
        'sql': f'''SELECT `采集时间` AS timestamp, `轴承温度_C` AS value,
            `负载率_pct` AS load, `生产模式` AS mode,
            CASE WHEN `采集时间` < :recent_start THEN 'baseline' ELSE 'recent' END AS period
            FROM `{table}` WHERE `设备编号` = :device AND `运行状态` = '运行'
            AND ((`采集时间` >= :baseline_start AND `采集时间` < :baseline_end)
              OR (`采集时间` >= :recent_start AND `采集时间` < :recent_end))
            ORDER BY `采集时间`''',
        'parameters': {'device': device_code, 'baseline_start': '2026-05-01T00:00:00+08:00',
                       'baseline_end': '2026-05-02T00:00:00+08:00',
                       'recent_start': '2026-10-18T00:00:00+08:00', 'recent_end': '2026-10-19T00:00:00+08:00'},
        'value_field': 'value', 'time_field': 'timestamp', 'load_field': 'load',
        'period_field': 'period', 'condition_fields': ['mode'], 'bin_width': 10,
        'min_samples': 5, 'minimum_coverage': 0.8, 'change_threshold': 0.15,
    }


def register(api, plan, password, quality_plan=None):
    """登记两个源、九设备及映射；检验标准仅为演示配置，可在系统内修改。"""
    sources = {}
    catalog = {'assets': [*plan['assets'], *(quality_plan or {}).get('assets', [])]}
    for database in ('szh_new', 'szh_new_food'):
        secret = api.call('POST', '/secret-references', {'name': f'本机演示 {database}',
                          'kind': 'database_password', 'value': password})
        source = api.call('POST', '/datasources', {'name': '食品设备演示' if database.endswith('food') else '化工与纺织设备演示',
            'provider': 'mysql', 'description': '本项目合成演示数据；完整来源与批次记录保存在数据库中。',
            'max_rows': 10000, 'credential_ref': secret['id'], 'schema_snapshot': table_schema(catalog, database),
            'connection_config': {'host': '127.0.0.1', 'port': 3306, 'database': database, 'username': 'root', 'tls': False}},
            label=f'source:{database}')
        source = api.call('POST', f"/datasources/{source['id']}/target-approvals", {
            'revision': source['revision'], 'host': '127.0.0.1', 'port': 3306, 'database': database,
            'service_owner': '本机Windows MySQL80，原生mysqld；本地数据目录与PID、实例UUID已核验',
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat(),
            'scope': ['query', 'metadata'], 'note': '用户2026-10-21明确授权本机两个指定库的导入和演示。'}, label=f'source:{database}')
        sources[database] = source
    with (ROOT / '设备预测维护数据包/设备台账.csv').open(encoding='utf-8-sig', newline='') as stream:
        assets = list(csv.DictReader(stream))
    for asset in assets:
        code = asset['设备编号']
        database = 'szh_new_food' if code.startswith('SP-JZ') else 'szh_new'
        table = next(a['table'] for a in plan['assets'] if a.get('device_id') == code)
        device = api.call('POST', '/devices', {'name': asset['设备名称'], 'code': code,
            'device_type': asset['设备类别'], 'location': asset['行业'] + '演示产线',
            'description': '来源：项目设备预测维护数据包；数据性质：合成演示。'}, label=f'device:{code}')
        mapping = analysis_query(table, code)
        api.call('POST', '/measurement-points', {'name': '轴承温度', 'code': code + '-BT',
            'device_id': device['id'], 'unit': '℃', 'datasource_id': sources[database]['id'],
            'mapping': {'sql': mapping['sql'], 'parameters': mapping['parameters'], 'analysis': mapping}}, label=f'point:{code}')
    template = api.call('POST', '/inspection-templates', {'name': '设备运行检验（演示标准）',
        'code': 'AIC-DEMO-DEVICE', 'target_type': 'device', 'description': '演示阈值，正式使用时按设备手册与企业标准配置。',
        'metrics': [{'id': 'bearing_temperature', 'name': '轴承温度', 'category': 'numeric', 'unit': '℃', 'standard_max': '75'},
                    {'id': 'vibration', 'name': '驱动端振动速度', 'category': 'numeric', 'unit': 'mm/s', 'standard_max': '4.5'}]}, label='device-template')
    return {'sources': {k: v['id'] for k, v in sources.items()}, 'devices': len(assets), 'device_template': template['id']}


def completed_plan(plan_file, receipt_file):
    """只有逐来源完成并与计划绑定的导入收据才能转为演示数据源。"""
    plan = json.loads(plan_file.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
    if receipt.get('status') not in ('succeeded', 'passed', 'completed'):
        raise RuntimeError('data_import_not_completed')
    if receipt.get('plan_sha256') != plan['plan_sha256'] or any(
        item.get('status') not in ('succeeded', 'already_succeeded')
        or item.get('committed_rows') != item.get('expected_rows') for item in receipt.get('assets', [])):
        raise RuntimeError('data_import_receipt_mismatch')
    if len(receipt.get('assets', [])) != len(plan['assets']):
        raise RuntimeError('data_import_incomplete')
    return plan


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--import-receipt', type=Path, required=True)
    parser.add_argument('--quality-plan', type=Path)
    parser.add_argument('--quality-receipt', type=Path)
    parser.add_argument('--password-env', default='AIC_DEMO_MYSQL_PASSWORD')
    args = parser.parse_args()
    plan = completed_plan(args.plan, args.import_receipt)
    if bool(args.quality_plan) != bool(args.quality_receipt):
        parser.error('--quality-plan 与 --quality-receipt 须一起提供')
    quality_plan = completed_plan(args.quality_plan, args.quality_receipt) if args.quality_plan else None
    api = PlatformAPI('register')
    try:
        api.login()
        api.state['summary'] = register(api, plan, os.environ[args.password_env], quality_plan)
    except BaseException as error:
        api.finish(error)
        raise
    api.finish()
    print(json.dumps({'status': api.state['status'], 'requests': api.state['requests'], 'summary': api.state['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
