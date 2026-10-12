"""Export saved inspection evidence as a Song-font Word report and editable snapshot dashboard.

职责：把"已保存的巡检报告"汇成一份宋体 Word 分析文档（内嵌中文图表），可选再生成一个可交互的
数字看板报告。属于证据导出工具，不是巡检执行入口。

位置与依赖：由工作流工具节点调用（``export_report``）；读取侧复用
``workflows.business_results.list_reports``（因此天然受报告授权链路约束），绘图走
``sandbox_provider.invoke``（沙箱内 matplotlib，禁网），产物经 ``artifact.service`` 落库。

重要约束（口径纪律）：
- 统计只对"报告里实际返回的检查行"计数，**不以待确认项推断巡检是否完成**，
  也不推算全厂未巡检数量或完成率（缺少应巡检清单时无法计算）；
- 异常/正常/待确认是**检查项结论**分类，不构成"设备已巡检/未巡检"的互斥分类；
- 无逐项证据时直接抛 ``ValidationError``，不出空报告；
- 看板与图表都标注为"已保存报告快照"，且 ``dataStatus='saved_evidence'``，
  避免被误读为实时数据。
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.errors import ValidationError


def inspection_facts(reports):
    """Count returned check rows; never infer inspection completion from pending checks.

    （中文补充）把报告列表压成喂给图表/看板的纯数据事实：
    - ``sources``：去重后的报告来源（``executionId`` / ``workflowId`` / 名称 / 时间 / 链接），
      去重键优先用 ``executionId``，缺失时退化为报告 ``id``；
    - ``devices``：按报告中的**设备名称**聚合的检查项计数（名称缺失记作"设备名称未记录"）；
    - ``attention``：非"正常"的逐项明细（异常 + 待确认），最多由调用方截断展示；
    - ``counts`` / ``checkCount`` / ``deviceNameCount`` 与 ``scopeNote``（口径说明，会原样写进文档）。

    分类口径：结果属于 {'预警','不合格'} 记异常，{'正常','合格'} 记正常，其余（含空）
    记"待确认"——**待确认不计入正常，也不计入异常**。
    忽略项：``historicalRevision`` 标记的历史修订报告不参与统计，避免同一执行重复计数。
    ``entry['异常项'] += category == '异常'`` 之类的写法依赖 Python 的布尔即整数语义，
    改写成 if 时不要引入额外的类型转换。
    纯函数，不访问数据库、无副作用。
    """
    devices = {}
    counts = Counter()
    attention = []
    sources = []
    seen = set()
    for report in reports:
        identity = report.get('executionId') or report.get('id')
        if identity in seen or report.get('historicalRevision'):
            continue
        seen.add(identity)
        sources.append({k: report.get(k) for k in ('executionId','workflowId','workflowName','createTime','reportUrl')})
        for row in report.get('data') or []:
            name = str(row.get('设备') or '设备名称未记录')
            outcome = str(row.get('结果') or '待确认')
            # 只做结论归类，不从"待确认"推断"未巡检"。
            category = '异常' if outcome in {'预警','不合格'} else '正常' if outcome in {'正常','合格'} else '待确认'
            counts[category] += 1
            entry = devices.setdefault(name, {'设备':name,'检查项':0,'异常项':0,'待确认项':0})
            entry['检查项'] += 1
            entry['异常项'] += category == '异常'
            entry['待确认项'] += category == '待确认'
            if category != '正常':
                attention.append({'设备':name,'项目':row.get('项目') or row.get('检查项目') or '未记录',
                                  '结果':outcome,'说明':row.get('说明') or '',
                                  '上次完成':row.get('上次完成') or '未记录','应完成时间':row.get('应完成时间') or '未记录',
                                  '来源':identity})
    return {'sources': sources, 'devices': list(devices.values()), 'attention': attention,
            'counts': dict(counts), 'checkCount': sum(counts.values()), 'deviceNameCount': len(devices),
            'scopeNote': '统计范围为当前有权查看的已保存巡检报告，每个工作流取最近一份。设备按报告名称去重；检查项、异常项和待确认项不等于未巡检设备数。缺少完整应巡检清单时，不推算全厂未巡检数量或巡检完成率。'}


# 沙箱内执行的绘图脚本（matplotlib，禁网）。键名必须与 inspection_facts 的返回值一致；
# 坐标轴与标题刻意写明"不是未巡检设备数 / 非巡检完成率"，防止读图者把检查项数当覆盖率。
CHART_CODE = '''import matplotlib.pyplot as plt
from textwrap import fill

def main(facts):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), layout='constrained')
    rows = sorted(facts['devices'], key=lambda r: r['异常项'], reverse=True)[:15]
    y = list(range(len(rows)))
    axes[0].barh(y, [r['异常项'] for r in rows], color='#E65B56', label='异常检查项')
    axes[0].barh(y, [r['待确认项'] for r in rows], left=[r['异常项'] for r in rows], color='#E8B24B', label='待确认检查项')
    axes[0].set_yticks(y, [fill(r['设备'], 12) for r in rows])
    axes[0].invert_yaxis()
    axes[0].set_title('设备检查问题分布（最多展示 15 个名称）', pad=16)
    axes[0].set_xlabel('检查项数（不是未巡检设备数）')
    axes[0].legend(loc='lower right')
    labels = [k for k in ['正常','异常','待确认'] if facts['counts'].get(k, 0)]
    values = [facts['counts'][k] for k in labels]
    axes[1].pie(values, labels=labels, autopct='%1.1f%%', startangle=90,
                colors=[{'正常':'#43A990','异常':'#E65B56','待确认':'#E8B24B'}[k] for k in labels],
                wedgeprops={'width':0.55, 'edgecolor':'white'})
    axes[1].set_title('检查项结论分布（非巡检完成率）', pad=16)
    fig.suptitle('设备巡检分析 · 已保存报告快照', fontsize=18)
    fig.savefig('inspection_analysis_song.png', dpi=160, bbox_inches='tight')
    plt.close(fig)
    return {'file':'inspection_analysis_song.png'}
'''


def export_report(ctx, run_id, args):
    """生成"已保存巡检证据"的宋体 Word 报告（含中文图表），可选附带数字看板。

    参数：``ctx`` 授权上下文；``run_id`` 为触发本次导出的工作流执行标识（产物归属与审计用）；
    ``args`` 支持 ``reference`` / ``keyword`` / ``start`` / ``end``（报告筛选与时间范围，时间
    字符串按 ``Asia/Shanghai`` 解释，带 Z 的按 UTC）与 ``include_dashboard``（是否另建看板）。

    权限：``workflows.view`` + ``alerts.view``；``include_dashboard`` 为真时另需
    ``dashboards.create``。

    返回：``{resultType, message, artifacts, dashboard, facts}``，其中 ``message`` 里含
    可下载链接，供对话/工具结果直接展示。

    异常：筛选范围内没有任何"含逐项证据"的报告时抛 ``ValidationError``
    （即没有检查行就不出文档，避免生成空统计被当成"全部正常"）。

    副作用：写 ``artifacts``（图片 + 文档，属当前工作区与执行）、可选写报告看板；
    调用 ``ctx.db.commit()`` 提交事务；绘图在受限沙箱内执行（30 秒超时、禁网），
    不接触业务库连接。统计口径全部来自 ``inspection_facts``，本函数不额外计算比例类指标。
    """
    from app.common.authorization import require_permission
    from app.modules.workflows.business_results import list_reports
    from app.modules.artifact.renderers import render_document
    from app.modules.artifact.service import create_artifact, artifact_reference
    from app.providers.tool.sandbox_provider import invoke
    from app.providers.tool.base import ToolSpec
    from zoneinfo import ZoneInfo
    for permission in ('workflows.view', 'alerts.view'):
        require_permission(ctx, permission)
    if args.get('include_dashboard'):
        require_permission(ctx, 'dashboards.create')
    dates = {}
    for key in ('start','end'):
        value = args.get(key)
        if value:
            # 无时区的时间按本地时区（Asia/Shanghai）解释，避免跨时区少算/多算一天。
            parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
            dates[key] = (parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))).isoformat()
    # 每个工作流只取最近一份、只取启用中的流程，避免历史修订重复计入同一批证据。
    report_page = list_reports(ctx, kind='alert', category='inspection', reference=args.get('reference') or '',
        keyword=args.get('keyword') or '', limit=20, latest_per_workflow=True, active_only=True, detail=True, **dates)
    facts = inspection_facts(report_page['items'])
    if facts['checkCount'] == 0:
        raise ValidationError('当前范围没有含逐项证据的巡检报告，无法据此生成统计文档；请先查看对应工作流运行结果')
    chart = invoke(ToolSpec(name='python_execute',description='render authorized inspection facts',provider='sandbox',timeout_seconds=30),
        {'code':CHART_CODE,'inputs':{'facts':facts}}, {'run_id':run_id,'allow_network':False})
    filename = next(Path(a['path']) for a in chart.artifacts if a['name']=='inspection_analysis_song.png')
    image = create_artifact(ctx.db,workspace_id=ctx.workspace_id,owner_user_id=ctx.user_id,run_id=run_id,
        type='image',name='设备巡检分析_宋体.png',data=filename.read_bytes(),metadata={'source':'saved_inspection_reports','facts':facts})
    image_ref = artifact_reference(image)
    # 章节顺序固定：先口径、再图表、再明细、最后来源追溯（任何人看文档第一页就知道数据边界）。
    sections = [
        {'title':'数据范围与口径','blocks':[{'type':'p','text':facts['scopeNote']},
            {'type':'p','text':f"本次读取 {len(facts['sources'])} 份报告、{facts['deviceNameCount']} 个设备名称、{facts['checkCount']} 个检查项。"},
            {'type':'p','text':'本页为最近已保存的报告快照；尚有后页，不代表完整覆盖。' if report_page.get('hasMore') else '本页已覆盖当前筛选返回的报告。'}]},
        {'title':'巡检图表与数字看板','blocks':[{'type':'image','_image_bytes':filename.read_bytes(),'caption':'检查项统计；异常和待确认不代表设备已巡检/未巡检的互斥分类。'}]},
        {'title':'设备检查汇总','blocks':[{'type':'table','headers':['设备','检查项','异常项','待确认项'],'rows':facts['devices']}]},
        {'title':'待核对事项','blocks':[{'type':'table','headers':['设备','项目','结果','说明','上次完成','应完成时间'],'rows':facts['attention'][:200]},
            {'type':'p','text':f"共 {len(facts['attention'])} 项关注事项，正文最多展示 200 项；完整证据可按来源报告查看。"}]},
        {'title':'来源追溯','blocks':[{'type':'p','text':f"{s.get('workflowName')}｜{s.get('createTime')}｜{s.get('executionId')}｜{s.get('reportUrl')}"} for s in facts['sources']]},
    ]
    dashboard = None
    if args.get('include_dashboard'):
        from app.modules.reports.service import save_report
        from app.modules.reports.schemas import ReportInput
        data = [[r[k] for k in ['设备','检查项','异常项','待确认项']] for r in facts['devices']]
        # 看板为草稿状态的快照报告（refreshSeconds=0：不自动刷新，避免被误认为实时监控）。
        widgets = [
            {'id':'title','component':'JText','componentName':'设备巡检分析','x':30,'y':20,'w':1220,'h':60,
             'config':{'option':{'body':{'text':'设备巡检分析 · 已保存报告快照','fontSize':30,'color':'#EAF8FF','fontFamily':'SimSun'}}}},
            {'id':'scope','component':'JText','componentName':'统计口径','x':30,'y':85,'w':1220,'h':105,
             'config':{'option':{'body':{'text':facts['scopeNote'],'fontSize':18,'color':'#BFD3E8','fontFamily':'SimSun'}}}},
            {'id':'checks','component':'JScrollBoard','componentName':'逐设备检查项统计','x':30,'y':210,'w':1220,'h':470,
             'config':{'dataType':1,'chartData':json.dumps(data,ensure_ascii=False),'chartDataParsed':data,
              'option':{'header':[{'label':k} for k in ['设备','检查项','异常项','待确认项']], 'rowNum':10,
                'headerFontSize':22,'bodyFontSize':20,'headerBGC':'#19435D','oddRowBGC':'#0C263F','evenRowBGC':'#12334D','fontColor':'#EAF8FF'}}},
        ]
        # lineage 记录数据来源与快照时间，便于溯源"这份看板是哪些执行生成的"。
        saved = save_report(ctx,ReportInput(reportName='设备巡检分析（报告快照）',description=facts['scopeNote'],datasourceIds=[],
            content={'canvas':{'width':1280,'height':720,'backgroundColor':'#07111F'},'widgets':widgets},width=1280,height=720,
            refreshSeconds=0,templateMode='none',status='draft'),lineage={'entry':'inspection_evidence_export','source':'saved_workflow_reports',
            'sourceExecutionIds':[s['executionId'] for s in facts['sources']], 'snapshotAt':datetime.now(timezone.utc).isoformat(),'dataStatus':'saved_evidence'})
        dashboard={'reportId':saved.id,'previewPath':f'/report/view/{saved.id}?workspace_id={ctx.workspace_id}'}
        sections[1]['blocks'].append({'type':'p','text':'交互看板：'+dashboard['previewPath']+'。Word 内嵌的是同一份已保存报告数据的静态分析图；看板可在浏览器中查看。'})
    document = create_artifact(ctx.db,workspace_id=ctx.workspace_id,owner_user_id=ctx.user_id,run_id=run_id,type='document',
        name='设备巡检分析报告_宋体.docx',data=render_document({'title':'设备巡检分析报告（已保存证据）','sections':sections}),
        metadata={'source':'saved_inspection_reports','sourceExecutionIds':[s['executionId'] for s in facts['sources']], 'font':'宋体'})
    # 图片与文档一次提交；看板若已单独提交则以其自身事务为准。
    ctx.db.commit()
    references=[image_ref,artifact_reference(document)]
    message = '已按已保存巡检证据生成宋体 Word 报告及中文分析图，文档已嵌入图表。'+facts['scopeNote']
    message += '\n\n'+'\n'.join(f"[{a['name']}]({a['download_url']})" for a in references)
    if dashboard:message+='\n\n[查看数字看板]('+dashboard['previewPath']+')'
    return {'resultType':'inspection_report_export','message':message,'artifacts':references,'dashboard':dashboard,'facts':facts}
