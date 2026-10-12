"""Device-specific workflows selected only from explicit source plans/repair tasks."""
from .graph_builder import GraphBuilder, calc, compare, expr, field, missing

PLAN_FIELDS={'dInspectionMaintenancePlanId':'有效计划','c_planName':'计划名称','c_jihualeixing':'计划类型',
             'c_xunjianzhibiao':'巡检周期编码','c_baoyangzhouqi':'保养周期编码','c_xiaoshi':'保养小时数'}


def device_name(device):
    if device.get('c_deviceName'): return device['c_deviceName']
    pipeline=' · '.join(str(device[k]).strip() for k in ('c_pipelineName','c_pipelineNumber','c_remark') if device.get(k))
    return pipeline or '未命名设备 · '+device['deviceManagementId']


def build_device(source, device, plans, repair_enabled=False, report=None):
    b=GraphBuilder(source)
    key=device['deviceManagementId']
    root=b.read('device','读取值 · 当前设备','t_devicemanagement',
                {'deviceManagementId':'deviceKey','c_deviceName':'设备名称'},filters=[('deviceManagementId','eq',key)])
    if plans:
        members=b.read('members','读取值 · 本设备计划关联','t_inspectiondevice',
                       {'mdDInspectionMaintenancePlanId':'planId'},root,matches={'c_device':'deviceKey'})
        joined=b.read('plans','读取值 · 计划周期与类型','t_dinspectionmaintenanceplan',PLAN_FIELDS,members,
                      matches={'dInspectionMaintenancePlanId':'planId'},mode='unique')
        clock=b.calculate('clock','计算值 · 本次检查时间',[calc('检查时间','now')],joined)
        types={p['c_jihualeixing'] for p in plans}
        if 2 in types and types-{2}:
            gate=b.condition('plan_type','判断值 · 是否保养计划',compare('eq','计划类型',2),clock)
            maint=(gate,'true');inspect=(gate,'false')
        else:
            maint=inspect=(clock,'out')
        if types-{2}: inspection(b,key,*inspect,types=types-{2})
        if 2 in types: maintenance(b,key,*maint)
    if repair_enabled: repair(b,key,root)
    return b.finish(report)


