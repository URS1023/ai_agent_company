"""Build inspectable graphs from generic nodes, without Python/SQL nodes.

This file assembles saved configuration only; it is not a workflow executor.
Each comparison, operand and branch is editable in the ordinary node panel.
"""
from copy import deepcopy

AS_OF = '2026-09-02 13:53:59'


def field(name):
    return {'field': name}


def value(v):
    return {'value': v}


def expr(op, *args):
    return {'op': op, 'args': list(args)}


def compare(op, name, v):
    return expr(op, field(name), value(v))


def missing(*names):
    return expr('or', *[expr('missing', field(name)) for name in names])


def calc(name, operation, left, right=None, mapping=None):
    item = {'name': name, 'operation': operation, 'left': {'source': 'field', 'field': left}}
    if right is not None:
        item['right'] = {'source': 'field', 'field': right} if isinstance(right, str) else {'source': 'constant', 'value': right}
    if mapping is not None:
        item['mapping'] = mapping
    return item


class Builder:
    def __init__(self, datasets, source, device_id=None):
        self.datasets, self.source, self.device_id = datasets, source, device_id
        self.nodes, self.edges = [], []
        self.node('start', 'start', '开始', {'variables': [
            {'variable': 'asOf', 'label': '参考时间（导入副本）', 'type': 'text-input', 'required': True, 'default': AS_OF}]}, x=0, y=0)

    def node(self, key, kind, title, config=None, x=0, y=0):
        self.nodes.append({'id': key, 'kind': kind, 'title': title, 'config': config or {},
                           'inputs': {}, 'position': {'x': x, 'y': y}})
        return key

    def edge(self, a, b, port='out'):
        self.edges.append({'id': a + '_' + port + '_' + b, 'source': a, 'target': b, 'port': port})

    def read(self, dataset, title, y):
        selected = self.datasets[dataset]
        key = dataset + '_read'
        self.node(key, 'read-values', title, {'datasourceId': self.source['id'],
            'datasourceVersion': self.source['version'], 'templateId': selected['id'], 'templateVersion': selected['version'],
            'deviceId': self.device_id, 'fields': [c['name'] for c in selected['parameters']['columns']],
            'parameters': {'asOf': AS_OF, 'deviceKey': ''}}, x=320, y=y)
        self.edge('start', key)
        return key

    def calculate(self, key, title, calculations, parent, x, y):
        self.node(key, 'calculate-values', title, {'calculations': calculations}, x, y)
        self.edge(parent, key)
        return key

    def chain(self, prefix, parent, decisions, y, final=('normal', '正常', '')):
        """Each true branch outputs; false continues to the next visible test."""
        outputs = []
        for index, (title, expression, status, message) in enumerate(decisions):
            key, out = f'{prefix}_if_{index}', f'{prefix}_output_{index}'
            x = 1000 + index * 330
            self.node(key, 'condition', title, {'mode': 'rows', 'expression': expression}, x, y)
            self.edge(parent, key, 'false' if index else 'out')
            self.node(out, 'output-values', '输出 · ' + {'normal': '正常', 'warning': '预警', 'pending': '待确认'}[status],
                      {'conclusion': status, 'message': message}, x, y + 230)
            self.edge(key, out, 'true')
            outputs.append(out)
            parent = key
        out = prefix + '_otherwise'
        self.node(out, 'output-values', '输出 · ' + final[1], {'conclusion': final[0], 'message': final[2]}, 1000 + len(decisions) * 330, y)
        self.edge(parent, out, 'false' if decisions else 'out')
        return outputs + [out]

    def finish(self, outputs, policy=None):
        right = max(n['position']['x'] for n in self.nodes) + 350
        self.node('results', 'merge', '汇总输出', x=right, y=0)
        for output in outputs:
            self.edge(output, 'results')
        tail = 'results'
        if policy:
            self.node('report', 'alert-evaluate', '保存预警结果', {'deviceId': self.device_id, 'ruleId': policy['id'],
                       'ruleRevision': policy['version']}, right + 330, 0)
            self.edge(tail, 'report')
            tail = 'report'
        self.node('end', 'end', '结束', x=right + 660, y=0)
        self.edge(tail, 'end')
        return {'nodes': self.nodes, 'edges': self.edges}


