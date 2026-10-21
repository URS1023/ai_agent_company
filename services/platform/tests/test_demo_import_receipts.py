"""用临时JSON和内存API检查导入收据绑定；不访问平台、MySQL或模型。"""
from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data import platform, verify_business
from tools.demo_data.package import Asset, Column, Plan, digest_json


def public_plan(*, quality_only=False):
    """采用正式计划对象产生摘要，夹具不读设备大包或任何真实收据。"""
    column = (Column('sample_id', 'VARCHAR(80)', 'string'),)
    assets = (
        Asset('设备预测维护数据包/食品.parquet', 'a' * 64, 30, 'parquet',
              'szh_new_food', 'aic_demo_food_telemetry', 1000, column, 'SP-JZ-001'),
        Asset('fixtures/食品产品质检.csv', 'b' * 64, 40, 'csv',
              'szh_new_food', 'aic_demo_product_quality', 16, column),
    )
    return Plan(Path('unused-local-fixture'), assets[1:] if quality_only else assets).public()


def success_receipt(plan):
    return {
        'kind': 'ai_agent_company-demo-import-receipt-v1', 'status': 'succeeded',
        'plan_sha256': plan['plan_sha256'], 'assets': [
            {**{key: asset[key] for key in ('import_id', 'source', 'source_sha256', 'database', 'table')},
             'status': 'succeeded', 'expected_rows': asset['row_count'],
             'committed_rows': asset['row_count']}
            for asset in plan['assets']
        ],
    }


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def registered(plan=None, quality=None):
    return {'status': 'passed', 'summary': {'imports': {
        'devices': platform.import_contract(plan or public_plan()),
        'quality': platform.import_contract(quality or public_plan(quality_only=True)),
    }}}


@pytest.mark.parametrize('recovery', [False, True])
def test_completed_plan_accepts_quality_and_full_recovery_receipt(tmp_path, recovery):
    plan = public_plan(quality_only=not recovery)
    receipt = success_receipt(plan)
    if recovery:
        receipt.update(recovery_budget=1800, recovery={'original_receipt_sha256': 'c' * 64},
                       rows_committed_this_run=16)
        receipt['assets'][0]['status'] = 'already_succeeded'
        receipt['assets'].reverse()
    path = write_json(tmp_path / 'plan.json', plan)
    proof = write_json(tmp_path / 'receipt.json', receipt)
    assert platform.completed_plan(path, proof) == plan
    assert platform.validate_completed_import(plan, receipt) == platform.import_contract(plan)


@pytest.mark.parametrize(('field', 'value'), [
    ('import_id', 'foreign-import'), ('source', 'another/source.csv'),
    ('source_sha256', 'd' * 64), ('database', 'szh_new'), ('table', 'foreign_table'),
    ('expected_rows', 0), ('committed_rows', 0), ('expected_rows', '1000'),
    ('committed_rows', 1000.0), ('status', 'running'),
])
def test_receipt_asset_cannot_replace_a_planned_source(field, value):
    plan = public_plan()
    receipt = success_receipt(plan)
    receipt['assets'][0][field] = value
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        platform.validate_completed_import(plan, receipt)


def test_self_consistent_zero_rows_and_duplicate_entries_do_not_count_as_completed():
    plan = public_plan()
    receipt = success_receipt(plan)
    receipt['assets'][0].update(expected_rows=0, committed_rows=0)
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        platform.validate_completed_import(plan, receipt)
    receipt = success_receipt(plan)
    receipt['assets'][1] = deepcopy(receipt['assets'][0])
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        platform.validate_completed_import(plan, receipt)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'hash', 'failed', 'malformed'])
def test_receipt_requires_the_complete_exact_asset_set(mutation):
    plan = public_plan()
    receipt = success_receipt(plan)
    if mutation == 'missing':
        receipt['assets'].pop()
    elif mutation == 'extra':
        receipt['assets'].append(deepcopy(receipt['assets'][0]))
    elif mutation == 'hash':
        receipt['plan_sha256'] = 'another-plan'
    elif mutation == 'failed':
        receipt['status'] = 'failed'
    else:
        receipt['assets'] = {}
    with pytest.raises(RuntimeError, match='data_import_'):
        platform.validate_completed_import(plan, receipt)


def test_plan_duplicate_or_changed_body_is_rejected_without_trusting_its_hash_label(tmp_path):
    plan = public_plan()
    receipt = success_receipt(plan)
    plan['assets'][1] = deepcopy(plan['assets'][0])
    with pytest.raises(RuntimeError, match='data_import_plan_duplicate_asset'):
        platform.import_contract(plan)
    plan = public_plan()
    plan['assets'][0]['row_count'] += 1
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        platform.completed_plan(write_json(tmp_path / 'plan.json', plan),
                                write_json(tmp_path / 'receipt.json', receipt))


@pytest.mark.parametrize('bad_count', [True, -1, '16', 16.0])
def test_plan_row_counts_are_exact_nonnegative_integers(bad_count):
    plan = public_plan(quality_only=True)
    plan['assets'][0]['row_count'] = bad_count
    with pytest.raises(RuntimeError, match='data_import_plan_invalid'):
        platform.import_contract(plan)


