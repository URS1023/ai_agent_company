"""Six inspectable business graphs, using table/field selection only.

No connections, mutations, SQL execution or passwords are present in this module.
Each record supplies its own plan/template; rules are not shared fixed thresholds.
"""
from configurations.yinhao.graph_builder import GraphBuilder, calc, compare, expr, field, missing
from configurations.yinhao.device_graphs import build_device
from configurations.szh_new.scenario_rules import standard_conditions


NAMES = {'inspection': '设备巡检', 'maintenance': '设备保养', 'repair': '设备维修',
         'incoming': '进场原（辅）料质检', 'process': '过程控制', 'finished': '产品检验'}


def quality_graph(source, category):
    b = GraphBuilder(source, quality=True)
    b.nodes[0]['config'] = {'variables': [
        {'variable': 'formNumber', 'label': '质检单号', 'type': 'text-input', 'required': True,
         'hint': '读取这张质检单的全部项目和样本；判断条件优先采用单据保存的标准，避免套用后来修改的模板。'}]}
    forms = b.read('forms', '读取值 · 质检单及业务分类', 't_incomingmaterialqcform', {
        'incomingMaterialQcFormId': '单据标识', 'c_incomingQcNo': '质检单号',
        'c_qualityInspectionTemplate': '模板标识', 'c_testResults': '原检验结果',
        'c_zhijianshifouhege': '原合格状态', 'c_samplingTime': '取样时间',
        'c_zhijiandanzhuangtai': '原单据状态'}, filters=[('c_qualityInspection', 'eq', category)])
    b.nodes[-1]['config']['filters'].append({'field': 'c_incomingQcNo', 'operator': 'eq',
        'valueSource': 'input', 'inputField': 'formNumber'})
    template = b.read('template', '读取值 · 本单模板', 't_qualityinspectiontemplate', {
        'qualityInspectionTemplateId': '有效模板', 'c_templateName': '模板名称', 'c_product': '产品标识'},
        forms, matches={'qualityInspectionTemplateId': '模板标识'}, mode='unique')
    req = b.read('requirements', '读取值 · 本模板项目要求', 't_qitemplatedetails', {
        'qITemplateDetailsId': '要求标识', 'c_xuanzezhijianxiangmu': '项目标识',
        'c_judgeRule': '模板判断方式', 'c_standardMin': '模板下界', 'c_standardMax': '模板阈值',
        'c_shifouzhuyaozhijianxiangmu': '模板主要项目'}, template,
        matches={'mdQualityInspectionTemplateId': '模板标识'})
    projects = b.read('projects', '读取值 · 项目名称和标准原文', 't_qualityinspectionproject', {
        'qualityInspectionProjectId': '有效项目', 'c_projectCode': '项目编码',
        'c_projectName': '检验项目', 'c_inspectionStandard': '标准原文',
        'c_inspectionRequirement': '要求原文'}, req,
        matches={'qualityInspectionProjectId': '项目标识'}, mode='unique')
    measured = b.read('measurements', '读取值 · 本单实测及当时判断条件', 't_qualityinspectionitemdetails', {
        'qualityInspectionItemDetailsId': '实测记录', 'c_jianyanjieguo': '单项实测值',
        'c_judgeRule': '单据判断方式', 'c_standardMin': '单据下界', 'c_standardMax': '单据阈值',
        'c_shifoushizhuyaopanduanbiaozhun': '单据主要项目'}, projects,
        matches={'mdIncomingMaterialQcFormId': '单据标识', 'c_qualityInspectionProject': '项目标识'})
    # Keep the rule/min/max/major flag from the same source. Partial overrides
    # must not combine an old rule with a newly changed template threshold.
    saved = b.condition('saved_standard', '判断值 · 是否有单据实测记录',
        expr('present', field('实测记录')), measured)
    tails = []
    for key, prefix, port in [('saved', '单据', 'true'), ('template', '模板', 'false')]:
        tails.append(b.calculate('standard_' + key, '计算值 · 采用' + prefix + '完整判断条件', [
            calc('判定方式', 'copy', prefix + '判断方式'), calc('下界', 'copy', prefix + '下界'),
            calc('阈值', 'copy', prefix + '阈值'), calc('主要项目', 'copy', prefix + '主要项目'),
            calc('判断条件来源', 'copy', prefix, constant=True)], saved, port))
    selected = b.add('selected_standard', 'merge', '汇合 · 保留对应判断依据')
    for tail in tails: b.edge(tail, selected)
    # Wide statistics are the source system's own project-code mapping.
    codes = [*[f'{i:04d}' for i in range(1, 19)], '0052', '0053', '0054', '0055', '0056']
    stats = b.read('statistics', '读取值 · 本单统计测量', 't_zhijianxiangmutongjimingxi', {
        'zhijianxiangmutongjimingxiId': '统计记录', **{f'c_n{code}': '统计_' + code for code in codes}},
        selected, matches={'mdIncomingMaterialQcFormId': '单据标识'}, mode='unique')
    gas_codes = {'0037': '气量', '0038': '磷化氢', '0039': '硫化氢',
                 '0042': '气量', '0044': '磷化氢', '0045': '硫化氢'}
    gas_gate = b.condition('gas_project', '判断值 · 是否气体样本项目',
        expr('or', *[compare('eq', '项目编码', code) for code in gas_codes]), stats)
    plain = b.calculate('plain_value', '计算值 · 单项或统计实测', [
        calc('统计项目值', 'mapped_field', '项目编码', mapping={code: '统计_' + code for code in codes}),
        calc('测量值', 'coalesce', '单项实测值', '统计项目值')], gas_gate, 'false')
    gas_kind = b.condition('gas_kind', '判断值 · 炉前或出厂项目',
        expr('or', *[compare('eq', '项目编码', code) for code in ['0037', '0038', '0039']]), gas_gate, 'true')
    gas_tails = []
    for key, table, pk, port in [('before', 't_incomingmaterialqcitemdetails', 'incomingMaterialQcItemDetailsId', 'true'),
                                ('after', 't_incomingmaterialqcitemdetails2', 'incomingMaterialQcItemDetails2Id', 'false')]:
        gas = b.read('gas_' + key, '读取值 · ' + ('炉前' if key == 'before' else '出厂') + '逐次样本', table, {
            pk: '样本标识', 'c_timePoint': '检测时间', 'c_luyanshu': '炉眼',
            'c_faqiliang': '气量', 'c_linhuaqing': '磷化氢', 'c_liuhuaqing': '硫化氢',
            'c_jieguo': '原样本等级'}, gas_kind, port=port,
            matches={'mdIncomingMaterialQcFormId': '单据标识'})
        gas_tails.append(b.calculate('gas_value_' + key, '计算值 · 按项目选择本次样本值', [
            calc('测量值', 'mapped_field', '项目编码', mapping=gas_codes)], gas))
    measured_all = b.add('all_measurements', 'merge', '汇合 · 每项每次独立测量')
    for tail in [plain, *gas_tails]: b.edge(tail, measured_all)
    numeric = b.calculate('numeric', '计算值 · 实测与上下限', [
        calc('测量数值', 'number', '测量值'), calc('下限数值', 'number', '下界'),
        calc('上限数值', 'number', '阈值'),
        calc('主要项目系数', 'lookup', '主要项目', mapping={'True': 1, '1': 1, 'true': 1, 'False': 0, '0': 0, 'false': 0})], measured_all)
    invalid, violation = standard_conditions()
    b.check('standard', '实测是否符合本项要求', numeric,
        expr('or', invalid, missing('有效模板', '有效项目')), violation,
        pending='本项缺少明确实测、有效关联或完整判断条件；保留原标准、原结果和缺失字段，不按合格处理。',
        warning='实测不满足本项目保存的比较条件。', normal='实测满足本项目保存的比较条件；不代替审批放行。')
    graph = b.finish()
    # Detail results retain secondary items, while the MES overall result only
    # uses primary items. Missing classification remains explicitly unresolved.
    b.add('summary_values', 'calculate-values', '计算值 · 区分主要项目与参考项目', {'calculations': [
        calc('主要项目不合格', 'multiply', '不合格标记', '主要项目系数'),
        calc('主要项目待确认', 'multiply', '待确认标记', '主要项目系数')]}, 'results')
    b.add('summary', 'aggregator', '汇总 · 主要项目判定数量', {
        'groupBy': ['单据标识', '质检单号', '原检验结果', '原合格状态'],
        'metrics': [{'name': '检查项次数', 'operation': 'count', 'field': ''},
                    {'name': '主要不合格次数', 'operation': 'sum', 'field': '主要项目不合格'},
                    {'name': '主要待确认次数', 'operation': 'sum', 'field': '主要项目待确认'}]}, 'summary_values')
    b.add('summary_end', 'end', '结束 · 单据汇总', parent='summary')
    b.layout()
    return graph


