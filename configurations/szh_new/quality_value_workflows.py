"""Quality configuration assembled from read/calculate/condition/output nodes."""
from value_workflows import Builder, calc, compare, expr, field, missing, value

SEASON_STANDARD = '夏季≤12.0%\n冬季≤14.0%'
GROUP_FIELDS = ['单据标识', '质检单号', '有效模板', '必检项数', '原检验结果', '原合格状态', '原单据状态']


def constant_calc(name, text):
    return {'name': name, 'operation': 'copy', 'left': {'source': 'constant', 'value': text}}


def build_quality(datasets, source, category):
    b = Builder(datasets, source)
    b.nodes[0]['config'] = {'variables': [{'variable': 'formNumber', 'label': '质检单号（留空检查本类别全部单据）',
                                         'type': 'text-input', 'default': ''}]}
    read = b.read('quality_items', '读取值 · 质检项目、实测值与标准', 0)
    calculations = [calc('距阈值差额', 'subtract', '测量值', '阈值'), calc('距下界差额', 'subtract', '测量值', '下界')]
    for name, text in [('含夏季条件', '夏'), ('含冬季条件', '冬')]:
        calculations.append({'name': name, 'operation': 'contains', 'left': {'source': 'field', 'field': '标准文本'},
                             'right': {'source': 'constant', 'value': text}})
    calculated = b.calculate('item_calc', '计算值 · 实测与阈值的差额', calculations, 'item_missing', 1000, 0)
    b.edges[-1]['port'] = 'false'
    b.edges[-1]['id'] = 'item_missing_false_item_calc'
    season = compare('eq', '标准文本', SEASON_STANDARD)
    lte, gte, between = [compare('eq', '判定方式', code) for code in ('lte', 'gte', 'between_closed')]
    # Complete supported cases, including invalid/missing bounds, are visible.
    invalid_bound = expr('or', missing('距阈值差额'), expr('and', between,
                         expr('or', missing('距下界差额'), expr('gt', field('下界'), field('阈值')))))
    violation = expr('or', expr('and', lte, compare('gt', '距阈值差额', 0)),
                     expr('and', gte, compare('lt', '距阈值差额', 0)),
                     expr('and', between, expr('or', compare('lt', '距下界差额', 0), compare('gt', '距阈值差额', 0))))
    # Split seasonal rows first so common measurements do not traverse every
    # seasonal branch and duplicate large batches in the persisted trace.
    b.node('item_missing', 'condition', '判断值 · 实测或项目是否缺失',
           {'mode':'rows','expression':missing('实测记录','有效项目','测量值')},660,0)
    b.edge(read,'item_missing')
    b.node('item_missing_output','output-values','输出 · 待确认',
           {'conclusion':'pending','message':'缺少必检项实测记录、测量值或有效项目。'},1000,300)
    b.edge('item_missing','item_missing_output','true')
    b.node('season_gate','condition','判断值 · 是否涉及季节标准',{'mode':'rows','expression':
           expr('or',compare('eq','含夏季条件',True),compare('eq','含冬季条件',True))},1330,0)
    b.edge(calculated,'season_gate')
    season_outputs=b.chain('season','season_gate',[
        ('判断值 · 两季上限均满足',expr('and',season,compare('lte','测量值',12)),
         'normal','测量值不超过12%，同时满足源标准的夏季和冬季上限。'),
        ('判断值 · 两季上限均超出',expr('and',season,compare('gt','测量值',14)),
         'warning','测量值超过14%，源标准的夏季和冬季上限均不满足。'),
    ],-650,('pending','待确认','需要确认适用季节标准；不根据月份自行指定夏季或冬季。'))
    unsupported=expr('and',*[compare('ne','判定方式',code) for code in ('lte','gte','between_closed')])
    common_outputs=b.chain('item','season_gate',[
        ('判断值 · 运算与阈值是否有效',expr('or',unsupported,invalid_bound),'pending',
         '判定方式未配置、数值不可计算、阈值缺失或上下界倒置。'),
        ('判断值 · 是否超出本项标准',violation,'warning','实际值不满足该单保存的比较方式与阈值。'),
    ],0,('normal','合格','实测值满足本项结构化标准，包含边界。'))
    for e in b.edges:
        if e['source']=='season_gate':
            e['port']='true' if e['target'].startswith('season_') else 'false'
            e['id']=e['source']+'_'+e['port']+'_'+e['target']
    for n in b.nodes:
        if n['id'].startswith(('season_if','season_output','item_if','item_output')) or n['id'] in ('season_otherwise','item_otherwise'):
            n['position']['x']+=660
    outputs=['item_missing_output',*season_outputs,*common_outputs]
    b.node('item_results', 'merge', '汇总各项目结果', x=3800)
    for out in outputs: b.edge(out, 'item_results')
    b.node('measurements_end', 'end', '输出 · 项目实测与逐项结论', x=4150, y=400)
    b.edge('item_results', 'measurements_end')
    b.node('document_counts', 'aggregator', '计算值 · 按质检单统计结果', {'groupBy': GROUP_FIELDS,
        'metrics': [{'name':'实测项数','operation':'count','field':'实测记录'},
                    {'name':'不合格项数','operation':'sum','field':'不合格标记'},
                    {'name':'待确认项数','operation':'sum','field':'待确认标记'}]}, 4150, 0)
    b.edge('item_results', 'document_counts')
    scope = '仅复判有依据的结构化项目；不代表审批通过或正式放行。'
    prepared = b.calculate('document_scope', '计算值 · 保留复判范围', [constant_calc('复判范围', scope)], 'document_counts', 4470, 0)
    document_outputs = b.chain('document', prepared, [
        ('判断值 · 是否存在不合格项目', compare('gt', '不合格项数', 0), 'warning',
         '存在不合格项目；需决定拒收或让步，正式让步扣重阶梯和审批要求尚待确认，不自动放行。' if category==1 else '存在不合格项目，需按业务流程处置。'),
        ('判断值 · 数据和必检项是否齐全', expr('or', missing('有效模板'), compare('eq', '必检项数', 0),
             compare('eq', '实测项数', 0), compare('gt', '待确认项数', 0)), 'pending', '模板、必检项目、测量值或判定依据尚不完整。'),
    ], 850, ('normal', '结构化项目合格', scope))
    # Keep the document branch after the item branch in the infinite canvas.
    for n in b.nodes:
        if n['id'].startswith('document_if') or n['id'].startswith('document_output') or n['id']=='document_otherwise':
            n['position']['x'] += 3800
    b.node('document_results', 'merge', '汇总单据结论', x=5900, y=850)
    for out in document_outputs: b.edge(out,'document_results')
    b.node('end','end','结束 · 质检单复判结果',x=6230,y=850)
    b.edge('document_results','end')
    # Preserve supplementary raw values; no unsupported thresholds are invented.
    for index,key in enumerate(('gas1','gas2','statistics'),start=1):
        r=b.read('quality_'+key, '读取值 · '+{'gas1':'炉前实测','gas2':'出厂实测','statistics':'统计明细'}[key], 1500+index*250)
        b.node(key+'_end','end','输出 · '+{'gas1':'炉前实测','gas2':'出厂实测','statistics':'统计明细'}[key],x=660,y=1500+index*250)
        b.edge(r,key+'_end')
    for node in b.nodes:
        if node['kind']=='read-values':
            node['config'].update(parameters={'category':category,'formNumber':''}, parameterSources={'category':'fixed','formNumber':'input'})
        if node['kind']=='output-values':
            node['config']['mode']='quality'
            if not node['id'].startswith('document_'):
                node['config']['fields'] = [*GROUP_FIELDS, '实测记录', '检验项目', '标准文本', '测量值', '判定方式', '下界', '阈值']
            node['title']=node['title'].replace('预警','不合格').replace('正常','合格')
    return {'nodes':b.nodes,'edges':b.edges}