def maintenance(b, y=0):
    read = b.read('maintenance', '读取值 · 本设备润滑点、周期和时间', y)
    calculated = b.calculate('maintenance_calc', '计算值 · 距上次保养天数', [
        calc('已过天数', 'days_between', '上次完成时间', '参考时间'),
        calc('周期数值', 'number', '周期天数')], read, 660, y)
    return b.chain('maintenance', calculated, [
        ('判断值 · 时间和周期是否缺失', expr('or', missing('已过天数', '周期数值'), compare('ne', '同名计划数', 1)),
         'pending', '缺少可关联的完成记录或周期，不能计算是否逾期。'),
        ('判断值 · 周期或时间是否无效', expr('or', compare('lte', '周期数值', 0), compare('lt', '已过天数', 0)),
         'pending', '周期必须大于零，完成时间不能晚于参考时间。'),
        ('判断值 · 已过天数超过本点周期', expr('gt', field('已过天数'), field('周期数值')),
         'warning', '超过此润滑点在源计划中配置的保养周期。'),
        ('判断值 · 油品或方式是否不一致', expr('or',
             expr('and', expr('present', field('记录油品')), expr('present', field('要求油品')), expr('ne', field('记录油品'), field('要求油品'))),
             expr('and', expr('present', field('记录方式')), expr('present', field('要求方式')), expr('ne', field('记录方式'), field('要求方式')))),
         'warning', '保养记录的油品或执行方式与本点要求不一致。'),
    ], y, ('pending', '待确认', '未发现周期超期；油品、用量、方式可能为复制要求，实际执行证据及换油日期仍需确认。'))


def inspection(b, qualitative, y=0):
    read = b.read('cycle', '读取值 · 巡检周期和上次时间', y)
    calculated = b.calculate('cycle_calc', '计算值 · 巡检间隔小时', [
        calc('已过小时', 'hours_between', '上次完成时间', '参考时间'),
        calc('周期小时', 'lookup', '周期编码', mapping={'1': 2, '2': 4, '3': 8, '4': 12, '5': 24, '6': 168}),
    ], read, 660, y)
    outputs = b.chain('cycle', calculated, [
        ('判断值 · 周期和时间是否有效', expr('or', missing('已过小时', '周期小时'), compare('ne', '同名计划数', 1), compare('lt', '已过小时', 0)),
         'pending', '巡检时间或周期不可计算；月、季度、年周期需配置日历计算。'),
        ('判断值 · 间隔超过本设备周期', expr('gt', field('已过小时'), field('周期小时')),
         'warning', '距上次完成巡检的时间已超过本设备计划周期。'),
    ], y, ('normal', '正常', '尚未超过本设备巡检周期。'))
    y += 600
    read = b.read('inspection', '读取值 · 本设备检查要求和实际值', y)
    calculated = b.calculate('inspection_calc', '计算值 · 实测数值与要求下限', [
        calc('数值下限', 'lower_limit', '要求'), calc('测量数值', 'number', '实际值'), calc('实际单位', 'unit', '实际值'),
        calc('要求单位', 'lookup', '要求', mapping={'21V以上': 'V', '在10°C以上': '°C'}),
        calc('定性要求', 'lookup', '要求', mapping={text: 1 for text in qualitative}),
        calc('定性指标', 'lookup', '指标', mapping={'正常': 1, '无': 1, '无变色': 1}),
        calc('记录距今小时', 'hours_between', '记录时间', '参考时间'),
    ], read, 660, y)
    has_lower = expr('present', field('数值下限'))
    outputs += b.chain('inspection', calculated, [
        ('判断值 · 项目能否唯一关联', expr('or', compare('ne', '同名计划数', 1), compare('ne', '匹配记录数', 1)),
         'pending', '最新任务中此检查项缺失或关联不唯一。'),
        ('判断值 · 原记录是否异常', compare('eq', '检查结果', 2), 'warning', '原系统明确记录此项检查异常。'),
        ('判断值 · 巡检是否完成', expr('or', compare('ne', '任务状态', 1), missing('记录距今小时'), compare('lt', '记录距今小时', 0)),
         'pending', '本次巡检尚未完成或缺少有效记录时间。'),
        ('判断值 · 使用的要求是否一致', expr('and', expr('present', field('要求')), compare('ne', '要求', ''), expr('ne', field('要求'), field('记录要求'))),
         'pending', '本次记录的要求与当前源计划不同，需核对标准版本。'),
        ('判断值 · 数值和单位是否齐全', expr('and', has_lower, expr('or', missing('测量数值', '要求单位'), expr('ne', field('实际单位'), field('要求单位')))),
         'pending', '有数值下限，但实际测量值或单位缺失、不匹配；不能把要求中的数字当实测值。'),
        ('判断值 · 实测是否低于下限', expr('and', has_lower, expr('lt', field('测量数值'), field('数值下限'))),
         'warning', '实际测量值低于此检查项的数值下限。'),
        ('判断值 · 实测是否达到下限', has_lower, 'normal', '实际测量数值达到本项下限，包含边界。'),
        ('判断值 · 定性项目是否人工确认', expr('and', compare('eq', '检查结果', 1), expr('or', compare('eq', '定性要求', 1),
             expr('and', expr('or', missing('要求'), compare('eq', '要求', '')), compare('eq', '定性指标', 1)))),
         'normal', '原系统按相同定性要求人工确认正常；并非自动测量。'),
    ], y, ('pending', '待确认', '缺少明确数值标准或独立实测值；复合文字要求需逐项确认。'))
    return outputs


