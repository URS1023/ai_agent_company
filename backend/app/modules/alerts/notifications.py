"""Transactional in-app delivery for a persisted equipment alert report.

职责：预警通知节点 —— 依据节点配置把"本次预警报告"投递到站内信（``system_inbox_messages``）。
与报告写入在同一事务中完成（只 ``flush``，由外层引擎提交），因此"有报告必有通知/要么都没有"。

位置与依赖：``alerts.reports.evaluate_alert`` 的下游节点，依赖 ``system_tools.notifications``
的收件人解析（``_recipient_ids``，跨模块复用其 users/roles/all 口径）。

重要约束与已知问题：
- 报告必须属于**本次执行**（``AlertReport.execution_id == execution_id``），否则拒绝，
  避免把历史报告或他人的报告重新播报一遍。
- 投递对象：``executor`` 只发给执行者本人，其余（如 ``all``）取工作区内全部在职成员。
- **通知去重缺失（已知问题）**：写入时 ``notice_id=None``，而唯一约束是
  ``(workspace_id, notice_id, recipient_user_id)``；在 SQL 中 NULL 不参与唯一性判定，
  因此约束不会阻止重复行。加上报告的唯一键是 ``(execution_id, node_id)``，
  **每轮调度都会生成新执行、新报告**，于是同一台设备的同一问题在连续几轮中会重复投递，
  收件人可能看到多条内容相近的站内信。当前依赖人工忽略，如需修正应改为按
  (报告, 收件人) 幂等（例如写入前查重或给 notice_id 绑定稳定业务标识）。
- 本模块不产生审计记录；不修改报告与问题。
"""
from __future__ import annotations

from sqlalchemy import select

from app.errors import NotFoundError, ValidationError
from app.modules.alerts.models import AlertReport
from app.modules.system_tools.models import InboxMessage
from app.modules.system_tools.notifications import _recipient_ids

_SEVERITY = {"info": 0, "warning": 1, "critical": 2}
_OUTCOME = {"issues": "发现问题", "no_data": "无数据，未完成判定", "normal": "未发现问题"}


def notify_alert(ctx, config, value, *, execution_id: str) -> dict:
    """Append inbox rows in the same transaction as the workflow node output.

    （中文补充）入参 ``value`` 必须是预警报告节点输出（含 ``alertReportId``）；
    ``config`` 含 ``outcomes``（允许通知的结论集合，如仅 issues/no_data）与
    ``minimum_severity``（问题等级下限）与 ``audience``（executor/其他）。
    返回原 ``value`` 并附加 ``notification``，其中 ``sent`` 为假时带
    ``reason``：``outcome_not_selected`` / ``below_minimum_severity``（跳过不算失败，
    不会中断流程）。报告不存在或不属于本次执行时抛 ``NotFoundError``；
    ``value`` 形状不对时抛 ``ValidationError``。

    等级计算：取本次问题中的最高 ``severity``（info<warning<critical）；无问题时，
    ``no_data`` 记为 warning，其余记为 info —— 即"无数据"也要提醒，不当作正常。

    副作用：为每个收件人新增一条 ``InboxMessage``（``message_type='alert'``，
    ``notice_id=None``）并 flush；不去重、不写审计（见模块 docstring 的已知问题）。
    """
    if not isinstance(value, dict):
        raise ValidationError("预警通知节点需要预警报告对象输入")
    report_id = value.get("alertReportId")
    if not isinstance(report_id, str):
        raise ValidationError("预警通知节点未收到实际报告标识")
    report = ctx.db.scalar(select(AlertReport).where(
        AlertReport.workspace_id == ctx.workspace_id,
        AlertReport.id == report_id,
        AlertReport.execution_id == execution_id,
    ))
    if report is None:
        raise NotFoundError("预警通知引用的报告不存在或不属于本次执行")
    outcome = str(value.get("outcome") or report.outcome)
    if outcome not in config.outcomes:
        return {**value, "notification": {"sent": False, "recipientCount": 0, "reason": "outcome_not_selected"}}

    issue_levels = [str(item.get("severity") or "info") for item in value.get("issues") or [] if isinstance(item, dict)]
    severity = max(issue_levels, key=lambda item: _SEVERITY.get(item, -1)) if issue_levels else (
        "warning" if outcome == "no_data" else "info"
    )
    if outcome == "issues" and _SEVERITY.get(severity, -1) < _SEVERITY[config.minimum_severity]:
        return {**value, "notification": {"sent": False, "recipientCount": 0, "reason": "below_minimum_severity"}}

    recipients = [ctx.user_id] if config.audience == "executor" else _recipient_ids(ctx, {"type": "all", "ids": []})
    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
    body = (
        f"{report.snapshot['device']['name']} · {_OUTCOME.get(outcome, outcome)}。"
        f"参与记录 {int(summary.get('rows') or 0)} 条，问题 {int(summary.get('issues') or 0)} 项。"
        f"报告编号：{report.id}。请在设备预警的问题列表中查看证据并处置。"
    )
    for user_id in recipients:
        ctx.db.add(InboxMessage(
            workspace_id=ctx.workspace_id, notice_id=None, recipient_user_id=user_id,
            subject=report.title, body=body, message_type="alert", severity=severity,
        ))
    ctx.db.flush()
    return {**value, "notification": {"sent": True, "recipientCount": len(recipients), "severity": severity}}