def inspection(b,device_key,parent,port,types):
    # A source task stores plan NAME, type and cycle, not the plan primary key.
    # Keep the name match count visible so duplicate plan names are not hidden.
    named=b.read('inspect_names','读取值 · 核对计划名称归属','t_dinspectionmaintenanceplan',
                 {'dInspectionMaintenancePlanId':'名称对应计划'},parent,port=port,
                 matches={'c_planName':'计划名称'},mode='unique')
    latest_columns={'deviceInspectionMaintenanceId':'最新巡检任务','c_inspectionStatus':'任务巡检状态',
                    'c_recordTime':'最新巡检时间','c_yujixunjianshijian':'预计巡检时间'}
    completed_columns={'deviceInspectionMaintenanceId':'上次完成任务','c_recordTime':'上次巡检完成时间'}
    parents=[]
    both=1 in types and bool(types-{1})
    gate=b.condition('inspect_type','判断值 · 是否单设备巡检',compare('eq','计划类型',1),named) if both else named
    for suffix,single in [('single',True),('area',False)]:
        if (single and 1 not in types) or (not single and not types-{1}): continue
        branch_port=('true' if single else 'false') if both else 'out'
        match={'c_xunjianjihua':'计划名称','c_jihualeixing':'计划类型','c_recordType':'巡检周期编码'}
        filters=[('c_deviceId','eq',device_key)] if single else []
        latest=b.read('task_'+suffix,'读取值 · 最近巡检任务','t_deviceinspectionmaintenance',latest_columns,gate,
                      port=branch_port,matches=match,mode='latest',filters=filters,
                      order=['c_yujixunjianshijian','createdOn','deviceInspectionMaintenanceId'])
        completed=b.read('completed_'+suffix,'读取值 · 上次已完成巡检','t_deviceinspectionmaintenance',completed_columns,latest,
                      matches=match,mode='latest',filters=[*filters,('c_inspectionStatus','eq',1),('c_recordTime','not_null',None)],
                      order=['c_recordTime','deviceInspectionMaintenanceId'])
        if not single:
            completed=b.read('area_result','读取值 · 本设备最近完成巡检结果','t_quyuleixingshebeibaoyangmingxi',
                {'quyuleixingshebeibaoyangmingxiId':'区域巡检记录','c_xunjianjieguo':'区域巡检结果','c_xunjianjieguobeizhu':'区域巡检说明'},completed,
                matches={'mdDeviceInspectionMaintenanceId':'上次完成任务','c_shebei':'deviceKey'},mode='unique')
        parents.append(completed)
    joined=parents[0]
    if len(parents)>1:
        joined=b.add('inspect_tasks','merge','汇合本设备适用巡检任务')
        for p in parents: b.edge(p,joined)
    period=b.calculate('inspect_due','计算值 · 应巡检时间',[calc('巡检间隔小时','lookup','巡检周期编码',mapping={str(k):v for k,v in {1:2,2:4,3:8,4:12,5:24,6:168}.items()}),
        calc('巡检间隔月数','lookup','巡检周期编码',mapping={'7':1,'8':3,'9':6,'10':12}),
        calc('小时周期到期时间','add_hours','上次巡检完成时间','巡检间隔小时'),
        calc('日历周期到期时间','add_months','上次巡检完成时间','巡检间隔月数'),
        calc('应巡检时间','coalesce','小时周期到期时间','日历周期到期时间'),
        calc('巡检超期小时','hours_between','应巡检时间','检查时间')],joined)
    b.check('inspect_cycle','巡检是否超期',period,
        expr('or',missing('巡检超期小时'),compare('ne','核对计划名称归属匹配数',1)),compare('gt','巡检超期小时',0),
        pending='无法唯一关联计划，或缺少完成时间/有效周期。请查看本行计划名称、周期编码及完成时间。',
        warning='距上次完成巡检已超过源计划周期。',normal='距上次完成巡检尚未超过源计划周期。')
    points=b.read('inspect_requirements','读取值 · 本计划逐项巡检要求','t_detailsofinspectionindicators',
        {'detailsOfInspectionIndicatorsId':'巡检要求标识','c_inspectionName':'检查项目','c_inspectionIndicators':'检查指标','c_inspectionContent':'检查要求'},joined,
        matches={'mdDInspectionMaintenancePlanId':'planId'})
    # Pending tasks are generated ahead of time. Assess actual measurements from
    # the latest completed task; timing independently determines their freshness.
    actual=b.read('inspect_actual','读取值 · 最近完成任务逐项实测','t_deviceinspectiondetails',
        {'deviceInspectionDetailsId':'巡检实测记录','c_inspectionResult':'单项巡检结果','c_inspectionDesc':'单项实测说明','c_inspectionContent':'记录采用要求'},points,
        matches={'mdDeviceInspectionMaintenanceId':'上次完成任务','c_inspectionName':'检查项目','c_inspectionIndicators':'检查指标'},mode='unique')
    calculated=b.calculate('inspect_values','计算值 · 实际结果与要求',[calc('现场结果','coalesce','单项巡检结果','区域巡检结果'),
        calc('现场说明','coalesce','单项实测说明','区域巡检说明'),calc('数值下限','lower_limit','检查要求'),
        calc('数值上限','upper_limit','检查要求'),calc('实际数值','number','单项实测说明'),
        calc('实际单位','unit','单项实测说明'),calc('现场结果含义','lookup','现场结果',mapping={'1':'正常','2':'异常'})],actual)
    numeric=expr('or',expr('present',field('数值下限')),expr('present',field('数值上限')))
    gate=b.condition('numeric_required','判断值 · 是否单一数值要求',numeric,calculated)
    manual=b.calculate('manual_requirements','计算值 · 现场巡检结论',[calc('判断依据','copy','源库现场人工巡检结果',constant=True)],gate,'false')
    b.check('inspect_result','现场巡检是否异常',manual,missing('现场结果含义'),compare('eq','现场结果含义','异常'),
        pending='源库最近完成巡检没有明确的正常/异常结果，请核对检查要求、上次完成任务和实际说明。',
        warning='本设备源库巡检记录明确标记异常，请按记录时间与现场说明处理。',
        normal='源库现场巡检记录标记正常；此项采纳人工记录，不代表新的自动测量。')
    numbers=b.calculate('inspect_numeric','计算值 · 数值检查标识',[calc('检查类型','copy','数值上下限',constant=True)],gate,'true')
    b.check('inspect_numeric_limit','数值是否超出要求',numbers,expr('or',missing('实际数值','实际单位'),
        expr('and',compare('eq','检查要求','21V以上'),compare('ne','实际单位','V')),
        expr('and',compare('eq','检查要求','在10°C以上'),compare('ne','实际单位','°C'))),
        expr('or',expr('and',expr('present',field('数值下限')),expr('lt',field('实际数值'),field('数值下限'))),
                  expr('and',expr('present',field('数值上限')),expr('gt',field('实际数值'),field('数值上限')))),
        pending='要求包含数值限值，但没有独立实测数值；不能把标准数字当成实测。',
        warning='实际巡检数值超出本项要求。',normal='实际巡检数值满足本项要求。')