def device_graph(source, category):
    types = {'inspection': [1, 3, 4], 'maintenance': [2], 'repair': []}[category]
    graph = build_device(source, {'deviceManagementId': '__selected_device__'},
        [{'c_jihualeixing': kind} for kind in types], repair_enabled=category == 'repair')
    nodes = {node['id']: node for node in graph['nodes']}
    nodes['start']['config'] = {'variables': [{'variable': 'deviceKey', 'label': '源库设备标识',
        'type': 'text-input', 'required': True, 'hint': '使用设备台账记录标识；本流程按该设备自己的计划、周期和记录检查。'}]}
    for node in graph['nodes']:
        if node['kind'] != 'read-values': continue
        config = node['config']
        for rule in list(config['filters']):
            if rule.get('value') != '__selected_device__': continue
            if node['id'] == 'device':
                rule.pop('value'); rule.update(valueSource='input', inputField='deviceKey')
            else:
                config['filters'].remove(rule)
                config['matches'].append({'field': rule['field'], 'inputField': 'deviceKey'})
        config['maxRows'] = 1000
    if types:
        nodes['plans']['config']['filters'].append({'field': 'c_jihualeixing', 'operator': 'eq' if category == 'maintenance' else 'ne', 'value': 2})
        # A device can have both inspection and maintenance plans. An excluded
        # plan is not missing data for the other category.
        graph['nodes'].append({'id': 'applicable_plans', 'kind': 'filter', 'title': '筛选 · 本类有效计划',
            'config': {'expression': expr('present', field('有效计划'))}, 'inputs': {}, 'position': {'x': 0, 'y': 0}})
        for edge in graph['edges']:
            if edge['source'] == 'plans' and edge['target'] == 'clock':
                edge['source'] = 'applicable_plans'; edge['id'] = 'applicable_plans_out_clock'
        graph['edges'].append({'id': 'plans_out_applicable_plans', 'source': 'plans', 'target': 'applicable_plans',
            'port': 'out', 'sourceSide': 'right', 'targetSide': 'left'})
    if category == 'maintenance':
        calculations = nodes['maintenance_values']['config']['calculations']
        for item in calculations:
            if item['name'] == '计划保养到期时间':
                item['left']['field'] = '计划日历到期时间'
                item['right']['field'] = '计划小时到期时间'
        index = next(i for i, item in enumerate(calculations) if item['name'] == '计划小时到期时间')
        calculations[index:index] = [calc('明确小时周期', 'number', '保养小时数'),
            calc('采用小时周期', 'coalesce', '计划间隔小时', '明确小时周期')]
        next(item for item in calculations if item['name'] == '计划小时到期时间')['right']['field'] = '采用小时周期'
        # "半个月" has no machine-readable day/calendar convention in the
        # inspected source. Do not silently assert 15 days or 0.5 months.
    if category == 'repair':
        b = GraphBuilder(source)
        b.nodes, b.edges = graph['nodes'], graph['edges']
        requirements = b.read('repair_requirements', '读取值 · 本设备维修计划明细', 't_equipmentselfrepairdetails', {
            'equipmentselfrepairDetailsId': '维修计划明细', 'mdEquipmentselfrepairId': '维修计划标识',
            'c_jianxiuxiangmumingcheng': '计划检修项目', 'c_jianxiuneirong': '计划检修内容',
            'c_yujiwanchengshijian': '计划预计完成时间'}, 'device', matches={'c_shebei': 'deviceKey'})
        exists = b.add('existing_repair_plans', 'filter', '筛选 · 已有维修计划',
            {'expression': expr('present', field('维修计划明细'))}, requirements)
        plan = b.read('repair_plan', '读取值 · 自行外出委外维修要求', 't_equipmentselfrepair', {
            'equipmentselfrepairId': '有效维修计划', 'c_jihuadanmingcheng': '维修计划名称',
            'c_weixiuleixing': '计划维修类型', 'c_cunzaiwentiyijixiufuyaoqiu': '修复加工要求'}, exists,
            matches={'equipmentselfrepairId': '维修计划标识'}, mode='unique')
        summary = b.read('repair_summary_link', '读取值 · 计划对应汇总单', 't_plandetailinmaintenance', {
            'planDetailInMaintenanceId': '维修汇总明细', 'mdSelfMaintenanceSummaryTableId': '维修汇总标识'}, plan,
            matches={'c_weixiujihua': '维修计划标识', 'c_shebei': 'deviceKey'}, mode='unique')
        execution = b.read('repair_plan_execution', '读取值 · 汇总单中本设备实际执行', 't_equipmentmaintenancelog', {
            'equipmentMaintenanceLogId': '计划对应执行记录', 'c_weixiuzhuangtai': '计划执行状态',
            'c_shijichuliwanchengshijian': '计划实际完成时间'}, summary,
            matches={'c_weixiujihuamingcheng': '维修汇总标识', 'c_zhubiaoshebei': 'deviceKey'}, mode='unique')
        values = b.calculate('repair_plan_time', '计算值 · 维修计划期限', [
            calc('计划检查时间', 'now'), calc('计划逾期小时', 'hours_between', '计划预计完成时间', '计划检查时间')], execution)
        b.check('repair_plan_due', '维修计划是否逾期', values,
            expr('or', missing('有效维修计划', '计划对应执行记录', '计划逾期小时'),
                 expr('not', expr('or', *[compare('eq', '计划执行状态', value) for value in [1, 2, 3, 4]]))),
            expr('and', compare('ne', '计划执行状态', 3), compare('gt', '计划逾期小时', 0)),
            pending='已读取维修计划；缺少唯一的计划—汇总单—本设备执行关联或预计完成时间，不能把其他维修日志当成本计划完成。',
            warning='本设备对应维修计划尚未完成，且已超过计划预计完成时间。',
            normal='本计划已完成或未超过计划预计完成期限；日志验收由另一分支单独检查。')
        for tail in b.results: b.edge(tail, 'results')
    layout = GraphBuilder(source)
    layout.nodes, layout.edges = graph['nodes'], graph['edges']
    layout.layout()
    return graph


def build_six(source):
    return {key: {'name': name, 'domains': ['quality' if key in {'incoming', 'process', 'finished'} else 'alert'],
        'description': '按实际业务系统要求，读取本地 szh_new 的记录及对应标准；普通读取、计算、判断、输出节点，可在画布中编辑。',
        'graph': quality_graph(source, {'incoming': 1, 'process': 2, 'finished': 3}[key])
        if key in {'incoming', 'process', 'finished'} else device_graph(source, key)} for key, name in NAMES.items()}
