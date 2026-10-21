"""使用正式API串行演示本机取数、设备检验、维护分析和食品三维质检。

每个阶段只派发一次；收据已存在即拒绝重放。所有SQL仍经过平台授权和EXPLAIN，
不调用模型，不修改MySQL原始记录。新建任务与报告保留在系统中供用户操作。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from tools.demo_data.platform import DATA, PlatformAPI, analysis_query, import_contract, validate_completed_import
from tools.demo_data.quality import fixture_rows, inspection_import, query_input, template_input


def inspect(api, *, label, target, template, query, mappings, sample_field, samples):
    """只用数据库查询证据计算，不把脚本中的预期结果写成检测结果。"""
    task = api.call('POST', '/inspections', {'name': label, 'target_type': template['target_type'],
        'template_id': template['id'], 'template_revision': template['revision'],
        'expected_samples': samples, **target}, label=label + ':task')
    imported = api.call('POST', f"/inspections/{task['id']}/imports", {
        'revision': task['revision'], 'query_id': query['id'], 'sample_field': sample_field,
        'mappings': mappings}, label=label + ':import')
    task = api.call('POST', f"/inspections/{task['id']}/runs", {'revision': imported['revision']}, label=label + ':result')
    api.call('POST', '/inspection-report-generations', {'inspection_id': task['id'],
        'run_id': task['result']['run_id'], 'inspection_revision': task['revision'],
        'name': label + '报告'}, label=label + ':report')
    return task


def device_demo(api, registered):
    """一台食品设备，六条点检证据和两日工况比较，共三次源读取。"""
    source = registered['operations']['source:szh_new_food']
    device = registered['operations']['device:SP-JZ-001']
    template = registered['operations']['device-template']
    query = api.call('POST', '/queries', {'name': '食品均质机01—设备检验数据',
        'datasource_id': source['id'], 'max_rows': 10,
        'sql': '''SELECT CONCAT(`设备编号`, '-', `采集时间`) AS sample_id,
            `轴承温度_C` AS temperature, `驱动端振动速度_mm_s` AS vibration
            FROM aic_demo_food_telemetry WHERE `设备编号`=:device
            AND `采集时间`>=:start AND `采集时间`<:end AND `运行状态`='运行'
            ORDER BY `采集时间` LIMIT 6''',
        'parameters': {'device': 'SP-JZ-001', 'start': '2026-10-18T00:00:00+08:00',
                       'end': '2026-10-19T00:00:00+08:00'}}, label='device-query')
    count = query['row_count']
    if count != 6:
        raise RuntimeError('device_demo_rows_mismatch')
    result = inspect(api, label='食品均质机01运行检验', target={'device_id': device['id']},
        template=template, query=query, sample_field='sample_id', samples=count, mappings=[
            {'metric_id': 'bearing_temperature', 'value_field': 'temperature', 'unit': '℃'},
            {'metric_id': 'vibration', 'value_field': 'vibration', 'unit': 'mm/s'}])
    maintenance = api.call('POST', '/maintenance-analyses', {
        'name': '食品均质机01同工况温度分析', 'device_id': device['id'], 'datasource_id': source['id'],
        **analysis_query('aic_demo_food_telemetry', 'SP-JZ-001')}, label='maintenance-result')
    api.state['summary'] = {'device_inspection': result['outcome'],
        'maintenance': maintenance['result']['status'], 'maintenance_samples': maintenance['result']['sample_count'],
        'business_source_reads': 3, 'max_source_read_seconds': 2}


def registered_imports(registered):
    """仅消费成功登记时冻结的来源合同，旧收据不猜测补全或重放登记。"""
    if not isinstance(registered, dict) or registered.get('status') != 'passed':
        raise RuntimeError('platform_registration_incomplete')
    summary = registered.get('summary')
    imports = summary.get('imports') if isinstance(summary, dict) else None
    if not isinstance(imports, dict) or 'devices' not in imports:
        raise RuntimeError('platform_import_contract_missing')
    return {key: import_contract(value) for key, value in imports.items() if key in ('devices', 'quality')}


def verified_quality_asset(registered, quality_receipt):
    """产品收据须对应登记时授权的食品资产，在任何平台写入前验证。"""
    contract = registered_imports(registered).get('quality')
    if contract is None:
        raise RuntimeError('platform_quality_contract_missing')
    validate_completed_import(contract, quality_receipt)
    if len(contract['assets']) != 1:
        raise RuntimeError('quality_import_not_completed')
    asset = contract['assets'][0]
    if (asset['database'] != 'szh_new_food' or asset['table'] != 'aic_demo_product_quality'
            or asset['row_count'] != 16):
        raise RuntimeError('quality_sample_rows_mismatch')
    return asset


def product_demo(api, registered, quality_receipt):
    """两个数据库读取均经过平台，只比较实际检测结果与固定样本预期。"""
    asset = verified_quality_asset(registered, quality_receipt)
    source = registered['operations']['source:szh_new_food']
    first = fixture_rows()[0]
    product = api.call('POST', '/products', {'name': first['product_name'], 'code': first['product_code'],
        'specification': '100 g 演示包装', 'description': '合成演示样本'}, label='product')
    batch = api.call('POST', '/product-batches', {'name': first['batch_no'], 'product_id': product['id'],
        'external_ref': first['batch_no'], 'quantity': 16}, label='product-batch')
    template = api.call('POST', '/inspection-templates', template_input(), label='product-template')
    query = api.call('POST', '/queries', query_input(source['id'], asset['import_id']), label='product-query')
    if query['row_count'] != 16:
        raise RuntimeError('product_demo_rows_mismatch')
    mapping = inspection_import(query['id'], 1)
    task = inspect(api, label='食品产品三维质检演示', target={'product_id': product['id'], 'batch_id': batch['id']},
        template=template, query=query, sample_field=mapping['sample_field'], samples=16, mappings=mapping['mappings'])
    outcomes = dict(Counter(sample['outcome'] for sample in task['result']['samples']))
    if outcomes != {'passed': 4, 'failed': 6, 'review': 1, 'incomplete': 5} or task['outcome'] != 'failed':
        raise RuntimeError('product_demo_outcomes_mismatch')
    api.state['summary'] = {'product_inspection': task['outcome'], 'business_source_reads': 2,
        'sample_count': 16, 'sample_outcomes': outcomes, 'max_source_read_seconds': 2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['devices', 'products'])
    parser.add_argument('--quality-receipt', type=Path)
    args = parser.parse_args()
    registered = json.loads((DATA / 'platform-register.json').read_text(encoding='utf-8'))
    registered_imports(registered)
    quality_receipt = None
    if args.stage == 'products':
        if not args.quality_receipt:
            parser.error('产品演示须提供已完成的导入收据')
        quality_receipt = json.loads(args.quality_receipt.read_text(encoding='utf-8'))
        verified_quality_asset(registered, quality_receipt)
    api = PlatformAPI('verify-' + args.stage, max_requests=20, seconds=90)
    try:
        api.login()
        if args.stage == 'devices':
            device_demo(api, registered)
        else:
            product_demo(api, registered, quality_receipt)
    except BaseException as error:
        api.finish(error)
        raise
    api.finish()
    print(json.dumps({'status': api.state['status'], 'requests': api.state['requests'], 'summary': api.state['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