def maintenance(b,device_key,parent,port):
    named=b.read('maintenance_names','读取值 · 核对保养计划归属','t_dinspectionmaintenanceplan',
        {'dInspectionMaintenancePlanId':'保养名称对应计划'},parent,port=port,
        matches={'c_planName':'计划名称'},mode='unique')
    points=b.read('maintenance_points','读取值 · 本计划润滑保养要求','t_xunjianjihuarunhuabaoyang',
        {'xunjianjihuarunhuabaoyangId':'润滑要求标识','c_runhuadianweimingcheng':'润滑点','c_runhuadianweibianhao':'润滑点编号',
         'c_runhuazhouqi':'周期天数','c_runhuayoupaihao':'要求油品','c_runhualiang':'要求用量','c_runhuafangshi':'要求方式',
         'c_huanyouzhouqi':'换油周期','c_huanyouliang':'换油量'},named,
        matches={'mdDInspectionMaintenancePlanId':'planId'})
    tasks=b.read('maintenance_tasks','读取值 · 本设备已完成保养','t_deviceinspectionmaintenance',
        {'deviceInspectionMaintenanceId':'保养任务','c_recentMaintenancePeriod':'保养完成时间','c_maintenanceStatus':'保养状态'},points,
        matches={'c_xunjianjihua':'计划名称','c_deviceId':'deviceKey'},mode='latest',filters=[('c_maintenanceStatus','eq',1)],
        order=['c_recentMaintenancePeriod','createdOn','deviceInspectionMaintenanceId'])
    details=b.read('maintenance_actual','读取值 · 本次润滑点完成记录','t_shebeirunhuabaoyangmingxibiao',
        {'shebeirunhuabaoyangmingxibiaoId':'润滑实测记录','c_baoyangzhuangtai':'润滑点完成状态',
         'c_runhuayoupaihao':'实际油品','c_runhualiang':'实际用量','c_runhuafangshi':'实际方式'},tasks,
        matches={'mdDeviceInspectionMaintenanceId':'保养任务','c_runhuadianweimingcheng':'润滑点'},mode='unique')
    calculated=b.calculate('maintenance_values','计算值 · 保养间隔与用量范围',[
        calc('已过天数','days_between','保养完成时间','检查时间'),calc('周期数值','number','周期天数'),
        calc('计划间隔小时','lookup','保养周期编码',mapping={'13':168}),
        calc('计划间隔月数','lookup','保养周期编码',mapping={'14':1,'15':3,'16':6,'17':12}),
        calc('计划小时到期时间','add_hours','保养完成时间','计划间隔小时'),
        calc('计划日历到期时间','add_months','保养完成时间','计划间隔月数'),
        calc('计划保养到期时间','coalesce','计划小时到期时间','计划日历到期时间'),
        calc('计划超期小时','hours_between','计划保养到期时间','检查时间'),
        calc('用量下限','range_lower','要求用量'),calc('用量上限','range_upper','要求用量'),calc('要求用量单位','range_unit','要求用量'),
        calc('实际用量数值','number','实际用量'),calc('实际用量单位','unit','实际用量'),
        calc('要求换算率','lookup','要求用量单位',mapping={'kg':1,'g':.001}),
        calc('实际换算率','lookup','实际用量单位',mapping={'kg':1,'Kg':1,'KG':1,'g':.001,'G':.001}),
        calc('用量下限千克','multiply','用量下限','要求换算率'),calc('用量上限千克','multiply','用量上限','要求换算率'),
        calc('实际用量千克','multiply','实际用量数值','实际换算率')],details)
    b.check('maintenance_plan_cycle','计划保养是否超期',calculated,
        expr('or',missing('计划超期小时'),compare('lt','已过天数',0),compare('ne','核对保养计划归属匹配数',1)),
        compare('gt','计划超期小时',0),
        pending='缺少可唯一关联的已完成保养时间或有效计划周期；计划周期与润滑点周期分别核对。',
        warning='已超过源库保养计划配置的周、月、季度、半年或年度周期。',normal='尚未超过源库保养计划周期。')
    b.check('maintenance_cycle','润滑保养是否超期',calculated,
        expr('or',missing('已过天数','周期数值'),compare('lte','周期数值',0),compare('lt','已过天数',0),compare('ne','润滑点完成状态',1),compare('ne','核对保养计划归属匹配数',1)),
        expr('gt',field('已过天数'),field('周期数值')),
        pending='源库缺少本点已完成保养记录、完成时间或有效润滑周期；其他检查独立继续。',
        warning='距本点上次完成保养的天数超过源计划润滑周期。',normal='本润滑点尚未超过源计划周期。')
    for key,title,required,actual in [('oil','润滑油品','要求油品','实际油品'),('method','保养方式','要求方式','实际方式')]:
        b.check('maintenance_'+key,title+'是否一致',calculated,
            expr('or',missing(required,actual,'保养完成时间'),compare('ne','润滑点完成状态',1),compare('lt','已过天数',0),compare('ne','核对保养计划归属匹配数',1)),expr('ne',field(required),field(actual)),
            pending=f'请核对本行{required}和{actual}；未读到对应已完成的保养实测值。',
            warning=f'{actual}与本点{required}不一致。',normal=f'{actual}与本点{required}一致。')
    b.check('maintenance_amount','润滑用量是否符合范围',calculated,
        expr('or',missing('用量下限千克','用量上限千克','实际用量千克','保养完成时间'),compare('ne','润滑点完成状态',1),compare('lt','已过天数',0),compare('ne','核对保养计划归属匹配数',1)),
        expr('or',expr('lt',field('实际用量千克'),field('用量下限千克')),expr('gt',field('实际用量千克'),field('用量上限千克'))),
        pending='用量要求或实际值缺少明确数值与单位，或没有已完成保养记录；保留原文等待确认。',
        warning='实际润滑用量超出数据库中该点的要求范围。',normal='实际润滑用量在该点要求范围内，已统一 kg/g 单位。')
    change=b.add('oil_change_required','filter','筛选 · 已配置换油要求',
        {'expression':expr('or',expr('present',field('换油周期')),expr('present',field('换油量')))},details)
    b.output('oil_change','输出 · 换油要求核对','pending',
        '源库已读取本点换油周期和换油量；当前执行明细没有独立换油时间/实测换油量，不能以普通润滑日期代替。',change)


