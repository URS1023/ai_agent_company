"""One read model for saved workflow reports, business pages and conversation tools.

中文补充：已保存业务报告的**只读**读取模型，被三处复用：报告页面/接口
（``router.business_results``）、会话工具（``tool_result``）与详情读取（``read_report``）。
所有数据都取自执行记录里已落库的结果，绝不会因为读报告而重新运行规则、模型或查询。

重要的匹配语义与风险：

- ``reference`` 采用"三选一命中"的宽松匹配：``or_(WorkflowExecution.id == reference,
  WorkflowExecution.run_id == reference, WorkflowExecution.workflow_id == reference)``。
  同一个字符串可以命中执行 id、统一运行 id，**也可以命中工作流 id**——传工作流 id 时返回的是
  该流程下最近的一批报告，而不是某一次运行。这样设计是为了让会话工具能用流程 id/名称直接取概览；
  代价是调用方无法用同一个参数表达"精确到某一次执行"，需要精确定位时应传 execution id
  （``read_report`` 依赖的正是这个语义）。
- ``Workflow.domains.contains(kind)`` 在 SQLite/MySQL 上是 JSON 子串匹配，用于按业务域筛选，
  因此要求 domains 里出现的就是该 kind 的字面量。
- 授权过滤在 Python 侧逐条进行（``authorize_graph``），无权查看的执行既不进入结果也不计入数量，
  避免通过计数泄露记录是否存在。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.common.authorization import require_permission
from app.common.owned_records import visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.modules.workflows.business_report import build_report
from app.modules.workflows.graph import authorize_graph
from app.modules.workflows.models import Workflow, WorkflowExecution
from app.modules.workflows.schemas import FlowGraph


def projected_report(execution):
    """Read persisted evidence only; compatibility projection never reruns rules or AI.

    中文补充：把一次执行投影成报告视图，有两条路径：

    1. 执行结果里已保存 ``workflow-business-report-v1``（新流程）——直接取最后一份；
    2. 历史/旧流程没有报告对象——由 snapshot 的 domains 推断 kind，再从已完成节点输出里
       找"看起来是判定结果"的数据包重新 ``build_report``；找不到就退化成只有正文的历史报告
       （outcome=historical），连正文都没有时返回 None。

    兼容投影的两个刻意选择：优先使用 end/reply/merge 与统一运行 outputs 里的聚合输出，
    因为 MySQL 的 JSON 键顺序不等于执行顺序，不能让"排序靠后的分支"覆盖完整结果；
    判定包的条件是"data 是数组且每行都是对象并含 '结果' 字段"，空数组也接受（空批次本身是结论）。

    返回的报告已剔除 originalRows/coverage，并补齐 id/executionId/workflowId/runId/
    workflowName/revision/status/createTime/durationMs/window/reportUrl 等展示字段。
    """
    trace = execution.result_json or {}
    nodes = {key: node for key, node in (trace.get('nodes') or {}).items()
             if node.get('status') == 'completed'}
    values = [n.get('output') for n in nodes.values()]
    reports = [v for v in values if isinstance(v, dict) and v.get('schemaVersion') == 'workflow-business-report-v1']
    report = dict(reports[-1]) if reports else None
    if report is None:
        domains = execution.snapshot.get('domains') or []
        kind = 'quality' if 'quality' in domains else 'alert' if 'alert' in domains else ''
        if not kind:
            return None
        # MySQL JSON key order is not execution order. Prefer saved terminal and
        # aggregate outputs so a later-sorted branch cannot replace the full result.
        aggregates = list((trace.get('outputs') or {}).values())
        aggregates += [nodes['results'].get('output')] if 'results' in nodes else []
        aggregates += [n.get('output') for n in nodes.values() if n.get('kind') in {'merge', 'end'}]
        def decisions(value, *, allow_empty=False):
            """判断一个输出是否"看起来是判定结果"：data 是数组且每行都是带 '结果' 字段的对象。

            ``allow_empty=False`` 时还要求数组非空；优先用 allow_empty=True 挑选聚合输出，
            找不到再退化为"取行数最多的那个数据包"。
            """
            if not isinstance(value, dict) or not isinstance(value.get('data'), list):
                return False
            return (allow_empty or bool(value['data'])) and all(
                isinstance(row, dict) and '结果' in row for row in value['data'])
        packet = next((v for v in aggregates if decisions(v, allow_empty=True)), None)
        if packet is None:
            packets = [v for v in values if decisions(v)]
            packet = max(packets, key=lambda v: len(v['data']), default=None)
        if packet is not None:
            report = build_report(packet['data'], kind, execution.snapshot.get('name', '业务检查') + '报告')
        else:
            texts = [v.get('content') for v in values if isinstance(v, dict) and isinstance(v.get('content'), str)]
            if not texts:
                return None
            report = {'kind': kind, 'title': execution.snapshot.get('name', '业务检查') + '报告',
                      'content': texts[-1], 'data': [], 'summary': {}, 'outcome': 'historical'}
    window = trace.get('collectionWindow') or report.get('window') or {}
    report = {k: v for k, v in report.items() if k not in {'originalRows', 'coverage'}}
    report.update(id=execution.id, executionId=execution.id, workflowId=execution.workflow_id,
                  runId=execution.run_id, workflowName=execution.snapshot.get('name', ''),
                  revision=execution.revision, status=execution.status,
                  createTime=execution.created_at.replace(tzinfo=timezone.utc).isoformat(),
                  durationMs=trace.get('durationMs', 0),
                  window={k: window[k] for k in ('start', 'end', 'timeZone', 'mode', 'hasMore', 'afterKey') if k in window},
                  reportUrl=f'/workflows/{execution.workflow_id}/logs?execution={execution.id}')
    return report


def _date(value):
    """把带时区的 ISO 时间字符串转成 UTC naive 值，供与数据库列比较。

    空值返回 None（表示不限制）；缺时区的时间会报错——前端必须提交带时区的范围，
    否则"本地日"与"UTC 日"的差异会导致漏查或多查。
    异常：ValidationError（格式错误或缺少时区）。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValidationError('请选择有效的开始和结束时间') from exc
    if parsed.tzinfo is None:
        raise ValidationError('筛选时间需要包含时区')
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def list_reports(ctx, *, kind='quality', reference='', keyword='', start='', end='', limit=20, offset=0, detail=False,
                 active_only=False, latest_per_workflow=False, category='', quality_stage=''):
    """分页查询已保存的业务报告（``GET /api/v1/workflows/business-results``）。

    参数：
        kind：``quality`` 或 ``alert``；决定所需权限（quality.view / alerts.view，另有 workflows.view）。
        reference：宽松匹配执行 id / run id / 工作流 id，见模块 docstring。
        keyword：设备名关键词，匹配前会把流程名与关键词中的"变电站"归一成"站"并去掉空格，
            使"XX变电站"与"XX站"这类写法都能命中。
        start/end：带时区的 ISO 时间，``end`` 为开区间（created_at < end）。
        limit/offset：分页；单页硬上限 50，扫描上限 200 条，避免深分页放大查询。
        detail：False 时剔除 content/data/groups，仅返回摘要（列表页用）。
        active_only：只在已发布流程里查，并标注 currentRevision/historicalRevision。
        latest_per_workflow：每个流程只取最新一条。
        category/quality_stage：设备检查类型（巡检/保养/维修）与质检阶段；
            阶段用流程图里配置的表单参数 category 精确匹配，不靠产品名推测。

    返回 ``{"items", "nextOffset", "hasMore", "source", "kind"}``；
    异常：ValidationError（kind/阶段/时间非法）；副作用：只读。
    """
    if kind not in {'quality', 'alert'}:
        raise ValidationError('不支持的报告类型')
    require_permission(ctx, 'quality.view' if kind == 'quality' else 'alerts.view')
    require_permission(ctx, 'workflows.view')
    begin, finish = _date(start), _date(end)
    if begin and finish and begin >= finish:
        raise ValidationError('结束时间应晚于开始时间')
    query = visible_records(ctx, WorkflowExecution, 'workflows.view').join(
        Workflow, Workflow.id == WorkflowExecution.workflow_id).where(
        WorkflowExecution.status == 'completed', Workflow.domains.contains(kind))
    if active_only:
        query = query.where(Workflow.status == 'published')
    if reference:
        # 宽松匹配：执行 id / 统一运行 id / 工作流 id 三者任一命中即可（见模块 docstring 的风险说明）。
        query = query.where(or_(WorkflowExecution.id == reference, WorkflowExecution.run_id == reference,
                               WorkflowExecution.workflow_id == reference))
    if keyword:
        # Match ordinary device-name variants without losing the device constraint.
        name = func.replace(func.replace(Workflow.name, '变电站', '站'), ' ', '')
        keyword = keyword.replace('变电站', '站').replace(' ', '')
        query = query.where(name.icontains(keyword, autoescape=True))
    if category:
        labels = {'inspection': '巡检', 'maintenance': '保养', 'repair': '维修'}
        if category not in labels:
            raise ValidationError('不支持的设备检查类型')
        query = query.where(Workflow.name.contains(labels[category], autoescape=True))
    if quality_stage:
        stages = {'incoming': 1, 'process': 2, 'finished': 3}
        if kind != 'quality' or quality_stage not in stages:
            raise ValidationError('不支持的质检阶段')
        # Use the configured source category, not a guessed product-name prefix.
        pattern = {'nodes': [{'id': 'forms', 'config': {'parameters': {'category': stages[quality_stage]}}}]}
        query = query.where(func.json_contains(Workflow.graph, json.dumps(pattern)) == 1)
    if begin:
        query = query.where(WorkflowExecution.created_at >= begin)
    if finish:
        query = query.where(WorkflowExecution.created_at < finish)
    query = query.order_by(WorkflowExecution.created_at.desc(), WorkflowExecution.id.desc())
    found, scanned, exhausted = [], 0, False
    seen_workflows = set()
    # Narrow identifiers avoid sorting large saved JSON in MySQL.
    # 先只查 id 再按 id 取整行：授权与"是否有报告"都必须在 Python 侧判断，
    # 因此分页是"边扫描边计数"的，limit 为可见条数上限、scanned 为 SQL 扫描条数上限。
    while len(found) < min(limit, 50) and scanned < 200:
        ids = list(ctx.db.scalars(query.with_only_columns(WorkflowExecution.id).offset(offset + scanned).limit(20)))
        if not ids:
            exhausted = True
            break
        by_id = {r.id: r for r in ctx.db.scalars(select(WorkflowExecution).where(WorkflowExecution.id.in_(ids)))}
        batch_scanned = 0
        for key in ids:
            scanned += 1
            batch_scanned += 1
            execution = by_id[key]
            if latest_per_workflow and execution.workflow_id in seen_workflows:
                continue
            try:
                authorize_graph(ctx, FlowGraph.model_validate(execution.snapshot['graph']))
            except (NotFoundError, PermissionErrorEx):
                continue
            report = projected_report(execution)
            if not report or report['kind'] != kind:
                continue
            if active_only:
                workflow = ctx.db.get(Workflow, execution.workflow_id)
                report['currentRevision'] = workflow.published_revision
                report['historicalRevision'] = execution.revision != workflow.published_revision
            seen_workflows.add(execution.workflow_id)
            if not detail:
                report.pop('content', None)
                report.pop('data', None)
                report.pop('groups', None)
            found.append(report)
            if len(found) >= min(limit, 50):
                break
        if len(ids) < 20:
            exhausted = batch_scanned == len(ids)
            break
    return {'items': found, 'nextOffset': offset + scanned, 'hasMore': not exhausted,
            'source': 'saved_workflow_reports', 'kind': kind}