def test_registration_records_only_the_verified_asset_contract(tmp_path, monkeypatch):
    device_path = tmp_path / '设备预测维护数据包/设备台账.csv'
    device_path.parent.mkdir()
    with device_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['设备编号', '设备名称', '设备类别', '行业'])
        writer.writeheader()
        writer.writerow({'设备编号': 'SP-JZ-001', '设备名称': '均质机01', '设备类别': '均质机', '行业': '食品'})
    monkeypatch.setattr(platform, 'ROOT', tmp_path)
    device = public_plan()
    device['assets'] = device['assets'][:1]
    device['plan_sha256'] = digest_json({key: value for key, value in device.items() if key != 'plan_sha256'})
    quality = public_plan(quality_only=True)

    class MemoryAPI:
        """仅接收DTO，不创建网络客户端。"""

        def __init__(self):
            self.calls = []

        def call(self, method, path, body, *, label=None):
            self.calls.append((method, path, body))
            return {'id': label or 'fixture-secret', 'revision': 1}

    api = MemoryAPI()
    summary = platform.register(api, device, 'in-memory-only', quality)
    assert summary['imports'] == {'devices': platform.import_contract(device),
                                  'quality': platform.import_contract(quality)}
    assert 'in-memory-only' not in json.dumps(summary)
    assert all('columns' not in item for contract in summary['imports'].values() for item in contract['assets'])
    food_source = next(body for _, path, body in api.calls
                       if path == '/datasources' and body['connection_config']['database'] == 'szh_new_food')
    assert {item['tableName'] for item in food_source['schema_snapshot']} == {
        'aic_demo_food_telemetry', 'aic_demo_product_quality'}


def test_product_receipt_is_bound_to_registered_quality_plan():
    quality = public_plan(quality_only=True)
    receipt = success_receipt(quality)
    result = verify_business.verified_quality_asset(registered(quality=quality), receipt)
    assert result['import_id'] == quality['assets'][0]['import_id']
    other = deepcopy(quality)
    other['assets'][0]['source_sha256'] = 'e' * 64
    other['assets'][0]['import_id'] = 'foreign-import'
    other['plan_sha256'] = 'foreign-plan'
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        verify_business.verified_quality_asset(registered(quality=quality), success_receipt(other))


@pytest.mark.parametrize('mutation', ['missing', 'wrong_database', 'wrong_table', 'wrong_count'])
def test_product_demo_rejects_invalid_binding_before_any_api_call(mutation):
    quality = public_plan(quality_only=True)
    if mutation != 'missing':
        key, value = {'wrong_database': ('database', 'szh_new'), 'wrong_table': ('table', 'other'),
                      'wrong_count': ('row_count', 15)}[mutation]
        quality['assets'][0][key] = value
    record = registered(quality=quality)
    if mutation == 'missing':
        del record['summary']['imports']['quality']
    class ForbiddenAPI:
        def call(self, *_args, **_kwargs):
            pytest.fail('无效绑定不得调用平台')
    with pytest.raises(RuntimeError, match='platform_quality_contract_missing|quality_sample_rows_mismatch'):
        verify_business.product_demo(ForbiddenAPI(), record, success_receipt(quality))


def test_business_cli_validates_receipt_before_constructing_api(tmp_path, monkeypatch):
    record = registered()
    quality = public_plan(quality_only=True)
    receipt = success_receipt(quality)
    receipt['assets'][0]['source_sha256'] = 'f' * 64
    write_json(tmp_path / 'platform-register.json', record)
    proof = write_json(tmp_path / 'quality.json', receipt)
    monkeypatch.setattr(verify_business, 'DATA', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['verify_business', 'products', '--quality-receipt', str(proof)])
    def forbidden_api(*_args, **_kwargs):
        pytest.fail('不应创建API或读取凭据')
    monkeypatch.setattr(verify_business, 'PlatformAPI', forbidden_api)
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        verify_business.main()


def test_register_cli_rejects_mismatched_receipt_before_constructing_api(tmp_path, monkeypatch):
    plan = public_plan()
    receipt = success_receipt(plan)
    receipt['assets'][1] = deepcopy(receipt['assets'][0])
    plan_file = write_json(tmp_path / 'plan.json', plan)
    proof = write_json(tmp_path / 'receipt.json', receipt)
    monkeypatch.setattr(sys, 'argv', ['platform', '--plan', str(plan_file), '--import-receipt', str(proof)])
    def forbidden_api(*_args, **_kwargs):
        pytest.fail('失败的资产收据不得开始登记')
    monkeypatch.setattr(platform, 'PlatformAPI', forbidden_api)
    with pytest.raises(RuntimeError, match='data_import_receipt_mismatch'):
        platform.main()


def test_legacy_registration_without_import_contract_is_not_silently_reused():
    with pytest.raises(RuntimeError, match='platform_import_contract_missing'):
        verify_business.registered_imports({'status': 'passed', 'summary': {'devices': 9}})