def repair(b, y=0):
    read = b.read('repair', '读取值 · 维修期限、状态和验收', y)
    calculated = b.calculate('repair_calc', '计算值 · 距期限和完成时间', [
        calc('超期小时', 'hours_between', '预计完成时间', '参考时间'),
        calc('完成距今小时', 'hours_between', '实际完成时间', '参考时间'),
        calc('完成时超期小时', 'hours_between', '预计完成时间', '实际完成时间'),
    ], read, 660, y)
    completed = compare('eq', '维修状态', 3)
    return b.chain('repair', calculated, [
        ('判断值 · 维修是否失败', compare('eq', '维修状态', 4), 'warning', '原维修任务明确标记维修失败。'),
        ('判断值 · 完成日期是否有效', expr('and', completed, expr('or', missing('完成距今小时'), compare('lt', '完成距今小时', 0))),
         'pending', '任务标记已完成，但实际完成日期缺失或晚于参考时间。'),
        ('判断值 · 验收或安装是否不合格', expr('and', completed, expr('or', compare('eq', '验收状态', 2), compare('eq', '安装状态', 2))),
         'warning', '已完成任务仍记录验收不合格或安装未完成。'),
        ('判断值 · 完成后验收是否缺失', expr('and', completed, compare('ne', '验收状态', 1)),
         'pending', '维修已完成，但验收结果未记录；需确认验收适用性。历史超期不计当前逾期。'),
        ('判断值 · 是否已完成并验收', completed, 'normal', '维修已完成且验收合格，历史超期不计当前逾期。'),
        ('判断值 · 状态和期限是否可判断', expr('or', missing('超期小时'), expr('and', compare('ne', '维修状态', 1), compare('ne', '维修状态', 2))),
         'pending', '缺少有效维修状态或预计完成时间。'),
        ('判断值 · 未完成任务是否逾期', compare('gt', '超期小时', 0), 'warning', '维修尚未完成且已超过此任务预计完成时间。'),
    ], y, ('normal', '正常', '维修任务尚未到期。'))


def build(datasets, source, scenes, qualitative=(), device_id=None, policy=None):
    b = Builder(datasets, source, device_id)
    outputs, y = [], 0
    for scene in scenes:
        outputs += inspection(b, qualitative, y) if scene == 'inspection' else maintenance(b, y) if scene == 'maintenance' else repair(b, y)
        y += 1200 if scene == 'inspection' else 600
    return b.finish(outputs, policy)