def read_report(ctx, execution_id, *, kind='quality'):
    """读取单份报告详情（``GET /api/v1/workflows/business-results/{execution_id}``）。

    依赖 ``list_reports`` 的 execution_id 匹配语义（同时也能命中 run_id/workflow_id），
    因此传入工作流 id 时返回的是该流程最近一份报告。找不到可查看报告时抛 NotFoundError，
    权限不足由 ``authorize_graph`` 过滤后同样表现为"找不到"，不暴露记录是否存在。
    """
    result = list_reports(ctx, kind=kind, reference=execution_id, limit=1, detail=True)
    if not result['items']:
        raise NotFoundError('没有找到可查看的已完成报告')
    return result['items'][0]


def tool_result(ctx, args):
    """会话工具入口：把报告查询包装成"可给模型读"的紧凑回执。

    与 UI 接口的差异：
    - 未指定 ``reference`` 且未要求历史时视为"概览"（overview）——只取已发布流程的最新一条，
      并标注历史修订，避免把旧版本结果当成当前预警；
    - limit 再收紧到 10 条；返回的 ``items`` 会剔除 data/content/groups（对话只需要摘要），
      完整逐项结果仍通过 UI 详情接口获取；
    - ``evidenceSummary`` 明确说明"无新增记录、未检查、待确认都不等于合格"，
      并且只统计本次返回的报告，不代表全库状态。

    异常：ValidationError（kind/阶段/时间非法）、权限异常；除 ``queriedAt`` 外不写任何状态。
    """
    # Older tool calls can omit kind; an explicit device category is unambiguous.
    kind = args.get('kind') or ('alert' if args.get('category') else 'quality')
    args = dict(args)
    for key in ('start', 'end'):
        value = args.get(key)
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                raise ValidationError('请选择有效的开始和结束时间')
            if parsed.tzinfo is None:
                args[key] = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
    history = args.get('history') is True
    overview = not args.get('reference') and not history
    result = list_reports(ctx, kind=kind, reference=args.get('reference', ''),
                          keyword=args.get('keyword', ''), start=args.get('start', ''), end=args.get('end', ''),
                          limit=min(int(args.get('limit') or 5), 20), offset=int(args.get('offset') or 0), detail=True,
                          active_only=overview, latest_per_workflow=overview, category=args.get('category', ''),
                          quality_stage=args.get('quality_stage', ''))
    label = '质检' if result['kind'] == 'quality' else '设备检查'
    result['queriedAt'] = datetime.now(timezone.utc).isoformat()
    result['scope'] = {k: args[k] for k in ('kind', 'reference', 'keyword', 'category', 'quality_stage', 'history', 'start', 'end') if k in args}
    result['evidenceSummary'] = report_evidence_summary(result['items'])
    totals = result['evidenceSummary']
    lines = [f'本次读取 {totals["reportCount"]} 份已保存的{label}报告，涉及 {totals["workflowCount"]} 个不同工作流。',
             '执行完成只表示流程成功运行；无新增记录不能认定质检合格或设备正常。']
    if history:
        scope = args.get('keyword') or '指定范围'
        category = {'inspection': '巡检', 'maintenance': '保养', 'repair': '维修'}.get(args.get('category'), label)
        lines = [f'已读取平台保存的{scope}历史{category}报告，按运行时间从新到旧列出最近 {len(result["items"])} 次已完成记录。']
    if not result['items']:
        lines = [f'当前范围内没有可查看的已完成{label}报告。可在运行结果中调整时间范围，或查看对应工作流的运行状态。']
    for item in result['items']:
        summary = item.get('summary') or {}
        item['executionStatus'] = item.get('status')
        item['businessStatus'] = item.get('outcome', 'unknown')
        item['attentionItems'] = [r for r in item.get('data', []) if r.get('结果') in {'不合格', '预警', '待确认'}][:5]
        if overview and item.get('historicalRevision'):
            lines += ['', f"**[{item['workflowName']}]({item['reportUrl']})**",
                      '检查规则已更新，最近保存的报告属于旧版本；当前结果等待下一次定时检查。历史问题不作为本次的当前预警。']
            continue
        lines += ['', f"**[{item['workflowName']}]({item['reportUrl']})**",
                  f"检查 {summary.get('records', '—')} 个对象、{summary.get('items', '—')} 项；"
                  f"异常 {summary.get('issues', '—')} 项，待确认 {summary.get('pending', '—')} 项。"]
        if history and item.get('createTime'):
            occurred = datetime.fromisoformat(item['createTime'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai'))
            lines.append(f"运行时间：{occurred:%Y-%m-%d %H:%M:%S}（北京时间）；修订 v{item.get('revision', '—')}。")
        if item.get('outcome') == 'no_data':
            lines.append('这次运行已完成，但当时没有可判定的记录，不能据此认定全部合格。')
        elif item.get('activity') == 'no_tasks':
            lines.append('本期没有需要检查的维修任务，不产生预警或待确认项。')
        elif item.get('outcome') == 'no_changes':
            lines.append('这段采集时间内没有新增或更新记录，历史检查结果仍然保留。')
        if item.get('window'):
            lines.append(f"采集范围：{item['window'].get('start', '—')} 至 {item['window'].get('end', '—')}。")
        rows = [r for r in item.get('data', []) if r.get('结果') not in {'正常', '合格'}][:3]
        for row in rows:
            subject = row.get('单据') or row.get('设备') or ''
            detail = f"标准 {row['标准']}，实测 {row['实测值']}。" if '标准' in row else ''
            lines.append(f"- {subject} · {row.get('项目', '')}：{row.get('结果', '')}。{detail}{row.get('说明', '')}")
    result['message'] = '\n'.join(lines)
    # A conversation needs a compact receipt, while the UI detail API keeps all rows.
    result['items'] = [{k: v for k, v in r.items() if k not in {'data', 'content', 'groups'}} for r in result['items']]
    result['resultType'] = 'workflow_reports'
    return result


def report_evidence_summary(items):
    """为会话回执计算"证据计数"，并明确计数口径的边界。

    只有真正做过判定的报告才计入 checkedItems/failedItems/pendingItems：
    outcome 属于 normal/issues/pending 且 summary.items > 0，且不是历史修订。
    其余（无新增记录、扫描未完成、旧修订、只有正文的历史报告）计入 notEvaluatedReports。
    ``allReturnedChecksPassed`` 要求"返回了报告、全部可评估、且无问题无待确认"三者同时成立，
    避免空结果集被误读成"全部合格"。
    """
    evaluated = [r for r in items if not r.get('historicalRevision')
                 and r.get('outcome') in {'normal', 'issues', 'pending'} and (r.get('summary') or {}).get('items', 0) > 0]
    return {
        'reportCount': len(items),
        'workflowCount': len({r.get('workflowId') or r.get('workflowName') for r in items}),
        'checkedItems': sum((r.get('summary') or {}).get('items', 0) for r in evaluated),
        'failedItems': sum((r.get('summary') or {}).get('issues', 0) for r in evaluated),
        'pendingItems': sum((r.get('summary') or {}).get('pending', 0) for r in evaluated),
        'notEvaluatedReports': len(items) - len(evaluated),
        'allReturnedChecksPassed': bool(items) and len(evaluated) == len(items)
            and all(r['summary'].get('issues', 0) == 0 and r['summary'].get('pending', 0) == 0 for r in evaluated),
        'scopeNote': '以上计数仅针对本次返回报告；无新增记录、未检查、待确认均不等于合格，不代表全库或全厂状态。',
    }
