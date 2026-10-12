"""Deterministic demo operations analysis; measurements and missing values stay distinct."""
from __future__ import annotations


def analyze_operating_rows(rows):
    from collections import Counter
    from datetime import datetime, timezone, timedelta
    from math import isfinite

    def number(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
            return result if isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def moment(value):
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    groups = {}
    for row in rows:
        if row.get('is_demo') not in (1, True) or not str(row.get('device_name', '')).startswith('演示-'):
            raise ValueError('演示分析只接受明确标记的独立演示数据')
        groups.setdefault(row['device_id'], []).append(row)
    latest, checks, trends = [], [], []
    for identity, samples in sorted(groups.items()):
        samples = sorted(samples, key=lambda row: moment(row['observed_at']))
        if len({str(row['observed_at']) for row in samples}) != len(samples):
            raise ValueError('同一设备存在重复采样时刻，不能合并为趋势')
        row = samples[-1]
        state = row.get('state')
        temperature, rpm, uptime = (number(row.get(k)) for k in ('temperature_c', 'rpm', 'uptime_seconds'))
        maximum_temp, maximum_rpm = number(row.get('temperature_limit_c')), number(row.get('rpm_limit'))
        valid_temps = [number(sample.get('temperature_c')) for sample in samples if number(sample.get('temperature_c')) is not None]
        item = {'deviceId': identity, 'deviceName': row['device_name'], 'state': state,
                'observedAt': moment(row['observed_at']).isoformat(), 'bootAt': moment(row['boot_at']).isoformat() if row.get('boot_at') else None,
                'uptimeHours': round(uptime / 3600, 2) if uptime is not None and uptime >= 0 else None,
                'rpm': rpm, 'temperatureC': temperature, 'sampleCount': len(samples),
                'meanTemperatureC': round(sum(valid_temps) / len(valid_temps), 2) if valid_temps else None,
                'maximumTemperatureC': max(valid_temps) if valid_temps else None, 'isDemo': True}
        latest.append(item)
        def add(project, outcome, description, actual):
            checks.append({'设备': row['device_name'], '检查项目': project, '结果': outcome,
                           '说明': '演示数据；示例阈值，仅用于功能验收。' + description,
                           '实测值': actual, 'observedAt': item['observedAt'], 'isDemo': True})
        add('运行状态', '待确认' if state == 'offline' or state not in {'running','stopped','fault'} else '预警' if state == 'fault' else '正常',
            {'running': '采样时处于运行状态。', 'stopped': '采样时处于停机状态。', 'fault': '采样时上报故障状态。', 'offline': '采样时离线，不能推断设备仍在运行。'}.get(state, '缺少有效状态。'), {'running': '运行', 'stopped': '停机', 'fault': '故障', 'offline': '离线'}.get(state, '未记录'))
        add('温度', '待确认' if temperature is None or maximum_temp is None else '预警' if temperature > maximum_temp else '正常',
            f'示例上限 {maximum_temp} ℃；缺失温度不按 0 处理。', None if temperature is None else f'{temperature:g} ℃')
        add('转速', '待确认' if rpm is None or maximum_rpm is None else '预警' if rpm < 0 or rpm > maximum_rpm or (state == 'stopped' and rpm > 0) else '正常',
            f'示例上限 {maximum_rpm} rpm；停机状态下转速应为 0。', None if rpm is None else f'{rpm:g} rpm')
        invalid_uptime = uptime is None or uptime < 0 or not row.get('boot_at') or moment(row['boot_at']) > moment(row['observed_at'])
        if not invalid_uptime:
            invalid_uptime = uptime > (moment(row['observed_at']) - moment(row['boot_at'])).total_seconds() + 1
        boot_label = moment(row['boot_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if row.get('boot_at') else '未记录'
        add('开机时长', '待确认' if invalid_uptime else '正常',
            f'开机时间（北京时间）：{boot_label}。依据采样时上报的本次开机累计运行秒数；停机保留上一运行周期值；未按当前时间补算。',
            None if item['uptimeHours'] is None else f"{item['uptimeHours']:g} 小时")
        trends.extend({'deviceId': identity, 'deviceName': row['device_name'],
                       'observedAt': moment(sample['observed_at']).isoformat(),
                       'temperatureC': number(sample.get('temperature_c')), 'rpm': number(sample.get('rpm'))} for sample in samples)
    status_counts = dict(Counter(item['state'] or 'unknown' for item in latest))
    return {'schemaVersion': 'device-operating-analysis-v1', 'isDemo': True,
            'notice': '独立演示数据与示例阈值；历史采样快照，不代表实际设备或实时监测。',
            'deviceCount': len(latest), 'sampleCount': len(rows), 'statusCounts': status_counts,
            'windowStart': min((item['observedAt'] for item in trends), default=None),
            'windowEnd': max((item['observedAt'] for item in trends), default=None),
            'latest': latest, 'trends': trends, 'data': checks, 'truncated': False}
