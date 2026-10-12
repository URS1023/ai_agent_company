"""Read only the rules and device references used by a selected published flow."""
from .database import DataError

GAS_FIELDS = {'0037': ('t_incomingmaterialqcitemdetails', 'c_faqiliang'),
              '0038': ('t_incomingmaterialqcitemdetails', 'c_linhuaqing'),
              '0039': ('t_incomingmaterialqcitemdetails', 'c_liuhuaqing'),
              '0042': ('t_incomingmaterialqcitemdetails2', 'c_faqiliang'),
              '0044': ('t_incomingmaterialqcitemdetails2', 'c_linhuaqing'),
              '0045': ('t_incomingmaterialqcitemdetails2', 'c_liuhuaqing')}


def quality_context(db, flow):
    """为质检类流程定位唯一的模板与检查项规则。

    参数：db 为受控本地连接（见 database.LocalDatabase），flow 为已发布流程摘要（含 parameters/name/category）。
    返回：{'template', 'rules', 'cloneTemplate'}；cloneTemplate 为真表示按名称借用了同类模板。
    异常：同一检测阶段匹配到 0 个或多个模板、检查项为空或重复时抛 DataError——
    这类情况必须人工确认，不能自动挑一个模板继续生成数据。
    """
    parameters = flow['parameters']
    templates = db.query('''SELECT t.qualityInspectionTemplateId id,t.c_templateName name
        FROM t_qualityinspectiontemplate t WHERE t.isDeleted=0 AND t.c_product=%s
        AND t.qualityInspectionTemplateId NOT LIKE 'GUISIM1-%%'
        LIMIT 10''', (parameters['product'],))
    if len(templates) > 1:
        # The same carbide product is used by both stages; keep their templates separate.
        if '炉前' in flow['name']:
            templates = [t for t in templates if '炉前' in t['name']]
        elif '成品电石' in flow['name']:
            templates = [t for t in templates if '成品' in t['name'] or '出厂' in t['name']]
        elif '成品石灰' in flow['name']:
            templates = [t for t in templates if '筛分' in t['name']]
        elif parameters['category'] in (1, '1'):
            templates = [t for t in templates if '质检报告' in t['name']]
    cloned = False
    if not templates:
        source_name = ('筛分楼石灰模板' if '成品石灰' in flow['name'] else
                       '筛分楼焦炭' if '筛分焦炭' in flow['name'] else None)
        if source_name:
            templates = db.query('''SELECT qualityInspectionTemplateId id,c_templateName name
                FROM t_qualityinspectiontemplate WHERE isDeleted=0 AND c_templateName=%s LIMIT 2''', (source_name,))
            cloned = True
    if len(templates) != 1:
        raise DataError(flow['name'] + '：没有唯一匹配本检测阶段的质检模板，无法自动选择')
    template = templates[0]
    rules = db.query('''SELECT d.c_xuanzezhijianxiangmu projectId,d.c_judgeRule op,
        d.c_standardMin low,d.c_standardMax high,d.c_shifouzhuyaozhijianxiangmu main,
        p.c_projectName name,p.c_projectCode code,p.c_inspectionStandard standard
        FROM t_qitemplatedetails d JOIN t_qualityinspectionproject p
        ON p.qualityInspectionProjectId=d.c_xuanzezhijianxiangmu AND p.isDeleted=0
        WHERE d.isDeleted=0 AND d.mdQualityInspectionTemplateId=%s LIMIT 50''', (template['id'],))
    if not rules or len({r['projectId'] for r in rules}) != len(rules):
        raise DataError(flow['name'] + '：检查项目为空或重复')
    return {'template': template, 'rules': rules, 'cloneTemplate': cloned}


def device_context(db, flow):
    """读取流程限定范围内的设备及其保养计划/保养项。

    只支持 c_deviceName contains 筛选（其它筛选方式抛 DataError，避免生成超出流程范围的数据）；
    设备数超出 30 台上限同样报错。维修类流程只需设备列表，巡检/保养类额外带出计划与保养项，
    返回 {'devices'[, 'plans', 'rules']}。
    """
    predicates, parameters = ['isDeleted=0'], []
    for value in flow['filters']:
        if value['field'] == 'isDeleted':
            continue
        if value['field'] != 'c_deviceName' or value.get('operator') != 'contains':
            raise DataError('设备工作流使用了尚未支持的筛选方式')
        predicates.append('LOCATE(%s,c_deviceName)>0')
        parameters.append(value['value'])
    devices = db.query('SELECT deviceManagementId id,c_deviceName name FROM t_devicemanagement WHERE '
                       + ' AND '.join(predicates) + ' LIMIT 31', parameters)
    if not devices or len(devices) > 30:
        raise DataError(flow['name'] + '：设备为空或超过工作流的 30 台范围')
    if flow['kind'] == 'repair':
        return {'devices': devices}
    marker = ','.join(['%s'] * len(devices))
    plans = db.query('''SELECT i.c_device deviceId,p.dInspectionMaintenancePlanId id,p.c_planName name,
        p.c_jihualeixing type,p.c_xunjianzhibiao cycle,p.c_baoyangzhouqi maintenanceCycle
        FROM t_inspectiondevice i JOIN t_dinspectionmaintenanceplan p
        ON p.dInspectionMaintenancePlanId=i.mdDInspectionMaintenancePlanId AND p.isDeleted=0
        WHERE i.isDeleted=0 AND i.c_device IN (''' + marker + ') AND p.c_jihualeixing '
        + ('=2' if flow['kind'] == 'maintenance' else 'IN (1,3,4)') + ' LIMIT 100', [d['id'] for d in devices])
    rules = []
    if plans and flow['kind'] == 'maintenance':
        ids = list({p['id'] for p in plans})
        rules = db.query('''SELECT mdDInspectionMaintenancePlanId planId,c_runhuadianweimingcheng point,
            c_runhuadianweibianhao code,c_runhuazhouqi days,c_runhuayoupaihao oil,
            c_runhualiang amount,c_runhuafangshi method,c_huanyouzhouqi oilChange
            FROM t_xunjianjihuarunhuabaoyang WHERE isDeleted=0 AND mdDInspectionMaintenancePlanId IN ('''
            + ','.join(['%s'] * len(ids)) + ') LIMIT 300', ids)
    return {'devices': devices, 'plans': plans, 'rules': rules}
