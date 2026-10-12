"""Independent checks for the belt lubrication requirements stored in Yinhao.

Every branch receives the same real rows. Missing dates block only the timing
assessment; missing oil, quantity or method records affect their own checks.
The graph uses ordinary, editable value/calculation/condition/output nodes.
"""
from copy import deepcopy

from value_workflows import calc, compare, expr, field, missing


def rebuild_belt_maintenance(graph):
    graph = deepcopy(graph)
    old = {n['id']: n for n in graph['nodes']}
    read = old['maintenance_read']
    removed = {key for key in old if key.startswith('maintenance_') and key != 'maintenance_read'}
    # Re-applying replaces only nodes owned by this four-check configuration.
    removed |= {key for key in old if key.startswith('maintcheck_')}
    graph['nodes'] = [n for n in graph['nodes'] if n['id'] not in removed]
    graph['edges'] = [e for e in graph['edges'] if e['source'] not in removed and e['target'] not in removed]
    nodes, edges = graph['nodes'], graph['edges']
    x = read['position']['x'] + 340
    y = read['position']['y']

    def node(key, kind, title, config, dx, dy):
        key = 'maintcheck_' + key
        nodes.append({'id': key, 'kind': kind, 'title': title, 'config': config,
                      'inputs': {}, 'position': {'x': x + dx, 'y': y + dy}})
        return key

    def edge(a, b, port='out'):
        edges.append({'id': a + '_' + port + '_' + b, 'source': a, 'target': b,
                      'port': port, 'sourceSide': 'right', 'targetSide': 'left'})

    def copy_value(name, value):
        return {'name': name, 'operation': 'copy', 'left': {'source': 'constant', 'value': value}}

    def branch(key, label, requirement, actual, calculations, checks, final, row):
        top = row * 500
        prepare = node(key + '_values', 'calculate-values', '计算值 · ' + label,
                       {'calculations': [copy_value('检查项目', label), calc('检查要求', 'copy', requirement),
                                         calc('实测值', 'copy', actual), *calculations]}, 0, top)
        edge(read['id'], prepare)
        parent = prepare
        for index, (title, expression, conclusion, message) in enumerate(checks):
            condition = node(f'{key}_if_{index}', 'condition', '判断值 · ' + title,
                             {'mode': 'rows', 'expression': expression}, 340 + index * 340, top)
            edge(parent, condition, 'false' if index else 'out')
            output = node(f'{key}_result_{index}', 'output-values', '输出 · ' + label + ' · ' + {'pending': '待确认', 'warning': '预警'}[conclusion],
                          {'conclusion': conclusion, 'message': message}, 340 + index * 340, top + 220)
            edge(condition, output, 'true'); edge(output, 'results')
            parent = condition
        output = node(key + '_normal', 'output-values', '输出 · ' + label + ' · 正常',
                      {'conclusion': 'normal', 'message': final}, 340 + len(checks) * 340, top)
        edge(parent, output, 'false'); edge(output, 'results')

    evidence = [
        calc('主任务完成状态', 'lookup', '任务状态', mapping={'1': '已完成', '2': '未完成'}),
        calc('点位完成状态', 'lookup', '点位状态', mapping={'1': '已完成', '2': '未完成'}),
        calc('完成距参考天数', 'days_between', '本次完成时间', '参考时间'),
    ]
    incomplete = expr('or', compare('ne', '同名计划数', 1), missing('完成距参考天数'),
                      compare('lt', '完成距参考天数', 0),
                      compare('ne', '主任务完成状态', '已完成'), compare('ne', '点位完成状态', '已完成'))

    branch('cycle', '保养超期', '周期天数', '上次完成时间', [
        calc('已过天数', 'days_between', '上次完成时间', '参考时间'), calc('周期数值', 'number', '周期天数'),
    ], [
        ('周期是否缺失或无效', expr('or', missing('周期数值'), compare('lte', '周期数值', 0)),
         'pending', '源计划的保养周期缺失或不大于零，无法判断是否超期；其他检查继续执行。'),
        ('保养时间是否缺失或无效', expr('or', missing('已过天数'), compare('lt', '已过天数', 0), compare('ne', '同名计划数', 1)),
         'pending', '未读到可关联的保养完成时间，或时间晚于参考时间，无法判断是否超期；其他检查继续执行。'),
        ('是否超过本点保养周期', expr('gt', field('已过天数'), field('周期数值')),
         'warning', '距上次完成保养的天数超过源计划中该润滑点的周期。'),
    ], '截至参考时间，距上次完成保养的天数未超过源计划周期。', 0)

    for row, key, label, requirement, actual in [
        (1, 'oil', '润滑油品', '要求油品', '记录油品'),
        (2, 'method', '保养方式', '要求方式', '记录方式'),
    ]:
        branch(key, label, requirement, actual, evidence, [
            (label + '要求或完成记录是否缺失', expr('or', missing(requirement, actual), incomplete),
             'pending', f'无法核对{label}：请查看本行源库要求和实测值；未读到对应实测值或已完成的保养记录。'),
            (label + '是否不符合要求', expr('ne', field(actual), field(requirement)),
             'warning', f'完成记录中的{label}与本润滑点在源计划中的要求不一致。'),
        ], f'完成记录中的{label}与本润滑点在源计划中的要求一致。', row)

    units = {'kg': 1, 'Kg': 1, 'KG': 1, 'g': '0.001', 'G': '0.001'}
    branch('amount', '润滑用量', '要求用量', '记录用量', [
        *evidence, calc('要求用量下限', 'range_lower', '要求用量'), calc('要求用量上限', 'range_upper', '要求用量'),
        calc('要求用量单位', 'range_unit', '要求用量'), calc('要求单位换算率', 'lookup', '要求用量单位', mapping=units),
        calc('下限千克', 'multiply', '要求用量下限', '要求单位换算率'),
        calc('上限千克', 'multiply', '要求用量上限', '要求单位换算率'),
        calc('实际用量数值', 'number', '记录用量'), calc('实际用量单位', 'unit', '记录用量'),
        calc('实际单位换算率', 'lookup', '实际用量单位', mapping=units),
        calc('实际用量千克', 'multiply', '实际用量数值', '实际单位换算率'),
    ], [
        ('用量范围是否缺失或不明确', expr('or', missing('下限千克', '上限千克'), compare('lt', '下限千克', 0)),
         'pending', '源库用量要求没有可自动核对的明确数值区间及质量单位，保留原要求供人工确认。'),
        ('实际用量是否缺失或无法核对', expr('or', missing('实际用量千克'), incomplete),
         'pending', '未读到已完成保养的实际用量，或实际值没有明确的 kg/g 单位；不能把要求区间当成实际加注量。'),
        ('实际用量是否超出范围', expr('or', expr('lt', field('实际用量千克'), field('下限千克')),
                                        expr('gt', field('实际用量千克'), field('上限千克'))),
         'warning', '实际润滑用量超出源计划要求的区间；比较时已统一 kg/g 单位。'),
    ], '实际润滑用量位于源计划要求的区间内，且有对应的已完成保养记录。', 3)

    right = max(n['position']['x'] for n in nodes if n['id'].startswith('maintcheck_')) + 350
    for offset, key in enumerate(('results', 'report', 'end')):
        if key in old:
            next(n for n in nodes if n['id'] == key)['position'] = {'x': right + offset * 330, 'y': y + 750}
    return graph
