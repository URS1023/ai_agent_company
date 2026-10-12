"""Editable visual rules over persisted source evidence; no code judgment nodes."""
from configurations.yinhao.graph_builder import GraphBuilder, calc, compare, expr, field, missing


def build_graph(source, keyword, model_profile='reasoning', category='inspection'):
    if category not in {'inspection', 'maintenance', 'repair'}:
        raise ValueError('Choose one independent device category')
    label = {'inspection': '巡检', 'maintenance': '保养', 'repair': '维修'}[category]
    b = GraphBuilder(source)
    b.read('devices', '读取设备范围', 't_devicemanagement',
           {'deviceManagementId': 'deviceKey', 'c_deviceName': 'deviceName'},
           filters=[('c_deviceName', 'contains', v) for v in (keyword if isinstance(keyword, list) else [keyword])])
    b.nodes[-1]['config']['maxRows'] = 30
    b.add('snapshot', 'tool', '采集并保存原始数据快照', {
        'toolName': 'collect_device_monitoring_snapshot',
        'arguments': {'datasource_id': source['id'], 'category': category}, 'includeInput': True}, 'devices')

    def prepare(key, title, dataset, calculations):
        b.calculate(key, title, calculations, 'snapshot')
        b.nodes[-1]['inputs'] = {'input': {'nodeId': 'snapshot', 'path': '$.data.visualRows.' + dataset}}
        return key

    def select_rows(parent, key, title, expression):
        node = next(n for n in b.nodes if n['id'] == parent)
        incoming = [e for e in b.edges if e['target'] == parent]
        b.edges[:] = [e for e in b.edges if e['target'] != parent]
        b.add(key, 'filter', title, {'expression': expression})
        b.nodes[-1]['inputs'] = node['inputs']
        node['inputs'] = {}
        for edge in incoming:
            b.edge(edge['source'], key, edge['port'])
        b.edge(key, parent)

    def check(key, title, parent, invalid, violation, pending, warning, normal):
        b.check(key, title, parent, invalid, violation, pending=pending, warning=warning, normal=normal)

    def number(name, source):
        return calc(name, 'number', source)

    if category != 'repair':
        hours = {'13': 168} if category == 'maintenance' else {'1': 2, '2': 4, '3': 8, '4': 12, '5': 24, '6': 168}
        months = {'14': 1, '15': 3, '16': 6, '17': 12} if category == 'maintenance' else {'7': 1, '8': 3, '9': 6, '10': 12}
        p = prepare('cycle_values', '计算源计划周期与应完成时间', 'plans', [
            calc('周期小时', 'lookup', 'cycle', mapping=hours), calc('周期月数', 'lookup', 'cycle', mapping=months),
            calc('小时到期', 'add_hours', 'completedAt', '周期小时'), calc('月份到期', 'add_months', 'completedAt', '周期月数'),
            calc('应完成时间', 'coalesce', '小时到期', '月份到期'),
            calc('超期小时', 'hours_between', '应完成时间', 'asOf'),
            calc('完成距今小时', 'hours_between', 'completedAt', 'asOf')])
        if category == 'maintenance':
            select_rows(p, 'applicable_plans', '仅检查已配置保养计划的设备', compare('gt', 'planMatches', 0))
        check('cycle', label + '是否超期', p,
              expr('or', compare('ne', 'planMatches', 1), missing('超期小时', '完成距今小时'), compare('lt', '完成距今小时', 0)),
              compare('gt', '超期小时', 0), '计划不唯一、缺少完成记录或周期未明确，不能判断超期。',
              '已超过源计划应完成时间。', '尚未超过源计划周期。')
    if category == 'inspection':
        p = prepare('inspection_values', '读取巡检结果与任务状态', 'inspection', [
            number('原结果编码', 'result'), number('任务状态编码', 'taskStatus'),
            calc('完成距今小时', 'hours_between', 'completedAt', 'asOf')])
        check('inspection', '巡检记录是否异常', p,
              expr('or', compare('ne', 'planMatches', 1), missing('原结果编码', '完成距今小时'),
                   compare('lt', '完成距今小时', 0), compare('ne', '任务状态编码', '1'),
                   expr('not_in', field('原结果编码'), {'value': ['1', '2']})),
              compare('eq', '原结果编码', '2'), '记录或完成状态不明确，不能判定正常。',
              '最近已完成巡检记录为异常；请核对原实测说明。', '该次人工巡检记录正常，不代表当前实时测量结果。')
    elif category == 'maintenance':
        p = prepare('maintenance_values', '提取保养记录、用量及单位', 'maintenance', [
            number('任务状态编码', 'taskStatus'), number('点位状态编码', 'pointStatus'),
            number('周期天数', 'required_days'), calc('完成距今天数', 'days_between', 'completedAt', 'asOf'),
            calc('区间下限', 'range_lower', 'required_amount'), calc('区间上限', 'range_upper', 'required_amount'),
            number('单值用量', 'required_amount'), calc('下限', 'coalesce', '区间下限', '单值用量'),
            calc('上限', 'coalesce', '区间上限', '单值用量'),
            calc('区间单位', 'range_unit', 'required_amount'), calc('单值单位', 'unit', 'required_amount'),
            calc('要求单位', 'coalesce', '区间单位', '单值单位'), calc('实际单位', 'unit', 'actual_amount'),
            number('实际用量', 'actual_amount'),
            calc('要求换算率', 'lookup', '要求单位', mapping={'kg': 1000, 'Kg': 1000, 'KG': 1000, 'g': 1, 'G': 1}),
            calc('实际换算率', 'lookup', '实际单位', mapping={'kg': 1000, 'Kg': 1000, 'KG': 1000, 'g': 1, 'G': 1}),
            calc('下限克', 'multiply', '下限', '要求换算率'), calc('上限克', 'multiply', '上限', '要求换算率'),
            calc('实际克', 'multiply', '实际用量', '实际换算率')])
        select_rows(p, 'recorded_maintenance', '已有保养记录才展开点位检查', compare('gt', 'recordMatches', 0))
        evidence = expr('or', compare('ne', 'planMatches', 1), compare('ne', 'pointMatches', 1),
                        missing('完成距今天数'), compare('lt', '完成距今天数', 0),
                        compare('ne', '任务状态编码', '1'), compare('ne', '点位状态编码', '1'))
        check('point_cycle', '润滑点是否超过周期', p,
              expr('or', evidence, missing('周期天数'), compare('lte', '周期天数', 0)),
              expr('gt', field('完成距今天数'), field('周期天数')),
              '缺少有效完成记录或润滑周期。', '超过本点润滑周期。', '本点尚未超过润滑周期。')
        for name, title in [('oil', '油品'), ('method', '保养方式')]:
            check(name, title + '是否符合要求', p,
                  expr('or', evidence, missing('required_' + name, 'actual_' + name)),
                  expr('ne', field('required_' + name), field('actual_' + name)),
                  title + '要求或实测证据缺失。', title + '与本点要求不一致。', title + '符合本点要求。')
        check('amount', '加注量是否在要求范围内', p,
              expr('or', evidence, missing('下限克', '上限克', '实际克'), compare('lt', '下限克', 0)),
              expr('or', expr('lt', field('实际克'), field('下限克')), expr('gt', field('实际克'), field('上限克'))),
              '用量缺少明确数值、单位或有效完成记录。', '加注量超出本点要求。', '加注量符合本点要求。')
        b.add('oil_change', 'filter', '筛选独立换油要求', {'expression': expr('present', field('required_oilChangeCycle'))}, p)
        b.output('oil_change_pending', '换油记录待核实', 'pending', '换油要求需独立换油完成记录核实。', 'oil_change')
    else:
        p = prepare('repair_values', '计算维修状态与完成时间', 'repair', [
            number('维修状态', 'status'), number('验收结果', 'acceptance'), number('安装结果', 'installation'),
            calc('完成距今小时', 'hours_between', 'completedAt', 'asOf'),
            calc('超期小时', 'hours_between', 'deadline', 'asOf')])
        select_rows(p, 'actual_repairs', '仅检查实际存在的维修任务', expr('present', field('recordId')))
        done = b.condition('repair_done', '维修是否已完成且时间有效', expr('and', compare('eq', '维修状态', '3'),
            expr('present', field('完成距今小时')), compare('gte', '完成距今小时', 0)), p)
        fail = b.condition('acceptance_failed', '验收或安装是否明确不合格', expr('or', compare('eq', '验收结果', '2'), compare('eq', '安装结果', '2')), done, 'true')
        b.output('acceptance_warning', '输出验收异常', 'warning', '维修已完成，但验收或安装记录不合格。', fail, 'true')
        accepted = b.condition('accepted', '验收是否明确合格', compare('eq', '验收结果', '1'), fail, 'false')
        b.output('repair_normal', '输出已完成并验收', 'normal', '维修已完成且验收合格；不将历史超期当作当前逾期。', accepted, 'true')
        b.output('acceptance_pending', '输出待验收', 'pending', '维修已完成，验收证据仍需核实。', accepted, 'false')
        late = b.condition('repair_late', '维修失败或未完成且超期', expr('or', compare('eq', '维修状态', '4'),
            expr('and', expr('in', field('维修状态'), {'value': ['1', '2']}), expr('present', field('超期小时')), compare('gt', '超期小时', 0))), done, 'false')
        b.output('repair_warning', '输出维修预警', 'warning', '维修失败或未完成任务已超过预计完成时间。', late, 'true')
        b.output('repair_pending', '输出待跟踪', 'pending', '已有维修任务尚未完成，或状态时间不完整，需要继续跟踪。', late, 'false')
    b.add('results', 'merge', '汇总逐项判断结果')
    for key in b.results:
        b.edge(key, 'results')
    b.add('report', 'llm', 'AI 撰写' + label + '报告', {
        'modelProfile': model_profile, 'temperature': 0.1,
        'systemPrompt': '你是设备报告分析员。仅解释本工作流的逐项节点判断，不重新判定规则。按设备汇总正常项，分别列出异常和待核实项、证据与建议。缺数据不能写全部正常。无独立实测不能把人工正常记录写成实时数值合格。没有数据时明确无法判断。输出中文 Markdown 报告。',
        'promptTemplate': '{{ input }}'}, 'results')
    b.add('end', 'end', '保存并展示分析报告', {}, 'report')
    b.layout()
    return {'nodes': b.nodes, 'edges': b.edges}
