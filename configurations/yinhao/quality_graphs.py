"""Read real quality forms, template requirements and all three measurement layouts."""
from .graph_builder import GraphBuilder, calc, compare, expr, field, missing

FORM={'incomingMaterialQcFormId':'单据标识','c_incomingQcNo':'质检单号','c_qualityInspectionTemplate':'模板标识',
      'c_testResults':'原检验结果','c_zhijianshifouhege':'原合格状态','c_zhijiandanzhuangtai':'原单据状态'}
OUTPUT=[*FORM.values(),'检验项目','项目编码','标准文本','模板判断方式','模板下界','模板阈值',
        '原单项判断方式','原单项下界','原单项阈值','判定方式','下界','阈值','测量值','实测记录','统计实测记录',
        '炉前实测记录','出厂实测记录','炉前原结论','出厂原结论','有效模板','必检项目标识']


def build_quality(source, category, template_id, project_codes=()):
    b=GraphBuilder(source,quality=True)
    b.nodes[0]['config']={'variables':[{'variable':'formNumber','label':'质检单号（留空检查最新100张）','default':'','type':'text-input'}]}
    forms=b.read('forms','读取值 · 产品质检单','t_incomingmaterialqcform',FORM,
        filters=[('c_qualityInspection','eq',category),('c_qualityInspectionTemplate','eq' if template_id else 'is_null',template_id)],
        order=['createdOn','incomingMaterialQcFormId'])
    b.nodes[-1]['config'].update(take=100)
    b.nodes[-1]['config']['filters'].append({'field':'c_incomingQcNo','valueSource':'input','inputField':'formNumber','operator':'eq','skipEmpty':True})
    templates=b.read('template','读取值 · 当前产品质检模板','t_qualityinspectiontemplate',
        {'qualityInspectionTemplateId':'有效模板','c_templateName':'模板名称','c_product':'产品标识'},forms,
        matches={'qualityInspectionTemplateId':'模板标识'},mode='unique')
    requirements=b.read('requirements','读取值 · 本模板全部必检要求','t_qitemplatedetails',
        {'qITemplateDetailsId':'必检项目标识','c_xuanzezhijianxiangmu':'项目标识','c_judgeRule':'模板判断方式',
         'c_standardMin':'模板下界','c_standardMax':'模板阈值','c_shifouzhuyaozhijianxiangmu':'主要项目'},templates,
        matches={'mdQualityInspectionTemplateId':'模板标识'})
    projects=b.read('projects','读取值 · 本项目标准说明','t_qualityinspectionproject',
        {'qualityInspectionProjectId':'有效项目','c_projectCode':'项目编码','c_projectName':'检验项目',
         'c_inspectionStandard':'标准文本','c_inspectionRequirement':'标准要求说明','c_judgeRule':'项目判断方式',
         'c_standardMin':'项目下界','c_standardMax':'项目阈值'},requirements,
        matches={'qualityInspectionProjectId':'项目标识'},mode='unique')
    actual=b.read('measurements','读取值 · 本单逐项实测值','t_qualityinspectionitemdetails',
        {'qualityInspectionItemDetailsId':'实测记录','c_jianyanjieguo':'单项实测值','c_judgeRule':'原单项判断方式',
         'c_standardMin':'原单项下界','c_standardMax':'原单项阈值'},projects,
        matches={'mdIncomingMaterialQcFormId':'单据标识','c_qualityInspectionProject':'项目标识'})
    # Field codes 0001..0018 and 0052..0056 directly correspond to physical
    # statistics fields. Never equate ml and % merely because names look alike.
    stats_fields={f'c_n{code}':f'统计值_{code}' for code in [*[f'{i:04d}' for i in range(1,19)],'0052','0053','0054','0055','0056']}
    tail=b.read('statistics','读取值 · 本单统计实测','t_zhijianxiangmutongjimingxi',
        {'zhijianxiangmutongjimingxiId':'统计实测记录',**stats_fields},actual,
        matches={'mdIncomingMaterialQcFormId':'单据标识'},mode='unique')
    gas_codes={'0037':'气量','0038':'磷化氢','0039':'硫化氢','0042':'气量','0044':'磷化氢','0045':'硫化氢'}
    if set(project_codes)&gas_codes.keys() or not template_id:
        for suffix,table,primary in [('炉前','t_incomingmaterialqcitemdetails','incomingMaterialQcItemDetailsId'),
                                     ('出厂','t_incomingmaterialqcitemdetails2','incomingMaterialQcItemDetails2Id')]:
            tail=b.read('gas_'+('before' if suffix=='炉前' else 'after'),'读取值 · '+suffix+'实际样本',table,
                {primary:suffix+'实测记录','c_timePoint':suffix+'检测时间','c_luyanshu':suffix+'炉眼',
                 'c_faqiliang':suffix+'气量','c_linhuaqing':suffix+'磷化氢','c_liuhuaqing':suffix+'硫化氢','c_jieguo':suffix+'原结论'},tail,
                matches={'mdIncomingMaterialQcFormId':'单据标识'},mode='all')
    mappings={code:'统计值_'+code for code in [*[f'{i:04d}' for i in range(1,19)],'0052','0053','0054','0055','0056']}
    values=b.calculate('quality_values','计算值 · 按项目取实测并读取当前标准',[
        calc('统计项目值','mapped_field','项目编码',mapping=mappings),
        calc('炉前项目值','mapped_field','项目编码',mapping={k:'炉前'+v for k,v in gas_codes.items()}),
        calc('出厂项目值','mapped_field','项目编码',mapping={k:'出厂'+v for k,v in gas_codes.items()}),
        calc('气体项目值','coalesce','炉前项目值','出厂项目值'),
        calc('补充项目值','coalesce','统计项目值','气体项目值'),
        calc('测量值','coalesce','单项实测值','补充项目值'),
        calc('判定方式','coalesce','模板判断方式','项目判断方式'),
        calc('下界','coalesce','模板下界','项目下界'),calc('阈值','coalesce','模板阈值','项目阈值'),
        calc('测量数值','number','测量值'),calc('上限数值','number','阈值'),calc('下限数值','number','下界'),
        {**calc('含夏季条件','contains','标准文本',0),'right':{'source':'constant','value':'夏'}},
        {**calc('含冬季条件','contains','标准文本',0),'right':{'source':'constant','value':'冬'}}],tail)
    seasonal=b.condition('seasonal','判断值 · 是否季节标准',expr('or',compare('eq','含夏季条件',True),compare('eq','含冬季条件',True)),values)
    ordinary=b.calculate('ordinary','计算值 · 当前标准偏差',[calc('距阈值差额','subtract','测量数值','上限数值')],seasonal,'false')
    supported=expr('or',*[compare('eq','判定方式',v) for v in ['lte','gte','lt','gt','eq','ne','between_closed']])
    invalid=expr('or',missing('有效模板','有效项目','测量数值','上限数值'),expr('not',supported),
        expr('and',compare('eq','判定方式','between_closed'),expr('or',missing('下限数值'),expr('gt',field('下限数值'),field('上限数值')))))
    violation=expr('or',*[expr('and',compare('eq','判定方式',rule),expr(op,field('测量数值'),field('上限数值')))
        for rule,op in [('lte','gt'),('gte','lt'),('lt','gte'),('gt','lte'),('eq','ne'),('ne','eq')]],
        expr('and',compare('eq','判定方式','between_closed'),expr('or',expr('lt',field('测量数值'),field('下限数值')),expr('gt',field('测量数值'),field('上限数值')))))
    b.check('quality_standard','实测是否符合当前项目标准',ordinary,invalid,violation,
        pending='本项缺少有效模板、项目、实测值或可执行阈值。请查看本行具体字段及源库标准说明。',
        warning='实测值不满足本产品当前模板/项目配置的比较方式及阈值。',
        normal='实测值满足本产品当前结构化标准；单项合格不等于整批审批放行。',fields=OUTPUT)
    season_values=b.calculate('season_values','计算值 · 季节区间核对',[calc('季节待确认','copy','未指定适用季节',constant=True)],seasonal,'true')
    exact=compare('eq','标准文本','夏季≤12.0%\n冬季≤14.0%')
    b.check('quality_season','是否超出两季共同范围',season_values,
        expr('or',missing('测量数值'),expr('not',exact),expr('and',compare('gt','测量数值',12),compare('lte','测量数值',14))),
        compare('gt','测量数值',14),pending='此项含季节适用条件，当前数据不能确定采用哪个季节标准；保留原文与实测值。',
        warning='实测值同时超过源库夏季12%和冬季14%的上限。',normal='实测值同时满足源库夏季及冬季上限。',fields=OUTPUT)
    graph=b.finish()
    # Results are already bounded, project-level evidence. Preserve a separate
    # readable document summary without hiding the detailed conclusions.
    end=next(n for n in graph['nodes'] if n['id']=='end')
    graph['nodes'].append({'id':'summary','kind':'aggregator','title':'计算值 · 按质检单汇总','inputs':{},
        'position':{'x':end['position']['x'],'y':end['position']['y']+260},'config':{
        'groupBy':['单据标识','质检单号','原检验结果','原合格状态','原单据状态'],
        'metrics':[{'name':'检查项数','operation':'count','field':''},
                   {'name':'不合格项数','operation':'sum','field':'不合格标记'},
                   {'name':'待确认项数','operation':'sum','field':'待确认标记'}]}})
    graph['nodes'].append({'id':'summary_end','kind':'end','title':'结束 · 单据汇总','inputs':{},'config':{},
        'position':{'x':end['position']['x']+345,'y':end['position']['y']+260}})
    for a,z in [('results','summary'),('summary','summary_end')]:
        graph['edges'].append({'id':a+'_out_'+z,'source':a,'target':z,'port':'out','sourceSide':'right','targetSide':'left'})
    return graph
