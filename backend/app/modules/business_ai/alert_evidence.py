"""Present saved alert facts without inferring physical condition or handling."""
from __future__ import annotations

import html
import re


OUTCOME_LABELS = {'issues': '发现问题', 'normal': '本次检查未发现问题', 'no_data': '无数据，不能判定正常'}
EXECUTION_LABELS = {'completed': '执行完成', 'failed': '执行失败', 'running': '执行中',
                    'created': '待执行', 'cancelled': '已取消'}
HANDLING_LABELS = {'open': '待处理', 'acknowledged': '已确认', 'resolved': '已解决', 'ignored': '已忽略'}
SEVERITY_LABELS = {'info': '提示', 'warning': '警告', 'critical': '严重'}


def label(mapping: dict[str, str], value: str | None) -> str:
    """把枚举值翻成中文标签；未记录与未知值分开表达（未知保留原值便于排查）。"""
    return mapping.get(value or '', '状态未记录' if not value else '未知状态：' + value)


def _text(value) -> str:
    """聊天消息里的安全文本：先 HTML 转义，再转义 Markdown 元字符，换行转 <br>。

    顺序不能颠倒（先转义 HTML 再 Escape Markdown），否则模型或数据里的标签会被当成
    结构渲染；超过 600 字符截断并显式标注，避免把长记录刷满聊天窗口。
    """
    if value is None or value == '':
        return '未记录'
    text = str(value)
    if len(text) > 600:
        text = text[:600] + '…（仅展示前600字符）'
    return re.sub(r'([\\`*\[\]_|])', r'\\\1', html.escape(text)).replace('\r', '').replace('\n', '<br>')


def alert_evidence_message(view: dict) -> str:
    """依据授权页渲染事实，并明确报告页与问题页的关联范围。"""
    reports, problems = view['reports'], view['problems']
    summary = view['summary']
    lines = ['已读取当前权限范围内保存的设备预警记录。',
             '报告判定、工作流执行状态和人工处置状态分别列示；执行完成不表示问题已解决。',
             f"本页返回 {len(reports)} 份报告、{len(problems)} 条问题；这些数量不是全工作区总数。"]
    if summary.get('relationship') == 'independent_recent_records':
        lines.append('报告和问题是独立分页的最近记录；本页问题数不代表这些报告的全部问题。')
        outside = summary.get('returnedProblemsOutsideReportPage', 0)
        if outside:
            lines.append(f'本页有 {outside} 条问题对应的报告未在本报告页内；可按 reportId 读取对应报告。')
    if summary.get('reportsMayHaveMore') or summary.get('problemsMayHaveMore'):
        lines.append('后面可能还有记录，可调整分页或进入问题列表查看。')
    if reports:
        lines += ['', '| 报告 / 设备 | 原始判定 | 工作流执行 | 检查记录数 | 原始问题数 | 报告时间 |',
                  '| --- | --- | --- | --- | --- | --- |']
        for row in reports:
            lines.append(f"| [{_text(row['title'])}]({row['href']}) / {_text(row['deviceName'])} | "
                         f"{_text(row['outcomeLabel'])} | {_text(row['executionStatusLabel'])} | "
                         f"{_text(row['rows'])} | {_text(row['issues'])} | {_text(row['createTime'])} |")
    if problems:
        lines += ['', '问题记录及当前人工处置状态：']
        for row in problems:
            lines.append(f"- [{_text(row['message'] or row['ruleName'] or '查看问题')}]({row['href']})；"
                         f"原始问题等级：{_text(SEVERITY_LABELS.get(row.get('severity'), row.get('severity')))}；"
                         f"状态：{_text(row['statusLabel'])}；记录时间：{_text(row['createTime'])}；"
                         f"最新处置说明：{_text(row.get('note'))}。")
    history = view.get('processingHistory')
    if history is not None:
        lines += ['', '已读取该问题的处置变更日志：']
        for event in history['events']:
            lines.append(f"- {_text(event['createTime'])}："
                         f"{_text(label(HANDLING_LABELS, event['previousStatus']))} → "
                         f"{_text(label(HANDLING_LABELS, event['status']))}；{_text(event['note'])}。")
        if not history['events']:
            lines.append('本页未找到处置变更记录；不能据此认定现场从未处理。')
        if history['hasMore']:
            lines.append('处置日志还有后续页，本页不是完整历史。')
    else:
        lines += ['', '本次概览未读取逐条问题的处置日志。跟进某个问题时，应按问题标识读取其处置历史。']
    lines += ['', '上述为保存记录，不能据此判断设备当前实时状态、故障持续时间或恶化趋势；'
                  '巡检/保养的逾期结果不能替代该问题的处置日志。', '']
    lines.extend(f"[{_text(action['label'])}]({action['href']})" for action in view['actions'])
    return '\n'.join(lines)


