"""Durable collection windows; only a complete successful batch advances the watermark.

中文补充：为"按时间增量采集"的流程提供持久化采集窗口。窗口状态存在
``workflow_business_states``（state_key='collection-window'）里，按流程唯一。

关键设计：
- 采集区间可能跨多轮（``hasMore``），只有整轮成功结束后才允许推进水位线；
  中途失败时水位线保持不动，下一轮会重新检查同一批记录——宁可重复检查，不可漏检。
- 人工指定窗口（windowStart/windowEnd）只是历史复核，绝不改动定时采集的水位线。
- 推进水位线用乐观锁：先读旧值作为 ``expected``，写回时比对，不一致就抛 ConflictError。

已知行为（重要）：``finish_window`` 抛出的 ConflictError 由引擎在
``nodes.py``/``engine.py`` 之外的收尾逻辑捕获——``execute_graph`` 结尾把提交扫描/窗口
进度失败统一转成 ``failure`` 文案，"本轮结果已保留，但扫描进度未能保存"，
于是这一次运行会被标记为 **failed**，即使所有节点本身都执行成功。
这是刻意取舍：进度没落盘就意味着下一轮会重复检查，必须让运行状态提示运维关注。
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.errors import ConflictError, ValidationError
from app.modules.workflows.models import WorkflowBusinessState, WorkflowExecution

STATE_KEY = 'collection-window'


def local_time(value, zone):
    """把前端传入的时间字符串解析成指定时区的 naive 本地时间。

    带时区的时间会先转换到目标时区；不带时区的字符串按本地时间直接使用
    （前端表单提交的就是本地时间）。解析失败抛 ValidationError，
    由调用方转成"请选择开始与结束时间"的用户提示。
    """
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if date.tzinfo:
            date = date.astimezone(ZoneInfo(zone)).replace(tzinfo=None)
        return date
    except (TypeError, ValueError) as exc:
        raise ValidationError('采集时间无效，请选择开始与结束时间') from exc


def prepare_window(ctx, execution, config):
    """在开始节点决定本轮采集区间，并返回可写回结果 JSON 的窗口对象。

    优先级：人工指定（windowStart/windowEnd）> 上次未完成的 pending 窗口 > 上次水位线。
    首次运行且没有水位线时回看 ``lookbackMinutes``，并会向上查找最近一条同样没有人工窗口的
    已完成运行，用它的创建时间作为起点，避免调度间隔变化导致漏采。

    返回字典含 start/end/timeZone/mode/afterKey/hasMore 以及 ``expected``
    （读取到的旧状态，供 ``finish_window`` 做乐观锁比对）。

    异常：时间缺一不可、先后顺序错误、晚于当前时间、跨度超过十年、续查位置为负时抛 ValidationError。
    副作用：只读取业务状态与历史运行，不写库。
    """
    previous = ctx.db.scalar(select(WorkflowBusinessState).where(
        WorkflowBusinessState.workspace_id == ctx.workspace_id,
        WorkflowBusinessState.workflow_id == execution.workflow_id,
        WorkflowBusinessState.state_key == STATE_KEY))
    saved = deepcopy(previous.value or {}) if previous else {}
    zone = config.get('timeZone', 'Asia/Shanghai')
    now = execution.created_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)
    inputs = execution.input_json or {}
    start, end = inputs.get('windowStart'), inputs.get('windowEnd')
    manual = bool(start or end)
    if manual and not (start and end):
        raise ValidationError('自定义采集需要同时选择开始时间和结束时间')
    if manual:
        begin, finish = local_time(start, zone), local_time(end, zone)
        after = int(inputs.get('windowAfterKey') or 0)
    elif saved.get('pending'):
        begin, finish = local_time(saved['start'], zone), local_time(saved['end'], zone)
        after = int(saved.get('afterKey') or 0)
    else:
        begin = local_time(saved['watermark'], zone) if saved.get('watermark') else now - timedelta(minutes=config.get('lookbackMinutes', 30))
        if not saved.get('watermark'):
            last = ctx.db.execute(select(WorkflowExecution.created_at, WorkflowExecution.input_json).where(
                WorkflowExecution.workflow_id == execution.workflow_id, WorkflowExecution.workspace_id == ctx.workspace_id,
                WorkflowExecution.status == 'completed', WorkflowExecution.id != execution.id,
                WorkflowExecution.created_at < execution.created_at).order_by(WorkflowExecution.created_at.desc()).limit(10)).all()
            automatic = next((date for date, values in last if not (values or {}).get('windowStart')), None)
            if automatic:
                begin = automatic.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)
        finish, after = now, 0
    if begin >= finish or finish > now + timedelta(seconds=5) or finish - begin > timedelta(days=3660):
        raise ValidationError('请选择先后顺序正确且不晚于当前时间的采集范围（最长十年）')
    if after < 0:
        raise ValidationError('续查位置无效')
    return {'start': begin.isoformat(' '), 'end': finish.isoformat(' '), 'timeZone': zone,
            'mode': 'manual' if manual else 'scheduled', 'afterKey': after, 'hasMore': False,
            'expected': saved}


def get_window(ctx, execution_id):
    """读取本次执行已保存的采集窗口；供数据库查询节点把 windowStart/windowEnd/afterKey 绑进 SQL。

    执行记录不存在或不属于当前工作区时抛 ValidationError，不允许跨工作区猜测窗口。
    """
    row = ctx.db.get(WorkflowExecution, execution_id)
    if not row or row.workspace_id != ctx.workspace_id:
        raise ValidationError('采集窗口不属于当前运行')
    return deepcopy((row.result_json or {}).get('collectionWindow') or {})


def finish_window(ctx, execution, trace):
    """整轮成功后推进采集水位线；重复执行与并发冲突都有明确语义。

    实现要点：
    - 先从本次运行 trace 里收集所有已成功节点的 ``windowBatch``，取最大 afterKey 作为
      本轮进度；任一节点失败时这些节点状态不是 completed，因此不会被计入（不因部分成功而推进）。
    - 人工窗口直接返回，历史复核不移动定时采集边界。
    - 先用 ``with_for_update()`` 锁行再比对 ``expected``：同一个执行重复提交直接返回（幂等），
      其他执行已推进则抛 ConflictError（见模块 docstring 说明的"标记为失败"行为）。

    异常：ConflictError（进度被其他轮次更新）；副作用：写入/更新
    ``workflow_business_states`` 的采集窗口状态，不修改任何来源数据。
    """
    window = trace.get('collectionWindow')
    if not window:
        return
    batches = [(n.get('output') or {}).get('windowBatch') for n in trace.get('nodes', {}).values()
               if isinstance(n.get('output'), dict) and n.get('status') == 'completed']
    batches = [b for b in batches if b]
    window['hasMore'] = any(b['hasMore'] for b in batches)
    if batches:
        window['afterKey'] = max(b['afterKey'] for b in batches)
    if window['mode'] == 'manual':
        return  # Historical checks must never move the scheduled collection boundary.
    state = ctx.db.scalar(select(WorkflowBusinessState).where(
        WorkflowBusinessState.workspace_id == ctx.workspace_id,
        WorkflowBusinessState.workflow_id == execution.workflow_id,
        WorkflowBusinessState.state_key == STATE_KEY).with_for_update())
    current = state.value if state else {}
    if current.get('executionId') == execution.id:
        return
    if current != window['expected']:
        raise ConflictError('另一轮已更新采集进度，本轮不覆盖新的进度')
    if state is None:
        state = WorkflowBusinessState(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                      workflow_id=execution.workflow_id, state_key=STATE_KEY, value={})
        ctx.db.add(state)
    state.value = {'watermark': current.get('watermark', window['start']) if window['hasMore'] else window['end'],
                   'start': window['start'], 'end': window['end'], 'pending': window['hasMore'],
                   'afterKey': window['afterKey'] if window['hasMore'] else 0, 'executionId': execution.id}


def batch_receipt(window, rows, key, max_rows):
    """为单次分批查询生成回执，校验"增量批次必须按唯一递增位置推进"。

    规则：位置列必须是整数（布尔不算）、严格递增且不重复、不能小于等于本轮起始 afterKey；
    ``len(rows) >= max_rows`` 视为"可能还有下一批"，置 hasMore。
    异常：ValidationError。仅返回回执字典，不写库（写库在 finish_window 统一完成）。
    """
    keys = [r.get(key) for r in rows]
    if any(isinstance(k, bool) or not isinstance(k, int) for k in keys) or keys != sorted(set(keys)):
        raise ValidationError('增量批次必须使用唯一递增的记录位置')
    if keys and min(keys) <= window['afterKey']:
        raise ValidationError('增量批次位置不能倒退')
    return {'afterKey': keys[-1] if keys else window['afterKey'], 'hasMore': len(rows) >= max_rows,
            'start': window['start'], 'end': window['end'], 'rows': len(rows)}
