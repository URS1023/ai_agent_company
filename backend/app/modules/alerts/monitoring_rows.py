"""Associate snapshot evidence for visual nodes; no business verdicts or thresholds.

职责：把 ``monitoring.collect_snapshot`` 采集到的原始数据集（plans / inspection /
areaInspection / maintenanceRequirements / maintenance / repair）整理成**按设备**展开的
画布行（``visualRows``），供可视化节点与 ``workflows/business_report`` 直接成表展示。

位置与依赖：仅被 ``alerts.monitoring`` 调用（采集末尾），不访问数据库、不读取权限上下文，
因此**无任何权限判断**；所有阈值与"是否正常"的判定都留在流程画布上的规则节点，
这里只做计数、连接与"最近一次记录"的关联。

重要约定：本模块只做"事实对齐"，不补造数据 —— 缺记录时输出的是**缺少业务字段的占位行**
（见 ``visual_rows``），由下游按 ``planMatches`` / ``pointMatches`` 等计数过滤或标注，
调用方不得把占位行当作"设备正常"的证据。
"""
from datetime import datetime


def text(value):
    """统一空值口径：``None`` 视为空串并去首尾空白。

    源库多为 MySQL 混合类型（字符串 / 数字 / DateTime），比较前一律转字符串，
    避免 ``'2'`` 与 ``2``、``' 2 '`` 与 ``'2'`` 被判为不等而误判"计划不匹配"。
    """
    return '' if value is None else str(value).strip()


def latest(rows):
    """在给定记录中取 ``completedAt`` 最大的一批（同刻多条全部保留）。

    已知边界：
    - 只接受可被 ``fromisoformat`` 解析且**不带时区**的时间；带时区的值被忽略，
      因此源库若返回带偏移量的时间，这些记录不会被选为"最近一次"。
    - 全部记录都没有合法时间时返回 ``[]``（调用方据此产出 ``pointMatches=0`` 等占位行），
      不会退化为"取第一条"。
    """

    dated = []
    for row in rows:
        try:
            date = datetime.fromisoformat(text(row.get('completedAt')))
            if date.tzinfo is None:
                dated.append((date, row))
        except ValueError:
            pass
    newest = max((date for date, _ in dated), default=None)
    return [row for date, row in dated if date == newest]


