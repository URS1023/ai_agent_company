"""复用同身份同范围的持久回执，兼容已有业务任务的续办快照。

保留旧快照字段以读取既有任务；本模块不依赖任何对话引擎或模型连接。
"""
from __future__ import annotations

from copy import deepcopy
import re

from sqlalchemy import select

from ...models import Run, ToolCall
from ...common.ordered_rows import ordered_payload_rows


_CONTINUE = re.compile(r"(?:请|帮我)?\s*(?:继续(?:生成|完成|执行|处理)?|接着(?:完成|执行|处理))(?:一下|吧)?[。！!\s]*")
_RETRY = re.compile(r"(?:请|帮我|再)?\s*(?:重新生成|重试)(?:一下|吧|一次)?[。！!\s]*")
_ACTIVE = {"created", "queued", "pending", "running", "stopping"}


def is_continue_request(content: str) -> bool:
    """只有纯继续指令才表示沿用原任务，带修改条件的消息属于新请求。"""
    return bool(_CONTINUE.fullmatch(str(content or "").strip()))


def original_request(snapshot: dict) -> str:
    """查找服务端已经保存的实质诉求，不把连续多轮的“继续”当成需求。"""
    resume = snapshot.get("task_resume") or {}
    for value in (snapshot.get("resolved_user_request"), snapshot.get("user_request"), resume.get("original_request")):
        text = str(value or "").strip()
        if text and not is_continue_request(text) and not _RETRY.fullmatch(text):
            return text
    return ""


def resource_scope(snapshot: dict, plugin_version_id: str | None = None) -> dict:
    """统一资源默认值；切换模型不会扩大数据权限，因此不属于业务范围。"""
    inputs = snapshot.get("plugin_inputs") or {}
    return {
        **{key: sorted(set(snapshot.get(key) or []))
           for key in ("file_ids", "knowledge_base_ids", "datasource_ids")},
        "plugin_inputs": inputs,
        "plugin_version_id": plugin_version_id,
        "template_id": snapshot.get("template_id") or inputs.get("template_id") or "executive-ivory",
        "dashboard_template_mode": snapshot.get("dashboard_template_mode") or "auto",
        "dashboard_template_id": snapshot.get("dashboard_template_id"),
        "mode": snapshot.get("mode") or "standard",
    }


def same_resource_scope(previous: Run, payload) -> bool:
    """移除、增添或替换任一资源都取消继承，后续工具仍按当前权限重新鉴权。"""
    if isinstance(payload, Run):
        current = payload.context_snapshot or {}
        plugin_version_id = payload.plugin_version_id
    else:
        current = payload.model_dump()
        plugin_version_id = payload.plugin_version_id
    return resource_scope(previous.context_snapshot or {}, previous.plugin_version_id) == resource_scope(current, plugin_version_id)


def latest_chat_run(db, workspace_id: str, user_id: str, conversation_id: str) -> Run | None:
    """只看最新真实聊天轮次，不能越过新主题或其他身份回捞更早的任务。"""
    candidates = ordered_payload_rows(db, Run, (
        Run.workspace_id == workspace_id, Run.conversation_id == conversation_id,
    ), (Run.created_at.desc(), Run.id.desc()), limit=20)
    previous = next((row for row in candidates if (row.context_snapshot or {}).get("user_message_id")), None)
    if previous is None or previous.started_by_user_id != user_id or previous.status in _ACTIVE:
        return None
    return previous


def unfinished_task_for_continue(ctx, conversation_id: str, payload) -> Run | None:
    """恢复最近未完的原生任务；取消任务同样必须等用户主动发出继续指令。"""
    if not is_continue_request(payload.content):
        return None
    previous = latest_chat_run(ctx.db, ctx.workspace_id, ctx.user_id, conversation_id)
    if previous is None or not same_resource_scope(previous, payload):
        return None
    snapshot = previous.context_snapshot or {}
    if not original_request(snapshot) or not (snapshot.get("openwebui") or {}).get("chat_id"):
        return None
    delivery = snapshot.get("delivery") or {}
    check = snapshot.get("completion_check") or {}
    tasks = snapshot.get("openwebui_tasks") or []
    pending_tasks = any(isinstance(task, dict) and task.get("status") not in {"completed", "cancelled", "skipped"}
                        for task in tasks)
    incomplete = (previous.status in {"failed", "cancelled", "interrupted", "stopped"}
                  or bool(delivery.get("missing")) or pending_tasks
                  or check.get("execution_complete") is False
                  or bool(check.get("missing_tool_results")) or bool(check.get("unfinished_ids")))
    return previous if incomplete else None


def _same_task(previous: Run, current: Run) -> bool:
    """回执归属须同时满足用户、工作区、会话、请求和资源范围。"""
    return (
        (previous.workspace_id, previous.started_by_user_id, previous.conversation_id)
        == (current.workspace_id, current.started_by_user_id, current.conversation_id)
        and same_resource_scope(previous, current)
        and bool(original_request(previous.context_snapshot or {}))
        and original_request(previous.context_snapshot or {}) == original_request(current.context_snapshot or {})
    )


