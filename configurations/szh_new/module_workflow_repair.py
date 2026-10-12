"""Repair existing module graphs without changing workflow/source identities."""
from copy import deepcopy
import re

from configurations.szh_new.scenario_workflows import quality_graph, device_graph
from configurations.yinhao.graph_builder import GraphBuilder, calc, compare, expr, field, missing


def quality_module_graph(source, entry):
    graph = quality_graph(source, entry['categoryValue'])
    nodes = {n['id']: n for n in graph['nodes']}
    nodes['start']['config'] = {'variables': []}
    # Scope by real product/template relationships, never the project type alone.
    product = entry['productId']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', product):
        raise ValueError('Invalid source product identity')
    forms = nodes['forms']
    fields = forms['config']['fieldAliases']
    forms['kind'] = 'database-query'
    select = ', '.join(f'f.`{column}` AS `{alias}`' for column, alias in fields.items())
    forms['config'] = {
        'datasourceId': source['id'], 'datasourceVersion': source['version'], 'maxRows': 5,
        'parameters': {'product': product, 'category': entry['categoryValue']},
        'query': f'SELECT {select} FROM t_incomingmaterialqcform f '
                 'WHERE f.isDeleted=0 AND f.c_qualityInspection=:category '
                 'AND EXISTS (SELECT 1 FROM t_qualityinspectiontemplate t '
                 'WHERE t.qualityInspectionTemplateId=f.c_qualityInspectionTemplate '
                 'AND t.isDeleted=0 AND t.c_product=:product) '
                 'ORDER BY f.autoId DESC LIMIT 5',
    }
    for node in graph['nodes']:
        if node['kind'] == 'read-values':
            node['config']['maxRows'] = 500
    # Input missingness must remain visible in the aggregate, including unknown
    # major-item flags; it must not disappear as a null sum interpreted as zero.
    ready = nodes['standard_ready']['config']
    ready['expression'] = expr('or', ready['expression'], missing('主要项目系数'))
    calculations = nodes['summary_values']['config']['calculations']
    calculations.insert(0, calc('待确认计数系数', 'coalesce', '主要项目系数', 1))
    next(c for c in calculations if c['name'] == '主要项目待确认')['right']['field'] = '待确认计数系数'
    nodes['summary']['title'] = '汇总 · 主要项目及分类缺失的待确认项'
    return graph


def _scope_devices(graph, keyword):
    nodes = {n['id']: n for n in graph['nodes']}
    nodes['start']['config'] = {'variables': []}
    root = nodes['device']['config']
    root['filters'] = [{'field': 'isDeleted', 'value': 0},
                       {'field': 'c_deviceName', 'operator': 'contains', 'value': keyword}]
    root['maxRows'] = 30
    for node in graph['nodes']:
        if node['kind'] == 'read-values':
            node['config']['maxRows'] = min(node['config']['maxRows'], 500)


def _merge_graphs(source, graphs):
    b = GraphBuilder(source)
    b.add('results', 'merge', '汇总 · 逐设备逐项结论')
    b.add('end', 'end', '结束 · 查看依据和待确认项', parent='results')
    for prefix, graph in graphs:
        for node in graph['nodes']:
            if node['id'] in {'start', 'results', 'end'}:
                continue
            node = deepcopy(node)
            node['id'] = prefix + '_' + node['id']
            b.nodes.append(node)
        for edge in graph['edges']:
            if edge['target'] == 'end':
                continue
            source_id = 'start' if edge['source'] == 'start' else prefix + '_' + edge['source']
            target_id = 'results' if edge['target'] == 'results' else prefix + '_' + edge['target']
            b.edge(source_id, target_id, edge['port'])
    b.layout()
    return {'nodes': b.nodes, 'edges': b.edges}


def inspection_records_graph(source, keyword):
    b = GraphBuilder(source)
    # The detail's device takes priority; a blank detail device can use its
    # parent task. Never join a detail to every device by isDeleted alone.
    read = b.add('read', 'database-query', '读取 · 本设备实际巡检记录及原始要求', {
        'datasourceId': source['id'], 'datasourceVersion': source['version'], 'maxRows': 100,
        'parameters': {'devicePattern': '%' + keyword + '%'},
        'query': '''SELECT dm.deviceManagementId AS deviceKey, dm.c_deviceName AS `设备名称`,
 d.deviceInspectionDetailsId AS `巡检明细`, d.c_inspectionName AS `巡检点`,
 d.c_inspectionIndicators AS `原指标`,d.c_inspectionContent AS `原要求`,
 d.c_inspectionResult AS `原结果`,d.c_inspectionDesc AS `实测说明`,
 m.c_recordTime AS `记录时间`,m.c_xunjianjihua AS `计划名称`
 FROM t_deviceinspectiondetails d
 JOIN t_deviceinspectionmaintenance m ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId AND m.isDeleted=0
 JOIN t_devicemanagement dm ON dm.deviceManagementId=COALESCE(NULLIF(d.c_shebei,''),m.c_deviceId) AND dm.isDeleted=0
 WHERE d.isDeleted=0 AND dm.c_deviceName LIKE :devicePattern
 ORDER BY m.c_recordTime DESC,d.autoId DESC LIMIT 100''',
    }, 'start')
    values = b.calculate('values', '计算 · 原结果编码含义', [
        calc('结果含义', 'lookup', '原结果', mapping={'1': '正常', '2': '异常'}),
    ], read)
    b.check('result', '巡检原记录是否异常', values, missing('结果含义'), compare('eq', '结果含义', '异常'),
            pending='原结果未填写或编码无法识别，不能判为正常；请核对原记录。',
            warning='该设备的原巡检记录为异常。只说明本条记录，不推断当前故障或要求自动停机。',
            normal='该设备本条巡检记录为正常，不代表全部设备或全部历史均正常。')
    return b.finish()


def device_module_graph(source, name):
    if '110kV' in name:
        keyword, category = '主变', 'inspection'
    elif '供电变压器' in name:
        keyword, category = '炉变', 'inspection'
    elif '机器人' in name:
        keyword, category = '机器人', 'maintenance'
    elif '加料机' in name:
        keyword, category = '加料机', 'maintenance'
    elif '液压' in name:
        keyword, category = '液压', 'maintenance'
    elif 'DCS' in name:
        keyword, category = 'DCS', 'inspection'
    else:
        raise ValueError('Unrecognized device entry; explicit scope required')
    primary = device_graph(source, category)
    repair = device_graph(source, 'repair')
    _scope_devices(primary, keyword)
    _scope_devices(repair, keyword)
    return _merge_graphs(source, [('plan', primary), ('repair', repair), ('actual', inspection_records_graph(source, keyword))])
