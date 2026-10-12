"""Pure code-node assessment of an already persisted device snapshot; no I/O."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import calendar
import re


def text(value):
    return '' if value is None else str(value).strip()


def stamp(value):
    try:
        result = datetime.fromisoformat(text(value))
        return result if result.tzinfo is None else None
    except ValueError:
        return None


def due_at(start, code, maintenance=False, hours=None):
    if not start:
        return None
    code = text(code)
    hour_map = {'13': 168} if maintenance else {'1': 2, '2': 4, '3': 8, '4': 12, '5': 24, '6': 168}
    month_map = {'14': 1, '15': 3, '16': 6, '17': 12} if maintenance else {'7': 1, '8': 3, '9': 6, '10': 12}
    if code in hour_map:
        return start + timedelta(hours=hour_map[code])
    if code in month_map:
        months = start.year * 12 + start.month - 1 + month_map[code]
        year, month = months // 12, months % 12 + 1
        return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    # No unverified conversion for the source's "半个月" code 20.
    return None


def amount_ok(actual, expected):
    pattern = r'([0-9]+(?:\.[0-9]+)?)(?:\s*[-~～至]\s*([0-9]+(?:\.[0-9]+)?))?\s*(kg|g)'
    rule = re.fullmatch(pattern, text(expected), re.I)
    measured = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(kg|g)', text(actual), re.I)
    if not rule or not measured:
        return None
    factor = Decimal(1000) if rule[3].lower() == 'kg' else Decimal(1)
    value = Decimal(measured[1]) * (1000 if measured[2].lower() == 'kg' else 1)
    return Decimal(rule[1]) * factor <= value <= Decimal(rule[2] or rule[1]) * factor


def main(input):
    now = stamp(input['asOf'])
    if not now:
        raise ValueError('快照时间无效')
    data, findings = input.get('datasets', {}), []
    category = input.get('category', 'all')
    if category not in {'all', 'inspection', 'maintenance', 'repair'}:
        raise ValueError('未知业务类别')
    def add(device, category, status, reason, evidence=None, **extra):
        findings.append({'deviceKey': device['deviceKey'], 'deviceName': device.get('deviceName', ''),
                         'category': category, 'status': status, 'reason': reason,
                         'evidence': evidence or {}, **extra})
    for device in input['devices']:
        key = device['deviceKey']
        plans = [p for p in data.get('plans', []) if p['deviceKey'] == key]
        plans = [p for p in plans if category == 'all' or (category == 'maintenance' and text(p['planType']) == '2') or (category == 'inspection' and text(p['planType']) in {'1', '3', '4'})]
        if not plans and category != 'repair':
            add(device, '计划', 'pending', '没有关联有效计划，不能判断巡检/保养是否到期')
        for plan in plans:
            maintenance = text(plan['planType']) == '2'
            code = plan.get('maintenanceCycle') if maintenance else plan.get('cycleCode')
            candidates = [r for r in data.get('maintenance' if maintenance else 'inspection', []) +
                          ([] if maintenance else data.get('areaInspection', []))
                          if r['deviceKey'] == key and r.get('planName') == plan['planName']
                          and (maintenance or (text(r.get('planType')) == text(plan['planType']) and text(r.get('cycleCode')) == text(code)))
                          and stamp(r.get('completedAt')) and stamp(r['completedAt']) <= now and text(r.get('taskStatus')) == '1']
            duplicates = [p for p in plans if p['planName'] == plan['planName'] and
                          text(p['planType']) == text(plan['planType']) and text(p.get('cycleCode')) == text(plan.get('cycleCode'))]
            if len(duplicates) != 1:
                add(device, '计划', 'pending', '同名同类型同周期计划不唯一，无法可靠关联', plan)
                continue
            last = max((stamp(r['completedAt']) for r in candidates), default=None)
            due = due_at(last, code, maintenance, plan.get('hours'))
            add(device, '保养周期' if maintenance else '巡检周期',
                'pending' if due is None else 'warning' if now > due else 'normal',
                '采集窗口内缺少完成记录或周期无法换算；不能认定从未执行' if due is None else
                '超过源计划应完成时间' if now > due else '尚未超过源计划周期', plan,
                lastCompleted=str(last or ''), dueAt=str(due or ''))
            latest = [r for r in candidates if stamp(r['completedAt']) == last]
            if not maintenance:
                for row in latest:
                    status = {'1': 'normal', '2': 'warning'}.get(text(row.get('result')), 'pending')
                    add(device, '巡检结果', status, '采用本设备最近已完成巡检的人工结果，详见原指标及实测说明；不代表当前实时测量', row)
            else:
                for rule in data.get('maintenanceRequirements', []):
                    if rule['deviceKey'] != key or rule['planId'] != plan['planId']:
                        continue
                    matches = [r for r in candidates if text(r.get('pointStatus')) == '1' and
                               (text(r.get('pointCode')) == text(rule['pointCode']) if text(rule.get('pointCode')) else r.get('point') == rule.get('point'))]
                    completed = max((stamp(r['completedAt']) for r in matches), default=None)
                    records = [r for r in matches if stamp(r['completedAt']) == completed]
                    if len(records) != 1:
                        add(device, '润滑保养', 'pending', '本润滑点缺少唯一的已完成记录', rule)
                        continue
                    row, warnings, pending = records[0], [], []
                    try:
                        days = Decimal(text(rule.get('days')))
                        if days <= 0:
                            raise ValueError()
                        if now > completed + timedelta(days=float(days)):
                            warnings.append('超过本点润滑周期')
                    except (ValueError, InvalidOperation, OverflowError):
                        pending.append('润滑周期无效')
                    for field, label in [('oil', '油品'), ('method', '方式')]:
                        if not text(rule.get(field)) or not text(row.get(field)):
                            pending.append(label + '要求或实测缺失')
                        elif text(rule[field]) != text(row[field]):
                            warnings.append(label + '与本点要求不一致')
                    amount = amount_ok(row.get('amount'), rule.get('amount'))
                    if amount is False:
                        warnings.append('加注量超出本点要求')
                    elif amount is None:
                        pending.append('用量缺少明确数值及单位')
                    if text(rule.get('oilChangeCycle')):
                        pending.append('换油要求需独立换油完成记录核实')
                    add(device, '润滑保养', 'warning' if warnings else 'pending' if pending else 'normal',
                        '；'.join(warnings + pending) or '本点要求已核实', {'rule': rule, 'record': row})
        if category == 'repair' and not any(r['deviceKey'] == key for r in data.get('repair', [])):
            add(device, '维修', 'pending', '本次采集范围内无维修任务记录，不据此判断设备正常或无需维修')
        for row in (data.get('repair', []) if category in {'all', 'repair'} else []):
            if row['deviceKey'] != key:
                continue
            status, completed, deadline = text(row.get('status')), stamp(row.get('completedAt')), stamp(row.get('deadline'))
            if status == '3' and completed and completed <= now:
                outcome = 'warning' if text(row.get('acceptance')) == '2' or text(row.get('installation')) == '2' else 'normal' if text(row.get('acceptance')) == '1' else 'pending'
                reason = '维修已完成；按原验收记录判断，不将历史超期当作当前逾期'
            elif status == '4' or (status in {'1', '2'} and deadline and now > deadline):
                outcome, reason = 'warning', '维修失败或当前未完成任务已超过预计完成时间'
            else:
                outcome, reason = 'pending', '尚未完成或状态/日期不完整，需持续跟踪'
            add(device, '维修', outcome, reason, row)
    return {'category': category, 'asOf': input['asOf'], 'windowStart': input['windowStart'], 'scope': input['scope'],
            'datasourceId': input['datasourceId'], 'deviceCount': len(input['devices']),
            'summary': {key: sum(r['status'] == key for r in findings) for key in ('normal', 'warning', 'pending')},
            'data': findings, 'queries': input.get('queries', {}),
            'note': 'AI 只能解释以上证据，未知不能判正常；没有独立数值实测时不自动推算阈值合格。'}