def visual_rows(snapshot):
    """Counts, joins and latest records only. All decisions live on the canvas.

    返回四个键 ``plans`` / ``inspection`` / ``maintenance`` / ``repair`` 的行列表。
    每个元素都是 ``{设备字段..., asOf, windowStart, scope, 业务类型, <计划或记录字段>}``
    的扁平字典，即"设备基线 + 业务字段"的并集，前端无需再关联设备表。

    行成形规则（关键）：
    - ``plans``：按设备的计划台账逐条展开；``planMatches`` 是**同名计划（名称+类型+周期）的
      重复条数**，``recordMatches`` 是最近一次已完成任务的明细条数。设备**没有任何计划**时，
      合成长度为 1 的占位行 ``{**base, 'planMatches': 0}`` —— 该行只含设备字段，没有
      ``planName`` 等业务字段。
    - ``inspection``：巡检明细直接铺开（``{**common, **row}``）。
    - ``maintenance``：按"润滑要求"逐点展开；设备/计划**没有要求行**时合成
      ``{**common, 'pointMatches': 0}`` 占位行（同样缺 ``point``/``itemId`` 等业务字段）。
      每个点的实际值只有在最近记录**恰好一条**时才带上（``actual`` 否则为空字典），
      因此 ``actual_*`` 可为 ``None``——这里宁可留空也不编造数值。
    - ``repair``：无维修记录时用 ``rows or [{}]`` 合成**仅含设备字段**的占位行。
      也就是说 repair 行永远至少 1 条/设备，下游判断维修必须依赖业务字段是否存在
      （如 ``recordId`` / ``status`` / ``completedAt``），不能只看行数。

    下游过滤约定：
    - ``workflows/business_report.py`` 依据 ``planMatches == 0``、``planMatches > 1``、
      ``recordMatches == 0`` 生成"计划不唯一/缺少完成记录/周期未明确"等说明，
      即**占位行是靠计数字段被识别的，不是靠行缺失**；
      因此占位行必须保留这些计数字段，删字段会让下游把它们误当真实业务记录。
    - ``category`` 决定哪些键被填充：仅 ``all`` 会同时产出四类，其余只产出对应类别
      （``plans`` 仍会为巡检/保养生成，作为基线）。

    参数 ``snapshot`` 需含 ``devices`` / ``datasets`` / ``category`` / ``asOf`` /
    ``windowStart`` / ``scope``（见 ``monitoring.collect_snapshot``）；不返回异常，
    但要求 ``snapshot['devices']`` 中每个设备都有 ``deviceKey``。
    """
    out = {key: [] for key in ('plans', 'inspection', 'maintenance', 'repair')}
    data, category = snapshot['datasets'], snapshot['category']
    for device in snapshot['devices']:
        # base 是"设备基线"，必须逐行复制进每个业务行，前端据此展示设备名/位置等信息。
        base = {**device, 'asOf': snapshot['asOf'], 'windowStart': snapshot['windowStart'],
                'scope': snapshot['scope'], '业务类型': {'maintenance':'保养','inspection':'巡检','repair':'维修'}.get(category, '')}
        key = device['deviceKey']
        plans = [p for p in data.get('plans', []) if p.get('deviceKey') == key]
        if category in ('inspection', 'maintenance', 'all'):
            # 无计划设备也要出行（仅设备字段），否则画布上看不出"这台设备压根没配计划"。
            if not plans:
                out['plans'].append({**base, 'planMatches': 0})
            for plan in plans:
                # 源库计划类型：2=保养，其余（1/3/4）按巡检处理。
                kind = 'maintenance' if text(plan.get('planType')) == '2' else 'inspection'
                if category not in ('all', kind):
                    continue
                matches = [p for p in plans if all(text(p.get(f)) == text(plan.get(f))
                           for f in ('planName', 'planType', 'cycleCode'))]
                records = [r for r in data.get(kind, []) + (data.get('areaInspection', []) if kind == 'inspection' else [])
                           if r.get('deviceKey') == key and r.get('planName') == plan.get('planName')
                           and (kind == 'maintenance' or all(text(r.get(f)) == text(plan.get(f)) for f in ('planType', 'cycleCode')))]
                # 巡检/保养都只看"每个计划最近一次已完成"的记录，不是全量历史。
                recent = latest(records)
                common = {**base, **plan, 'planMatches': len(matches), 'recordMatches': len(recent),
                          'completedAt': recent[0].get('completedAt') if recent else None,
                          'cycle': text(plan.get('maintenanceCycle') if kind == 'maintenance' else plan.get('cycleCode'))}
                out['plans'].append(common)
                if kind == 'inspection':
                    out[kind].extend({**common, **row} for row in recent)
                else:
                    rules = [r for r in data.get('maintenanceRequirements', [])
                             if r.get('deviceKey') == key and r.get('planId') == plan.get('planId')]
                    # 计划在册但没有润滑要求明细：同样只出占位行，pointMatches=0 供下游识别。
                    if not rules:
                        out[kind].append({**common, 'pointMatches': 0})
                    for rule in rules:
                        # pointCode 优先做点匹配（源库历史数据可能没有该列，故退化到 point 名称）。
                        points = latest([r for r in records if
                            (text(r.get('pointCode')) == text(rule['pointCode']) if text(rule.get('pointCode'))
                             else r.get('point') == rule.get('point'))])
                        # 仅当命中恰好一条记录时才认为"该点有实测值"，多条视为不唯一、不取其一。
                        actual = points[0] if len(points) == 1 else {}
                        out[kind].append({**common, 'pointMatches': len(points),
                            'itemId': rule.get('itemId'), 'point': rule.get('point'),
                            # required_* 来自要求台账，actual_* 来自实测记录；缺值一律 None，不补默认值。
                            **{'required_' + f: text(rule.get(f)) or None for f in ('days', 'oil', 'method', 'amount', 'oilChangeCycle')},
                            **{'actual_' + f: text(actual.get(f)) or None for f in ('oil', 'method', 'amount')},
                            'completedAt': actual.get('completedAt'), 'taskStatus': actual.get('taskStatus'),
                            'pointStatus': actual.get('pointStatus'), 'recordId': actual.get('recordId')})
        if category in ('repair', 'all'):
            rows = [r for r in data.get('repair', []) if r.get('deviceKey') == key]
            # 无维修记录时补一条纯设备行，保证每台设备在维修画布上都有位置。
            out['repair'].extend({**base, **r} for r in (rows or [{}]))
    return out