def repair(b,device_key,parent):
    rows=b.read('repair_tasks','读取值 · 本设备维修记录','t_equipmentmaintenancelog',
        {'equipmentMaintenanceLogId':'维修记录','c_zhubiaoshebei':'维修设备','c_jianxiudianmingcheng':'维修点',
         'c_jianxiuneirong':'维修要求','c_weixiuzhuangtai':'维修状态','c_yujichuliwanchengshijian':'预计维修完成时间',
         'c_shijichuliwanchengshijian':'实际维修完成时间','c_daochangyanshoushifouhege':'维修验收状态','c_shifouanzhuangwancheng':'维修安装状态'},
         parent,matches={'c_zhubiaoshebei':'deviceKey'})
    values=b.calculate('repair_values','计算值 · 维修时间和状态',[calc('维修检查时间','now'),
        calc('维修超期小时','hours_between','预计维修完成时间','维修检查时间'),
        calc('完成距今小时','hours_between','实际维修完成时间','维修检查时间'),
        calc('维修状态含义','lookup','维修状态',mapping={'1':'未开始','2':'进行中','3':'已完成','4':'检修失败'})],rows)
    b.check('repair_state','维修是否失败',values,missing('维修状态含义'),compare('eq','维修状态含义','检修失败'),
        pending='源库维修状态未填写或无法识别。',warning='源库维修状态明确为检修失败。',normal='源库维修任务未标记检修失败；完成和验收由独立检查核对。')
    gate=b.condition('repair_completed','判断值 · 维修是否已完成',compare('eq','维修状态含义','已完成'),values)
    complete=b.calculate('repair_complete_value','计算值 · 已完成维修检查',[calc('检查类型','copy','维修验收',constant=True)],gate,'true')
    b.check('repair_acceptance','维修验收是否合格',complete,
        expr('or',missing('实际维修完成时间','完成距今小时'),
             expr('and',compare('ne','维修验收状态',1),compare('ne','维修验收状态',2)),compare('lt','完成距今小时',0)),
        expr('or',compare('eq','维修验收状态',2),compare('eq','维修安装状态',2)),
        pending='维修标记已完成，但缺少有效完成时间或验收记录。',warning='维修验收不合格或安装未完成。',normal='维修已完成且验收合格。')
    ongoing=b.calculate('repair_ongoing','计算值 · 未完成维修检查',[calc('检查类型','copy','维修期限',constant=True)],gate,'false')
    b.check('repair_overdue','未完成维修是否逾期',ongoing,missing('维修超期小时'),compare('gt','维修超期小时',0),
        pending='源库未填写有效预计维修完成时间，无法判断超期。',warning='维修尚未完成且超过任务预计完成时间。',normal='维修尚未到预计完成期限。')
