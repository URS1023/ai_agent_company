"""本机已核验库存的有限接口和真实模型验收；需要独立基准文件，禁止自动换库。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values

from live_baseline import STOP, _verify_local_service
from live_business_acceptance import Acceptance, normalized, office_text, seed_platform
from live_model_proxy import LiveModelProxy
from real_platform_smoke import HERE, ROOT, Host


RAW_FIELDS = {
    'stock_id': '库存记录标识', 'material_id': '物料标识', 'material_name': '备件名称',
    'material_code': '备件编码', 'stock_model': '库存规格型号', 'material_model': '物料规格型号',
    'quantity': '实际可用库存', 'stock_unit': '库存计量单位编码', 'material_unit': '物料计量单位编码',
    'safety_stock': '安全库存', 'warehouse_id': '仓库标识', 'warehouse_name': '仓库名称',
    'location_id': '仓位标识', 'location_sequence': '仓位序号', 'location_code': '仓位编码', 'batch': '批次号',
}


def check_rows(actual, expected):
    """逐行逐字段比对独立 SQL 基准，保留重复行和 NULL。"""
    assert len(actual) == len(expected)
    for row, reference in zip(actual, expected):
        for key, label in RAW_FIELDS.items():
            assert normalized(row[label]) == normalized(reference[key]), (key, label)
        assert row['库存预警'] == ('未知' if reference['safety_stock'] is None or reference['quantity'] is None else '待核对')


def collect_deliveries(acceptance, conversation, task, output, expected):
    """逐轮核验工件来源与真实内容，避免历史文件掩盖当前轮未完成。"""
    detail = acceptance.json('GET', f'/api/chat/conversations/{conversation}')
    destination_dir = output / task['label']
    destination_dir.mkdir(exist_ok=True)
    task['deliveries'] = []
    for item in detail.get('projections', {}).get('deliveries', []):
        if item.get('status') != 'ready' or item.get('source_run_id') != task['run_id']:
            continue
        data = acceptance.api('GET', item['download_url']).content
        destination = destination_dir / item['name']
        assert destination.parent == destination_dir and destination.suffix in {'.xlsx', '.pptx'}
        destination.write_bytes(data)
        finding = {'name': item['name'], 'type': item['type'], 'bytes': len(data), 'artifact_id': item['id']}
        if destination.suffix == '.xlsx':
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                sheet = next(s for s in book if set(RAW_FIELDS.values()) <= set(next(s.iter_rows(values_only=True))))
                values = list(sheet.iter_rows(values_only=True))
                check_rows([dict(zip(values[0], row)) for row in values[1:]], expected)
                finding['exact_rows'] = True
            finally:
                book.close()
        else:
            texts = office_text(data, '.pptx')
            finding['pages'] = len(texts)
            destination.with_suffix('.txt').write_text('\n'.join(texts), encoding='utf-8')
        task['deliveries'].append(finding)


def select_count(path):
    """按数据库审计统计真实业务 SELECT，不用模型的文字承诺判断是否重查。"""
    if not path.exists():
        return 0
    entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    return sum(row.get('event') == 'query_completed' and row.get('summary') == 'business_select' for row in entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--follow-up', action='store_true', help='同一任务续轮复用查询并生成独立第二份介绍PPT')
    parser.add_argument('--skip-api', action='store_true', help='接口已通过时仅重测被修改的模型交付路径')
    args = parser.parse_args()
    if args.follow_up and not args.models:
        parser.error('--follow-up 需要 --models')
    if args.seed:
        seed_platform(args.seed)
        return 0
    assert args.baseline and args.output
    assert not STOP.exists(), '已有数据库异常停止记录'
    owner = _verify_local_service(15)
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    assert baseline['status'] == 'passed' and baseline['owner']['machine'] == owner['machine']
    assert (baseline['owner']['host'], baseline['owner']['port'], baseline['owner']['database']) == ('127.0.0.1', 3306, 'szh_new')
    baseline['rows'] = baseline['golden_rows']
    assert 0 < len(baseline['rows']) <= 20
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = dotenv_values(ROOT / '.env.local')
    report = {'scope': '本机szh_new，临时SQLite平台，最多6次业务查询；独立基准不重复查询',
              'api_requests': [], 'tasks': [], 'assertions': [], 'owner': owner, 'status': 'running'}
    host, proxy = None, None
    started = time.monotonic()
    try:
        with LiveModelProxy.from_opencode(Path.home() / '.config/opencode/opencode.json', model='deepseek-v4.1-flash',
                max_requests=20, total_timeout=360, request_timeout=90) as proxy, tempfile.TemporaryDirectory(prefix='test5-inventory-') as temporary, ExitStack() as cleanup:
            directory = Path(temporary)
            host = Host(directory, urlsplit(proxy.base_url).port, env_overrides={
                'DSH_TEST_ISOLATION': '0', 'LIVE_DSH_ACCEPTANCE': '1', 'PYTHONPATH': str(HERE / 'live_python_guard'),
                'LIVE_ALLOWED_TABLES': json.dumps([t['tableName'] for t in baseline['metadata']]),
                'LIVE_DB_AUDIT_PATH': str(output / 'database.jsonl'), 'LIVE_DB_STOP_PATH': str(STOP),
                'MYSQL_HOST': '127.0.0.1', 'MYSQL_PORT': '3306', 'MYSQL_USER': settings.get('MYSQL_USER') or 'root',
                'MYSQL_PASSWORD': settings['MYSQL_PASSWORD'], 'AGNES_BASE_URL': proxy.base_url,
                'AGNES_CHAT_MODEL': proxy.model, 'VISION_ENABLED': '0', 'EMBEDDING_ENABLED': '0'})
            cleanup.callback(host.close)
            host.ready()
            host.login()
            identity = host.json('GET', '/api/auth/me')
            seed = directory / 'seed.json'
            seed.write_text(json.dumps({'workspace_id': host.http.headers['X-Workspace-Id'], 'user_id': identity['user']['id'],
                                        'metadata': baseline['metadata']}, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--seed', str(seed)],
                env=host.env, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
            assert result.returncode == 0, result.stderr[-1000:]
            source_id = json.loads(result.stdout.strip().splitlines()[-1])['source_id']
            acceptance = Acceptance(host, output, baseline, report)
            if not args.skip_api:
                query = acceptance.json('POST', '/api/v1/analytics/inventory/query', json={'datasourceId': source_id, 'pageSize': 20})['data']
                check_rows(query['data'], baseline['rows'])
                assert query['statistics']['quantityTotal'] is None
                for row in query['data']:
                    for table, raw, label in [('t_spinventory', '库存计量单位编码', '库存计量单位'), ('t_sparepartsinfor', '物料计量单位编码', '物料计量单位')]:
                        metadata = next(t for t in baseline['metadata'] if t['tableName'] == table)
                        column = next(c for c in metadata['columns'] if c['columnName'] == 'c_unit')
                        options = {str(v['value']): v['label'] for v in column['enumValues']}
                        assert row[label] == options.get(str(row[raw]))
                restored = acceptance.json('GET', f"/api/v1/analytics/history/{query['queryId']}")['data']
                check_rows(restored['data'], baseline['rows'])
                report['assertions'].append({'name': '库存API/历史与独立SQL的64个原值一致，字典单位一致，空阈值未知，无跨单位合计', 'passed': True})
                (output / 'query.json').write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding='utf-8')
                for kind, label in [('warehouses', '仓库名称'), ('materials', '备件名称')]:
                    data = acceptance.json('POST', '/api/v1/analytics/inventory/query', json={'datasourceId': source_id, 'kind': kind, 'pageSize': 20})['data']
                    assert all(label in row for row in data['data'])
                    report['assertions'].append({'name': kind + '独立业务接口返回真实名称', 'rows': len(data['data']), 'passed': True})
            print(json.dumps({'phase': 'business_api', 'status': 'passed', 'requests': report['api_requests']}, ensure_ascii=False), flush=True)
            if args.models:
                acceptance.json('POST', '/api/models/profiles', status=201, json={'profile_name': 'live-acceptance', 'provider': 'openai-compatible',
                    'model': proxy.model, 'endpoint': proxy.base_url, 'api_key': 'local-acceptance-proxy', 'capabilities': ['chat', 'tools'],
                    'runtime_options': {'protocol': 'openai-completions', 'thinking': 'disabled', 'context_window': 65536, 'max_output_tokens': 4096}})
                conversation = acceptance.json('POST', '/api/chat/conversations', status=201, json={'title': '库存多工件真实验收'})['id']
                task = acceptance.run(conversation, '帮我查看当前仓库的备品备件库存，生成一份名为备品备件库存表的Excel和一份库存数据PPT，再做一份ERP系统介绍PPT。'
                    '使用当前选择的数据源，数据文件要带备件名称、仓库名称、计量单位、批次和安全库存，保留空值，给出需要核对的问题。ERP介绍为通用产品介绍，六到八页即可。',
                    'inventory-deliveries', datasource_ids=[source_id], skill_ids=['business-analysis', 'document-delivery'],
                    required_deliveries=[{'type': 'spreadsheet', 'count': 1}, {'type': 'presentation', 'count': 2}])
                collect_deliveries(acceptance, conversation, task, output, baseline['rows'])
                tool_names = [tool['name'] for tool in task['tool_calls']]
                report['assertions'].append({'name': '真实模型自主选择固定库存查询并复用queryId生成Excel/PPT',
                    'passed': 'query_inventory_data' in tool_names and 'export_query_spreadsheet' in tool_names
                    and 'export_query_presentation' in tool_names and 'query_business_data' not in tool_names})
                report['assertions'].append({'name': '三个真实工件交付且Excel所有原值一致', 'passed': len(task['deliveries']) == 3
                    and sum(x['type'] == 'presentation' for x in task['deliveries']) == 2 and any(x.get('exact_rows') for x in task['deliveries'])})
                report['assertions'].append({'name': 'ERP介绍满足六到八个物理页且包含封面结尾', 'passed': any(
                    'erp' in x['name'].lower() and 6 <= x.get('pages', 0) <= 8 for x in task['deliveries'])})
                if args.follow_up:
                    assert task['status'] == 'completed' and task.get('task_id'), '首轮未完成，不继续运行模型任务'
                    before_selects = select_count(output / 'database.jsonl')
                    revised = acceptance.run(conversation,
                        '继续同一个任务，把刚才的库存查询结果另存为库存复核.xlsx，并生成库存复核数据PPT。'
                        '请复用已经完成的queryId，不要重新读取数据库，数据原值和空值保留。'
                        '此前的ERP介绍保留，再单独创建一份《MES系统介绍》PPT，六到八页，通用产品介绍。',
                        'inventory-follow-up', task_id=task['task_id'], datasource_ids=[source_id],
                        skill_ids=['business-analysis', 'document-delivery'],
                        required_deliveries=[{'type': 'spreadsheet', 'count': 1}, {'type': 'presentation', 'count': 2}])
                    collect_deliveries(acceptance, conversation, revised, output, baseline['rows'])
                    after_selects = select_count(output / 'database.jsonl')
                    names = [tool['name'] for tool in revised['tool_calls']]
                    report['assertions'].append({'name': '跨轮保持任务且复用查询，真实数据库新增查询为零',
                        'selects_before': before_selects, 'selects_after': after_selects,
                        'passed': revised.get('task_id') == task['task_id'] and before_selects > 0
                        and after_selects == before_selects and not {'query_inventory_data', 'query_business_data'} & set(names)})
                    report['assertions'].append({'name': '续轮生成三个新的工件且库存Excel原值一致',
                        'passed': len(revised['deliveries']) == 3 and any(x.get('exact_rows') for x in revised['deliveries'])
                        and sum(x['type'] == 'presentation' for x in revised['deliveries']) == 2})
                    report['assertions'].append({'name': '同任务第二份独立介绍PPT为MES且满足六到八页',
                        'passed': any('mes' in x['name'].lower() and 6 <= x.get('pages', 0) <= 8 for x in revised['deliveries'])})
            report['status'] = 'measured'
    except Exception as error:
        report.update(status='failed', error={'type': type(error).__name__, 'message': str(error)[:1200]})
        if host:
            report['diagnostics'] = list(host.diagnostics)[-8:]
    finally:
        if proxy:
            report['models'], report['model_requests'] = proxy.summary(), proxy.records
        audit = output / 'database.jsonl'
        if audit.exists():
            report['database_audit'] = [json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines()]
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['checks_passed'] = report['status'] == 'measured' and all(x['passed'] for x in report['assertions']) and all(t['status'] == 'completed' for t in report['tasks'])
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({key: report[key] for key in ['status', 'checks_passed', 'assertions', 'elapsed_seconds']} | {'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