def compact_alert_message(view: dict) -> str:
    """聊天正文只显示本页事实与处置结论，完整明细保留在结果卡片。"""
    summary = view.get('summary') or {}
    lines = [f"已读取保存的设备预警记录：本页 {summary.get('returnedReports', len(view.get('reports') or []))} 份报告、"
             f"{summary.get('returnedProblems', len(view.get('problems') or []))} 条问题。"]
    counts = summary.get('statusCounts') or {}
    if counts:
        lines.append('本页问题状态：' + '、'.join(f"{label(HANDLING_LABELS, status)} {count} 条" for status, count in counts.items()) + '。')
    important = [row for row in view.get('problems') or [] if row.get('severity') in {'critical', 'warning'}]
    important.sort(key=lambda row: row.get('severity') != 'critical')
    if important:
        lines.append('本页重点问题（最多列示 3 条）：\n\n' + '\n'.join(
            f"- {SEVERITY_LABELS[row['severity']]}：{_text(row.get('message') or row.get('ruleName'))}；"
            f"{label(HANDLING_LABELS, row.get('status'))}。" for row in important[:3]))
    history = view.get('processingHistory')
    if history is not None:
        lines.append('该问题当前人工处置状态：' + label(HANDLING_LABELS, history.get('currentStatus')) + '。')
        if history.get('currentNote'):
            lines.append('最新处置说明：' + _text(history['currentNote']) + '。')
        if not history.get('events'):
            lines.append('本页未找到处置变更记录；不能据此认定现场从未处理。')
        elif history.get('hasMore'):
            lines.append('已读取本页处置变更记录，还有后续页，本页不是完整历史。')
    elif summary.get('relationship') == 'independent_recent_records':
        lines.append('报告与问题分别分页；本页问题数不代表这些报告的全部问题。')
    if summary.get('reportsMayHaveMore') or summary.get('problemsMayHaveMore'):
        lines.append('仍有后续记录，本页不是全量。')
    lines.append('详细报告和问题见结果卡片；执行完成不表示问题已解决，保存记录不能证明设备当前实时状态或恶化趋势。')
    return '\n\n'.join(lines)


def verified_alert_reply(db, run, content: str, status: str, error: dict | None) -> dict | None:
    """DSH 结算时使用本轮预警回执约束纯预警回答，复合制作任务保留其正文。"""
    from sqlalchemy import select
    from app.models import Message, ToolCall
    from app.modules.runtime.report_grounding import report_answer_error

    snapshot = run.context_snapshot or {}
    request = str(snapshot.get('user_request') or snapshot.get('submitted_content') or '')
    task = snapshot.get('business_task') or {}
    if task.get('required_deliveries') or snapshot.get('required_deliveries'):
        return None
    if re.search(r'库存|仓库|备品|备件|ERP|Excel|xlsx|ppt|word|docx|pdf|文件|表格|大屏|看板', request, re.I):
        return None
    calls = list(db.scalars(select(ToolCall).where(ToolCall.run_id == run.id)
                           .order_by(ToolCall.started_at, ToolCall.id)))
    allowed = {'view_device_alerts', 'query_workflow_results', 'current_datetime'}
    if any(call.tool_name not in allowed for call in calls):
        return None
    alert_calls = [call for call in calls if call.tool_name == 'view_device_alerts']
    message = db.get(Message, snapshot.get('assistant_message_id')) if snapshot.get('assistant_message_id') else None
    denied_receipt = any(part.get('type') == 'tool_result' and part.get('tool') == 'view_device_alerts'
                         and (part.get('result') or {}).get('status') != 'success'
                         for part in (message.parts_json or []) if isinstance(part, dict)) if message else False
    if not alert_calls:
        if not denied_receipt and (not re.search(r'预警|问题|故障|处置', request) or not report_answer_error(content, None)):
            return None
        return {'content': '本轮尚未读取该预警问题的当前处置记录，不能确认已解决、已处理或持续恶化。请重新查询该问题的保存记录。',
                'status': 'partial' if status == 'completed' else status,
                'error': error or {'code': 'alert_evidence_missing', 'message': '缺少本轮预警处置证据。'}}
    receipts = []
    failed = denied_receipt
    for call in alert_calls:
        result = call.result_json or {}
        value = result.get('data') if isinstance(result.get('data'), dict) else {}
        if call.status != 'completed' or result.get('status') != 'success' or value.get('resultType') != 'alert_problem_list':
            # 后一个范围读取失败后，之前范围不能作为本次完整答复；失败保持到本轮结束。
            receipts.clear()
            failed = True
        elif not failed:
            receipts.append(value)
    if failed or not receipts:
        return {'content': '本次预警读取未全部完成，尚未核实请求范围内的全部问题及处置状态。已成功返回的局部记录可在结果卡片查看。',
                'status': 'partial' if status == 'completed' else status,
                'error': error or {'code': 'alert_evidence_incomplete', 'message': '本轮存在未成功读取的预警范围。'}}
    facts = list(dict.fromkeys(compact_alert_message(view) for view in receipts))
    workflow_calls = [call for call in calls if call.tool_name == 'query_workflow_results' and call.status == 'completed']
    for call in workflow_calls:
        data = (call.result_json or {}).get('data') or {}
        counts = data.get('evidenceSummary') or {}
        if counts.get('reportCount') is not None:
            facts.append(f"另外读取了 {counts['reportCount']} 份保存的工作流报告；该报告不能替代问题的人工处置日志。")
    if any(call.tool_name == 'query_workflow_results' and call.status != 'completed' for call in calls):
        facts.append('工作流报告读取未全部完成；上述仅为已读取的预警问题，尚未核实失败范围。')
        status = 'partial' if status == 'completed' else status
        error = error or {'code': 'report_evidence_incomplete', 'message': '本轮存在未成功读取的工作流报告。'}
    # 模型失败仍保留已经取得的事实，但不把技术失败改成成功终态。
    return {'content': '\n\n'.join(facts), 'status': status, 'error': error}