def validated_resume_calls(db, run: Run) -> list[ToolCall]:
    """读取经过复核的历史成功回执，不执行工具，也不把失败调用当作自动重试指令。"""
    snapshot = run.context_snapshot or {}
    if snapshot.get("chat_engine") == "dsh":
        return _validated_dsh_task_calls(db, run)
    source_id = snapshot.get("resumed_from_run_id")
    previous = db.get(Run, source_id) if source_id else None
    if previous is None or previous.id == run.id or previous.status in _ACTIVE or not _same_task(previous, run):
        return []
    resume = snapshot.get("task_resume") or {}
    inherited_ids = [value for value in resume.get("successful_tool_call_ids", []) if isinstance(value, str)]
    # 兼容尚未保存通用恢复清单的 PPT 续写记录；已有清单时不隐式扩展到后来新增的回执。
    filters = [ToolCall.workspace_id == run.workspace_id, ToolCall.status == "completed"]
    if "successful_tool_call_ids" in resume:
        filters.append(ToolCall.id.in_(inherited_ids))
    else:
        filters.append(ToolCall.run_id == previous.id)
    calls = ordered_payload_rows(db, ToolCall, filters, (ToolCall.completed_at, ToolCall.id))
    owner_runs = {row.id: row for row in db.scalars(select(Run).where(Run.id.in_({call.run_id for call in calls}))).all()}
    allowed_runs = set(resume.get("source_run_ids") or [previous.id])
    return [call for call in calls if call.run_id != run.id and call.run_id in allowed_runs and call.run_id in owner_runs
            and (call.result_json or {}).get("status") == "success"
            and owner_runs[call.run_id].status not in _ACTIVE
            and owner_runs[call.run_id].created_at <= previous.created_at
            and _same_task(owner_runs[call.run_id], run)]


def _validated_dsh_task_calls(db, run: Run) -> list[ToolCall]:
    """原生任务跨轮共享查询证据，允许修改交付标题、模板和页数，不扩大数据范围。"""
    snapshot = run.context_snapshot or {}
    task_id = snapshot.get("task_id")
    if not task_id or (snapshot.get("business_task") or {}).get("task_id") != task_id:
        return []
    candidates = db.scalars(select(Run).where(
        Run.workspace_id == run.workspace_id, Run.started_by_user_id == run.started_by_user_id,
        Run.conversation_id == run.conversation_id, Run.id != run.id,
        Run.created_at <= run.created_at, Run.status.not_in(_ACTIVE),
        Run.context_snapshot["chat_engine"].as_string() == "dsh",
        Run.context_snapshot["task_id"].as_string() == task_id,
    )).all()
    # 原话在“改成另一种文件”时必然变化；资源身份仍必须与原查询任务一致。
    scope_keys = ("datasource_ids", "file_ids", "knowledge_base_ids")
    run_ids = [previous.id for previous in candidates
        if ((previous.context_snapshot or {}).get("business_task") or {}).get("task_id") == task_id
        and previous.plugin_version_id == run.plugin_version_id
        and all(set((previous.context_snapshot or {}).get(key) or []) == set(snapshot.get(key) or [])
                for key in scope_keys)]
    if not run_ids:
        return []
    calls = ordered_payload_rows(db, ToolCall, (
        ToolCall.workspace_id == run.workspace_id, ToolCall.run_id.in_(run_ids),
        ToolCall.status.in_(["completed", "success"]),
    ), (ToolCall.completed_at, ToolCall.id))
    return [call for call in calls if isinstance(call.result_json, dict)
            and call.result_json.get("status") == "success"]


def resume_snapshot(db, previous: Run) -> dict:
    """建立新运行的恢复合同；仅复制进度与回执引用，不复制租约、终态和模型调用。"""
    snapshot = previous.context_snapshot or {}
    inherited = validated_resume_calls(db, previous)
    current = ordered_payload_rows(db, ToolCall, (
        ToolCall.workspace_id == previous.workspace_id, ToolCall.run_id == previous.id,
        ToolCall.status == "completed",
    ), (ToolCall.completed_at, ToolCall.id))
    successes = {call.id: call for call in [*inherited, *current]
                 if (call.result_json or {}).get("status") == "success"}
    tasks = deepcopy(snapshot.get("openwebui_tasks") or [])
    delivery = deepcopy(snapshot.get("delivery") or {})
    return {
        "user_request": original_request(snapshot),
        "resumed_from_run_id": previous.id,
        "tool_failures": deepcopy(snapshot.get("tool_failures") or {}),
        "openwebui_tasks": tasks,
        "task_resume": {
            "version": 1,
            "source_run_id": previous.id,
            "source_status": previous.status,
            "original_request": original_request(snapshot),
            "source_run_ids": list(dict.fromkeys([previous.id, *(call.run_id for call in successes.values())])),
            "successful_tool_call_ids": list(successes),
            "openwebui_tasks": tasks,
            "delivery": delivery,
            "completion_check": deepcopy(snapshot.get("completion_check") or {}),
            "policy": "只补齐未完成项；成功回执仅作为证据复用，禁止自动重放副作用或重新查询已取得的数据。",
        },
    }
