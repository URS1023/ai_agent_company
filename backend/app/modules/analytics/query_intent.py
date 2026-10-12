"""Server-owned user turns for conversational SQL; not assistant claims or results."""
from __future__ import annotations

import re
from datetime import timezone, timedelta

from app.common.ordered_rows import ordered_payload_rows
from app.errors import PermissionErrorEx
from app.models import Conversation, Run
from app.modules.analytics.semantic import query_semantics


def explicit_user_sql(request: str) -> str | None:
    """Recognize only an entire SQL request, never SQL invented by the planner."""
    text = str(request or '').strip()
    fenced = re.fullmatch(r'```(?:sql|mysql|postgresql|sqlite)?\s*\n(.*?)\n```', text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    if re.match(r'^(?:SELECT|WITH)\b', text, re.I):
        return text  # The normal AST authorization still validates every statement.
    return None


def is_followup(request: str) -> bool:
    """判断本轮是否像追问（保留上文范围）还是新话题（不继承历史）。

    先看语义层的追问标记词，再补一组“质疑/确认上一步结果”的口语表达（对吗、查了吗、
    前面/上面/这批等）。已知边界：纯正则判断，问句里出现“这些”等指示词但实际是
    新话题时会被视为追问，因此调用方只把它当作**是否携带上文**的提示，最终范围仍以
    服务端保存的用户原话为准。
    """
    return bool(query_semantics(request)['followUpCandidate'] or re.search(
        r'对吗|正确吗|准确吗|没查|没有查|查了吗|还没|还没有|前面|上面|这批|这个|这些|改成|改为|换成|调整为|'
        r'\b(?:these|that|same|still|really|correct)\b', request, re.I))


def query_context(ctx, tool_context: dict, source_id: str, tool_question: str) -> dict | None:
    """Resolve only this actor's run, with a bounded contiguous follow-up chain.

    Failed or unanswered user turns still contain requirements. They supply no
    evidence of a successful query. Explicit new topics do not inherit history.
    """
    run_id = tool_context.get('run_id')
    run = ctx.db.get(Run, run_id) if run_id else None
    if run is None:
        return None  # Direct non-conversation tool calls have no user-turn context.
    if run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        raise PermissionErrorEx('不能读取其他用户或工作区的查询上下文')
    selected_sources = (run.context_snapshot or {}).get('datasource_ids') or []
    if selected_sources and source_id not in selected_sources:
        raise PermissionErrorEx('查询数据源不在服务端保存的本次选择范围内')
    if not run.conversation_id:
        return None
    conversation = ctx.db.get(Conversation, run.conversation_id)
    if (conversation is None or conversation.workspace_id != ctx.workspace_id
            or conversation.owner_user_id != ctx.user_id):
        raise PermissionErrorEx('查询会话不属于当前用户')
    if tool_context.get('conversation_id') not in (None, '', run.conversation_id):
        raise PermissionErrorEx('查询上下文与当前会话不一致')
    run_snapshot = run.context_snapshot or {}
    current = run_snapshot.get('user_request')
    if not isinstance(current, str) or not current.strip():
        return None
    previous = []
    steering = [item['content'] for item in run_snapshot.get('steering_messages') or []
                if isinstance(item, dict) and item.get('accepted') is True and isinstance(item.get('content'), str)]
    same_run_previous = []
    if steering:
        submitted = run_snapshot.get('submitted_content')
        same_run_previous = ([submitted] if isinstance(submitted, str) and submitted else []) + steering[:-1]
    first_request = same_run_previous[0] if same_run_previous else current
    if is_followup(first_request):
        candidates = ordered_payload_rows(ctx.db, Run, (
            Run.workspace_id == ctx.workspace_id, Run.started_by_user_id == ctx.user_id,
            Run.conversation_id == run.conversation_id, Run.created_at <= run.created_at,
            Run.id != run.id,
        ), (Run.created_at.desc(), Run.id.desc()), limit=12)
        # Some databases store timestamps only to the second. ULID randomness
        # within a millisecond does not encode message order; use the server's
        # persisted message sequence when timestamps tie.
        candidates.sort(key=lambda item: (item.created_at,
            int((item.context_snapshot or {}).get('user_message_sequence') or 0)), reverse=True)
        current_sequence = (run.context_snapshot or {}).get('user_message_sequence')
        remaining = 16000
        for candidate in candidates:
            snapshot = candidate.context_snapshot or {}
            sequence = snapshot.get('user_message_sequence')
            if current_sequence and sequence and sequence >= current_sequence:
                continue
            request = snapshot.get('user_request')
            if not isinstance(request, str) or not request.strip():
                continue
            sources = snapshot.get('datasource_ids') or []
            if sources and source_id not in sources:
                break  # Never jump across an explicit source change.
            if len(request) > remaining:
                break  # Do not truncate away a qualifier or negation.
            previous.append({'runId': candidate.id, 'request': request})
            remaining -= len(request)
            if len(previous) >= 6 or not is_followup(request):
                break
    # 相对月份固定使用提交任务时的服务端日期；长任务跨日与数据库时区不改变查询范围。
    submitted_at = run.created_at.replace(tzinfo=timezone.utc) if run.created_at.tzinfo is None else run.created_at
    return {'runId': run.id, 'conversationId': run.conversation_id,
            'currentRequest': current, 'previousUserTurns': [*reversed(previous),
                *({'runId': run.id, 'request': request, 'source': 'steering'} for request in same_run_previous)],
            'plannerTask': tool_question[:8000],
            'referenceDate': submitted_at.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            'referenceTimezone': 'Asia/Shanghai'}
