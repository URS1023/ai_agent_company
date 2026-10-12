"""Prepare a reviewable batch, then insert only its new test records in one transaction."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import random
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from .catalog import GAS_FIELDS, device_context, quality_context
from .database import DataError, LocalDatabase, ROOT, TABLE_KEYS, identifier, load_connection_and_flows
from .samples import decimal, outcomes, sample

DEFAULT_OPTIONS = {'supplement': True, 'gasVolume': '280', 'phosphine': '0.08', 'sulfide': '0.1',
                   'inspectionHours': '24', 'maintenanceDays': '7', 'oil': 'GUI测试润滑油',
                   'amount': '1kg', 'method': 'GUI测试加注'}
BATCHES = ROOT / 'storage/workflow-test-data'


def moment(seconds=0):
    return {'$writeTimeSeconds': seconds}


def resolve_time(values, now):
    return {key: now + timedelta(seconds=value['$writeTimeSeconds'])
            if isinstance(value, dict) and set(value) == {'$writeTimeSeconds'} else value
            for key, value in values.items()}


def amount_midpoint(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:[-~～—至]\s*(\d+(?:\.\d+)?))?\s*(kg|g)\s*', str(value), re.I)
    if not match:
        raise DataError('保养用量需要明确的 g / kg 数值或范围')
    low, high = decimal(match[1]), decimal(match[2] or match[1])
    if low > high:
        raise DataError('保养用量范围顺序不正确')
    return str(((low + high) / 2).normalize()) + match[3].lower()


class Builder:
    def __init__(self, db, options):
        self.db, self.options = db, {**DEFAULT_OPTIONS, **options}
        self.rng = random.Random(uuid4().int)
        self.batch = {'id': 'GUI-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:8],
                      'status': 'preview', 'rows': [], 'preview': [], 'warnings': [], 'workflows': []}
        self.test_plans = {}

    def row(self, table, values):
        schema = self.db.schema(table)
        key = TABLE_KEYS[table]
        prepared = {**self.db.required_defaults(table), key: 'GUISIM1-' + uuid4().hex,
                    'createdOn': moment(), 'modifiedOn': moment(), 'isDeleted': 0, **values}
        if 'modifiedBy' in schema and 'createdBy' in prepared:
            prepared['modifiedBy'] = prepared['createdBy']
        prepared = {k: v for k, v in prepared.items() if k in schema}
        missing = [name for name, col in schema.items() if col['Null'] == 'NO' and col['Default'] is None
                   and 'auto_increment' not in col['Extra'] and prepared.get(name) is None]
        if missing:
            raise DataError('目标表缺少必要归属信息：' + ', '.join(missing))
        self.batch['rows'].append({'table': table, 'values': prepared})
        if len(self.batch['rows']) > 550:
            raise DataError('本批明细超过 550 行，请减少生成数量')
        return prepared[key]

    def item(self, flow, subject, passed, detail, rules='已有标准'):
        self.batch['preview'].append({'workflow': flow['name'], 'workflowId': flow['id'], 'subject': subject,
            'outcome': '合格 / 正常' if passed else '不合格 / 异常', 'passed': passed, 'detail': detail, 'rules': rules})

    def quality(self, flow, count, mode, fail_percent):
        context = quality_context(self.db, flow)
        rules = deepcopy(context['rules'])
        if context['cloneTemplate']:
            if not self.options['supplement']:
                raise DataError(flow['name'] + '的产品未关联标准模板，请启用测试模板补充')
            source_name = context['template']['name']
            template_id = self.row('t_qualityinspectiontemplate', {
                'c_templateName': self.batch['id'] + '-' + source_name,
                'c_product': flow['parameters']['product']})
            for rule in rules:
                self.row('t_qitemplatedetails', {'mdQualityInspectionTemplateId': template_id,
                    'c_xuanzezhijianxiangmu': rule['projectId'], 'c_judgeRule': rule['op'],
                    'c_standardMin': rule['low'], 'c_standardMax': rule['high'],
                    'c_shifouzhuyaozhijianxiangmu': rule['main']})
            context['template'] = {'id': template_id, 'name': source_name + '（本批测试副本）'}
            self.batch['warnings'].append(flow['name'] + '：按现有“' + source_name + '”复制测试模板，关联本工作流产品；原配置未修改。')
        supplemental = []
        for rule in rules:
            if rule['op'] and rule['high'] is not None and rule['main'] is not None:
                continue
            gas = GAS_FIELDS.get(rule['code'])
            if not self.options['supplement'] or not gas:
                raise DataError(flow['name'] + '：' + rule['name'] + '缺少判断标准，请启用测试标准补充')
            option = {'c_faqiliang': 'gasVolume', 'c_linhuaqing': 'phosphine', 'c_liuhuaqing': 'sulfide'}[gas[1]]
            rule.update(op='gte' if option == 'gasVolume' else 'lte', low=None,
                        high=decimal(self.options[option]), main=1)
            supplemental.append(rule['name'])
        if supplemental:
            self.batch['warnings'].append(flow['name'] + '：本批单据使用界面中的测试标准，原模板未改动。')
        for index, passed in enumerate(outcomes(count, mode, fail_percent, self.rng), 1):
            number = self.batch['id'] + '-Q' + str(len(self.batch['preview']) + 1).zfill(3)
            form_id = self.row('t_incomingmaterialqcform', {
                'c_incomingQcNo': number, 'c_qualityInspectionTemplate': context['template']['id'],
                'c_qualityInspection': flow['parameters']['category'], 'c_samplingTime': moment(),
                'c_quyangshijiandian': moment(), 'c_testResults': 1 if passed else 2,
                'c_zhijianshifouhege': 1 if passed else 0,
                'c_beizhu': '【GUI测试数据】' + self.batch['id'],
                'c_conclusion': '测试样本，预期' + ('合格' if passed else '不合格') + '；不用于实际审批放行。'})
            failed_index = self.rng.randrange(len(rules)) if not passed else -1
            gas_rows, details = {}, []
            for rule_index, rule in enumerate(rules):
                value = sample(rule, rule_index != failed_index, self.rng)
                self.row('t_qualityinspectionitemdetails', {
                    'mdIncomingMaterialQcFormId': form_id, 'c_qualityInspectionProject': rule['projectId'],
                    'c_jianyanjieguo': str(value), 'c_testresults': str(value),
                    'c_judgeRule': rule['op'], 'c_standardMin': rule['low'], 'c_standardMax': rule['high'],
                    'c_shifoushizhuyaopanduanbiaozhun': rule['main']})
                if rule['code'] in GAS_FIELDS:
                    table, field = GAS_FIELDS[rule['code']]
                    gas_rows.setdefault(table, {'mdIncomingMaterialQcFormId': form_id, 'c_timePoint': moment(),
                                               'c_beizhu': '【GUI测试数据】' + self.batch['id'], 'c_luyanshu': 1})[field] = str(value)
                boundary = (str(rule['low']) + '～' if rule['op'].startswith('between') else '') + str(rule['high'])
                details.append(f"{rule['name']}={value.normalize()}（{rule['op']} {boundary}）")
            for table, values in gas_rows.items():
                self.row(table, values)
            self.item(flow, number, passed, context['template']['name'] + '；' + '；'.join(details),
                      '补充测试标准' if supplemental else '复制已有标准' if context['cloneTemplate'] else '已有标准')

    def test_plan(self, flow, device):
        cache_key = (flow['id'], device['id'])
        if cache_key in self.test_plans:
            return self.test_plans[cache_key]
        if not self.options['supplement']:
            raise DataError(device['name'] + '缺少可用计划或完整标准。可启用“补充测试标准/计划”后预览。')
        maintenance = flow['kind'] == 'maintenance'
        hours = int(self.options['inspectionHours'])
        cycle_map = {2: 1, 4: 2, 8: 3, 12: 4, 24: 5, 168: 6}
        if hours not in cycle_map:
            raise DataError('巡检测试周期只支持 2、4、8、12、24、168 小时')
        days = int(self.options['maintenanceDays'])
        if not 1 <= days <= 365:
            raise DataError('润滑点测试周期应为 1–365 天')
        name = self.batch['id'] + ('-保养-' if maintenance else '-巡检-') + device['name']
        plan = {'name': name[:200], 'type': 2 if maintenance else 1, 'cycle': cycle_map[hours], 'maintenanceCycle': '13'}
        plan['id'] = self.row('t_dinspectionmaintenanceplan', {'c_planName': plan['name'],
            'c_jihualeixing': plan['type'], 'c_xunjianzhibiao': plan['cycle'],
            'c_xiaoshi': hours, 'c_baoyangzhouqi': '13' if maintenance else None})
        self.row('t_inspectiondevice', {'mdDInspectionMaintenancePlanId': plan['id'], 'c_device': device['id'],
                                      'c_deviceDesc': '【GUI测试数据】' + self.batch['id']})
        points = []
        if maintenance:
            amount = amount_midpoint(self.options['amount'])
            point = {'point': 'GUI测试润滑点', 'code': 'GUI-POINT-1', 'days': days, 'oil': self.options['oil'],
                     'amount': amount, 'method': self.options['method'], 'oilChange': None}
            if not point['oil'].strip() or not point['method'].strip():
                raise DataError('请填写测试油品和保养方式')
            self.row('t_xunjianjihuarunhuabaoyang', {'mdDInspectionMaintenancePlanId': plan['id'],
                'c_runhuadianweimingcheng': point['point'], 'c_runhuadianweibianhao': point['code'],
                'c_runhuazhouqi': days, 'c_runhuayoupaihao': point['oil'], 'c_runhualiang': amount,
                'c_runhuafangshi': point['method'], 'c_beizhu': '【GUI测试数据】' + self.batch['id']})
            points.append(point)
        self.batch['warnings'].append(device['name'] + '：新增独立测试计划；原计划和标准保持原样。')
        self.test_plans[cache_key] = (plan, points)
        return plan, points

    def plan_for(self, flow, device, context):
        plans = [p for p in context['plans'] if p['deviceId'] == device['id']]
        signatures = Counter((p['name'], p['type'], str(p['cycle'])) for p in plans)
        for plan in plans:
            if plan['name'].startswith('GUI-') or signatures[(plan['name'], plan['type'], str(plan['cycle']))] != 1:
                continue
            if flow['kind'] == 'inspection' and str(plan['cycle']) in {str(x) for x in range(1, 11)}:
                return plan, []
            if flow['kind'] == 'maintenance' and str(plan['maintenanceCycle']) in {'13', '14', '15', '16', '17'}:
                points = [r for r in context['rules'] if r['planId'] == plan['id']]
                try:
                    if (not points or any(not r['oil'] or not r['method'] or not r['days'] or r['days'] <= 0
                            or r['oilChange'] for r in points)
                            or len({r['code'] or r['point'] for r in points}) != len(points)):
                        continue
                    for point in points:
                        amount_midpoint(point['amount'])
                except DataError:
                    continue
                return plan, points
        return self.test_plan(flow, device)

    def device(self, flow, count, mode, fail_percent):
        context = device_context(self.db, flow)
        devices = context['devices']
        self.rng.shuffle(devices)
        for index, passed in enumerate(outcomes(count, mode, fail_percent, self.rng)):
            device = devices[index % len(devices)]
            completed = moment(-max(1, count - index))
            if flow['kind'] == 'repair':
                self.row('t_equipmentmaintenancelog', {'c_zhubiaoshebei': device['id'], 'c_shebeimingcheng': device['name'],
                    'c_jianxiudianmingcheng': self.batch['id'] + '-维修测试', 'c_jianxiuneirong': '【GUI测试数据】验收场景',
                    'c_weixiuzhuangtai': 3, 'c_yujichuliwanchengshijian': moment(3600),
                    'c_shijichuliwanchengshijian': completed,
                    'c_daochangyanshoushifouhege': 1 if passed else 2, 'c_shifouanzhuangwancheng': 1})
                self.item(flow, device['name'], passed, '维修已完成；验收' + ('合格' if passed else '不合格'), '工作流状态规则')
                continue
            plan, points = self.plan_for(flow, device, context)
            maintenance = flow['kind'] == 'maintenance'
            task_id = self.row('t_deviceinspectionmaintenance', {'c_deviceId': device['id'],
                'c_jihualeixing': plan['type'], 'c_recordType': plan['cycle'], 'c_xunjianjihua': plan['name'],
                'c_maintenanceStatus': 1 if maintenance else None, 'c_inspectionStatus': 1 if not maintenance else None,
                'c_recordTime': completed if not maintenance else None,
                'c_recentMaintenancePeriod': completed if maintenance else None,
                'c_inspectionSummarize': '【GUI测试数据】' + self.batch['id']})
            if maintenance:
                details = []
                for point_index, point in enumerate(points):
                    oil = point['oil'] if passed or point_index else ('GUI测试不符油品-' + point['oil'])[:200]
                    self.row('t_shebeirunhuabaoyangmingxibiao', {'mdDeviceInspectionMaintenanceId': task_id,
                        'c_runhuadianweimingcheng': point['point'], 'c_runhuadianweibianhao': point['code'],
                        'c_runhuazhouqi': point['days'], 'c_runhuayoupaihao': oil,
                        'c_runhualiang': amount_midpoint(point['amount']), 'c_runhuafangshi': point['method'],
                        'c_baoyangzhuangtai': 1, 'c_beizhu': '【GUI测试数据】' + self.batch['id']})
                    details.append(f"{point['point']}：{oil}，{amount_midpoint(point['amount'])}，{point['method']}")
                detail = '；'.join(details)
            else:
                self.row('t_deviceinspectiondetails', {'mdDeviceInspectionMaintenanceId': task_id,
                    'c_shebei': device['id'], 'c_inspectionName': 'GUI测试巡检点',
                    'c_inspectionResult': 1 if passed else 2, 'c_inspectionContent': 'GUI测试：设备巡检记录判断',
                    'c_inspectionDesc': '【GUI测试数据】' + self.batch['id'] + ('；记录正常' if passed else '；记录异常')})
                detail = '已完成巡检，结果编码 ' + ('1（正常）' if passed else '2（异常）')
            self.item(flow, device['name'], passed, detail, '新增测试计划' if plan['name'].startswith('GUI-') else '已有计划')


def prepare(credentials, flows, count, mode, fail_percent, options):
    if not flows or len(flows) * count > 50:
        raise DataError('请选择工作流；每批最多生成 50 份单据/任务')
    with LocalDatabase(credentials) as db:
        builder = Builder(db, options)
        for flow in flows:
            builder.batch['workflows'].append({k: flow[k] for k in ('id', 'name', 'revision')})
            (builder.quality if flow['kind'] == 'quality' else builder.device)(flow, count, mode, fail_percent)
        batch = builder.batch
        batch['warnings'] = list(dict.fromkeys(batch['warnings']))
        batch['summary'] = {'records': len(batch['preview']), 'rows': len(batch['rows']),
                            'passed': sum(r['passed'] for r in batch['preview'])}
        batch['summary']['failed'] = batch['summary']['records'] - batch['summary']['passed']
        batch['readBudget'] = {'connections': 1, 'queries': db.calls, 'explains': db.explains}
        return batch


def save_manifest(batch):
    BATCHES.mkdir(parents=True, exist_ok=True)
    path = BATCHES / (batch['id'] + '.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)
    return path


def write_batch(credentials, batch, *, rollback_only=False):
    if batch['status'] != 'preview' or not 1 <= len(batch['rows']) <= 550:
        raise DataError('这批数据已处理或无有效预览，请重新预览')
    current_credentials, flows = load_connection_and_flows()
    versions = {f['id']: f['revision'] for f in flows}
    if any(versions.get(f['id']) != f['revision'] for f in batch['workflows']):
        raise DataError('工作流发布版本已变化，请重新加载并预览')
    if current_credentials != credentials:
        raise DataError('本地数据源配置已变化，请重新打开工具')
    now = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None, microsecond=0)
    if not rollback_only:
        batch['status'] = 'writing'
        batch['writeTime'] = now.isoformat(' ')
        save_manifest(batch)
    try:
        with LocalDatabase(credentials, budget=650) as db:
            for item in batch['rows']:
                db.insert(item['table'], resolve_time(item['values'], now))
            # Verify exact generated IDs in the same transaction; no full-table count.
            grouped = {}
            for item in batch['rows']:
                grouped.setdefault(item['table'], []).append(item['values'][TABLE_KEYS[item['table']]])
            for table, ids in grouped.items():
                rows = db.query('SELECT COUNT(*) count FROM ' + identifier(table) + ' WHERE '
                    + identifier(TABLE_KEYS[table]) + ' IN (' + ','.join(['%s'] * len(ids)) + ')', ids)
                if rows[0]['count'] != len(ids):
                    raise DataError('写入条数核对不一致，整批已取消')
            # DATETIME columns have second precision. Stamp just before commit, slightly
            # ahead of the current boundary, so an overlapping scheduled read cannot
            # advance its watermark past records that are still uncommitted.
            collected_at = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None, microsecond=0) + timedelta(seconds=2)
            for table, ids in grouped.items():
                time_fields = [k for k in ('createdOn', 'modifiedOn') if k in db.schema(table)]
                db.query('UPDATE ' + identifier(table) + ' SET ' + ','.join(identifier(k) + '=%s' for k in time_fields)
                    + ' WHERE ' + identifier(TABLE_KEYS[table]) + ' IN (' + ','.join(['%s'] * len(ids)) + ')',
                    [collected_at] * len(time_fields) + ids, explain=False)
            if not rollback_only:
                db.connection.commit()
            result = {'connections': 1, 'queries': db.calls, 'explains': db.explains,
                      'rows': len(batch['rows']), 'rollbackOnly': rollback_only, 'writeTime': now.isoformat(' '),
                      'collectionTime': collected_at.isoformat(' ')}
        if not rollback_only:
            batch['status'] = 'written'
            batch['writeReceipt'] = result
            save_manifest(batch)
        return result
    except Exception:
        if not rollback_only:
            batch['status'] = 'unconfirmed'
            save_manifest(batch)
        raise


def undo_batch(credentials, batch):
    if batch['status'] not in {'written', 'unconfirmed', 'writing'}:
        raise DataError('该批次没有需要撤销的写入')
    with LocalDatabase(credentials, budget=650) as db:
        # Validate the entire manifest and transactional tables before any deletion.
        for item in batch['rows']:
            table = item['table']
            if table not in TABLE_KEYS or not re.fullmatch(r'GUISIM1-[0-9a-f]{32}', item['values'].get(TABLE_KEYS.get(table), '')):
                raise DataError('只允许撤销本工具新增的测试记录')
            db.schema(table)
        for item in reversed(batch['rows']):
            table = item['table']
            if table not in TABLE_KEYS:
                raise DataError('批次文件包含不允许的表')
            key = TABLE_KEYS[table]
            record_id = item['values'].get(key, '')
            if not re.fullmatch(r'GUISIM1-[0-9a-f]{32}', record_id):
                raise DataError('只允许撤销本工具新增的测试记录')
            db.query('DELETE FROM ' + identifier(table) + ' WHERE ' + identifier(key) + '=%s', (record_id,), explain=False)
        db.connection.commit()
    batch['status'] = 'undone'
    save_manifest(batch)
