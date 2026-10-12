"""将原生 Open WebUI 执行事件投影到现有聊天界面和审计记录。"""
from __future__ import annotations

from contextvars import copy_context
from datetime import datetime
from hashlib import sha256
import json
import logging
import queue
import threading
import time

from sqlalchemy import select

from ...core.observability import log_event
from ...core.parts import artifact_part, citation_part, error_part, text_part, tool_call_part, tool_result_part
from ...errors import AppError
from ...ids import new_id
from ...models import Conversation, Message, Run, RunStep, ToolCall
from ...providers.tool.base import ToolResult
from ...providers.tool.gateway import ToolGateway, build_context
from ..runtime.service import (create_message, start_step, complete_step, fail_step,
    record_tool_call, finish_tool_call, finalize_run, touch_conversation, record_citations)
from ..runtime.task_control import checkpoint, current, ChatCancelled, TaskControl
from .artifacts import persist_artifacts
from .client import OpenWebUIClient, BackendUnavailable
from .context import _attached_file_summaries, _kb_descriptions, _load_plugin_context
from .skills import sync_skills
from .delivery import DeliveryTracker, DeliveryIncomplete
from .task_completion import TaskCompletionGuard
from .tool_failures import ToolFailureLedger
from ..octop_bridge.trajectory.bridge import RunTrajectory
from ..octop_bridge.completion import inspect_completion

logger = logging.getLogger(__name__)


def _retryable_model_failure(error: Exception) -> bool:
    """判断模型错误是否可以安全恢复。

    只有没有形成可靠回复的连接、限流和不完整响应才会进入无限恢复；余额、权限、
    配置和参数错误直接返回上层，避免用无效请求空转。模型恢复只受用户取消控制。
    """
    code = str(getattr(error, "code", "") or "")
    status = getattr(error, "provider_status", None) or getattr(error, "status_code", None)
    if code in {"model_unavailable", "model_rate_limited", "model_response_incomplete"}:
        return True
    # 业务层已经分类的其它 AppError（余额、权限、配置、参数）不可通过重复请求修复。
    if isinstance(error, AppError):
        return False
    if isinstance(status, int) and (status == 408 or status == 425 or status == 429 or status >= 500):
        return True
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in (
        "apiconnectionerror", "connecterror", "ssleoferror", "remoteprotocolerror",
        "readtimeout", "write timeout", "connection reset", "temporarily unavailable",
    ))


def _wait_for_model_recovery(control: TaskControl, seconds: float) -> None:
    """等待供应商恢复，同时让用户取消可以立即打断等待。"""
    if control.cancelled.wait(seconds):
        checkpoint()
    checkpoint()


def legacy_content(message):
    """旧会话迁移时保留附件和工件标识，不把历史诊断堆栈塞入模型上下文。"""
    resources = [part for part in (message.parts_json or [])
                 if isinstance(part, dict) and part.get("type") in {"file", "artifact", "citation"}]
    return (message.content or "") + ("\n\n历史资源引用：" + json.dumps(resources, ensure_ascii=False)
                                      if resources else "")


def native_binding(db, run):
    """从同一用户和工作区的历史运行查找原生会话绑定。"""
    rows = db.scalars(select(Run).where(Run.conversation_id == run.conversation_id,
        Run.workspace_id == run.workspace_id, Run.started_by_user_id == run.started_by_user_id)
        .order_by(Run.created_at.desc())).all()
    return next(((row.context_snapshot or {}).get("openwebui") for row in rows
                 if (row.context_snapshot or {}).get("openwebui", {}).get("chat_id")), None)


def run_chat(db, run, workspace, user, *, content, file_ids=None, kb_ids=None,
             plugin_version_id=None, events=None):
    """运行兼容层：不做意图路由或模型循环，所有推理轮次由 Open WebUI 驱动。"""
    client = None
    message = None
    parts = []
    artifact_cache = {}
    tools_seen = {}
    model_threads = []
    sequence = 0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    last_text = ""
    snapshot = dict(run.context_snapshot or {})
    delivery = DeliveryTracker(snapshot.get("user_request") or content)
    task_guard = TaskCompletionGuard(snapshot.get("openwebui_tasks") if snapshot.get("resumed_from_run_id") else None)
    failure_ledger = ToolFailureLedger(snapshot.get("tool_failures") if snapshot.get("resumed_from_run_id") else None)
    trajectory = RunTrajectory(run.id, run.conversation_id, run.workspace_id, run.started_by_user_id)
    profile, workspace_id, run_id = run.model_profile or "reasoning", run.workspace_id, run.id
    control = current.get()
    control_token = None
    if control is None:
        control = TaskControl()
        control_token = current.set(control)
    # 对话正文只写入独立的人读日志；机器审计 JSONL 仍保持正文脱敏，便于追踪又不刷屏。
    log_event("chat.message.user", "用户发送消息", human_label="用户", human_content=content,
              run_id=run.id, conversation_id=run.conversation_id)
    run.status, run.started_at = "running", datetime.utcnow()
    db.commit()

    def trace(kind, payload):
        """复用Octop轨迹投影，按节点持久化可定位的运行摘要。"""
        trajectory.observe(kind, payload)
        run.context_snapshot = {**(run.context_snapshot or {}), "octop": trajectory.snapshot()}
        db.commit()
        log_event("octop.node", "记录任务执行节点", run_id=run.id, node=kind,
                  tool_name=payload.get("tool"), status=payload.get("status"),
                  error_code=payload.get("error_code"))

    def event(kind, payload):
        if kind in {"run.started", "run.completed", "run.failed", "tool.started", "tool.completed"}:
            trace(kind, payload)
        return {"type": kind, "run_id": run.id, "payload": payload}

    yield event("run.started", {"run_id": run.id, "status": "running", "engine": "openwebui"})
    try:
        checkpoint()
        client = OpenWebUIClient(run.workspace_id, run.started_by_user_id)
        client.connect()
        log_event("openwebui.connected", "已连接完整 Open WebUI 后端", run_id=run.id)
        if not snapshot.get("user_message_id"):
            user_message = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                          role="user", content=content, parts=[text_part(content)])
            snapshot["user_message_id"] = user_message.id
            run.context_snapshot = {**(run.context_snapshot or {}), **snapshot}
            db.commit()
        binding = native_binding(db, run)
        if binding:
            chat_id = binding["chat_id"]
        else:
            old_messages = db.scalars(select(Message).where(Message.conversation_id == run.conversation_id,
                Message.workspace_id == run.workspace_id).order_by(Message.sequence)).all()
            history = [{"id": row.id, "role": row.role, "content": legacy_content(row)}
                       for row in old_messages if row.id != snapshot.get("user_message_id") and row.status == "completed"]
            conv = db.get(Conversation, run.conversation_id)
            chat_id = client.create_chat(conv.title or "业务对话", history)
        native = client.request("GET", f"/api/v1/chats/{chat_id}")
        task_guard.set_baseline(native.get("tasks") or [])
        parent_id = native["chat"].get("history", {}).get("currentId")
        message = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                 role="assistant", content="", parts=[])
        message.status = "running"
        run.context_snapshot = {**(run.context_snapshot or {}), **snapshot, "openwebui": {"chat_id": chat_id, "message_id": message.id,
                                                        "engine_version": "0.11.4"}}
        db.commit()
        from .resume import validated_resume_calls
        # 每次恢复都重新验证身份、资源和原始要求；只继承回执，不重新调用成功工具。
        for saved_call in validated_resume_calls(db, run):
            saved_result = saved_call.result_json or {}
            delivery.observe(db, run, saved_call.tool_name, saved_call.arguments_json or {},
                ToolResult(status="success", data=saved_result.get("data"),
                            artifacts=saved_result.get("artifacts") or [], metadata=saved_result.get("metadata") or {}),
                trusted_source_run_id=saved_call.run_id)
        gateway = ToolGateway(db)
        context = build_context(db=db, workspace_id=run.workspace_id, user_id=user.id, run_id=run.id,
            conversation_id=run.conversation_id, attached_file_ids=file_ids or [],
            selected_datasource_ids=snapshot.get("datasource_ids") or [],
            selected_dashboard_template_id=snapshot.get("dashboard_template_id"),
            selected_dashboard_template_mode=snapshot.get("dashboard_template_mode") or "auto")
        available = {spec.name: spec for spec in gateway.list_available(run.workspace_id, execution_context=context)}
        plugin = _load_plugin_context(db, run.workspace_id, plugin_version_id or run.plugin_version_id)
        skills = sync_skills(client, snapshot.get("business_module"), plugin)
        resources = {
            "附件": _attached_file_summaries(db, run.workspace_id, file_ids or []),
            "知识库": _kb_descriptions(db, run.workspace_id, kb_ids),
            "已选数据源": snapshot.get("datasource_ids") or [],
            "文档模板": snapshot.get("template_id"), "模板参数": snapshot.get("plugin_inputs") or {},
            "大屏模板": snapshot.get("dashboard_template_id"),
            "大屏模板模式": snapshot.get("dashboard_template_mode") or "auto",
            "对话模式": snapshot.get("mode") or "standard",
            "PPT续写进度": snapshot.get("presentation_resume"),
            "任务恢复": snapshot.get("task_resume"),
            "已核验交付": delivery.status(),
            "必须交付": delivery.required,
            "本轮实际请求": snapshot.get("user_request") or content,
        }
        client.submit(chat_id=chat_id, message_id=message.id,
            user_message={"id": snapshot["user_message_id"], "role": "user",
                          "content": content + ("\n\n附加资源标识：" + json.dumps({
                              "files": resources["附件"], "knowledge": resources["知识库"] if kb_ids else [],
                              "datasources": resources["已选数据源"]}, ensure_ascii=False)
                              if file_ids or kb_ids or resources["已选数据源"] else ""),
                          "parentId": parent_id, "timestamp": int(time.time())},
            model=run.model_profile or "reasoning",
            specs=[spec.to_openai_function()["function"] for spec in available.values()],
            system="当前业务资源（仅资源标识，不授予额外权限）：\n" + json.dumps(resources, ensure_ascii=False),
            skill_ids=skills)
        trace("context.loaded", {"source": "octop.skills", "label": "业务技能已同步到原生会话"})
        log_event("openwebui.submitted", "原生对话任务已提交", run_id=run.id, native_chat_id=chat_id)
        def finish_or_continue():
            """核对上游持久化终态和真实附件，缺项由原生后端继续编排。"""
            saved = client.request("GET", f"/api/v1/chats/{chat_id}")
            saved_message = saved["chat"]["history"]["messages"].get(client.message_id, {})
            if not saved_message or not saved_message.get("done"):
                raise BackendUnavailable("原生任务尚无持久化完成记录")
            if saved_message.get("error"):
                raise BackendUnavailable("Open WebUI 原生任务记录了执行错误，不能标记完成")
            completion = inspect_completion(saved_message.get("output") or [], done=saved_message.get("done"),
                                            error=saved_message.get("error"), fallback_text=saved_message.get("content") or "")
            failure_ledger.observe_completion(client.message_id, completion)
            if any(item.get("name") in {"create_tasks", "update_task"}
                   for item in completion.get("successful_tool_calls", [])):
                # 持久回执补偿丢失的清单事件，即使清单文本碰巧与上一轮完全相同也要核对。
                task_guard.active = True
            task_guard.reconcile(saved.get("tasks") or [])
            delivery_state = delivery.status()
            run.context_snapshot = {**(run.context_snapshot or {}),
                                    "completion_check": {key: value for key, value in completion.items() if key != "final_answer"},
                                    "openwebui_tasks": task_guard.status()["tasks"],
                                    "task_completion": task_guard.status(),
                                    "tool_failures": failure_ledger.state(),
                                    "tool_failure_check": failure_ledger.status(delivery_state),
                                    "delivery": delivery_state}
            db.commit()
            structural_errors = [item for item in completion["errors"]
                                 if item["code"] not in {"missing_final_answer", "progress_only_answer"}]
            if structural_errors or completion["missing_tool_results"] or completion["unfinished_ids"]:
                raise BackendUnavailable("原生工具调用尚未闭环，已保留运行轨迹")
            instruction = delivery.continuation()
            if instruction is not None:
                if task_guard.status()["missing"]:
                    instruction += "\n仍待完成的任务：" + json.dumps(task_guard.status(), ensure_ascii=False)
            else:
                # 附件核验通过后仍检查分析等非附件事项；有真实替代文件时允许原工具失败被补交覆盖。
                instruction = task_guard.continuation(
                    reply_errors=[item["code"] for item in completion["errors"]],
                    failed_tools=failure_ledger.effective(delivery_state),
                    blocker=delivery.blocker)
            if instruction is None:
                return False
            parent = client.message_id
            native_message_id = new_id()
            client.submit(chat_id=chat_id, message_id=native_message_id,
                user_message={"id": new_id(), "role": "user", "content": instruction,
                              "parentId": parent, "timestamp": int(time.time())},
                model=profile, specs=[spec.to_openai_function()["function"] for spec in available.values()],
                system="同一业务任务的交付核验补交，权限与资源范围保持不变：\n" + json.dumps(resources, ensure_ascii=False),
                skill_ids=skills)
            binding_state = dict((run.context_snapshot or {}).get("openwebui") or {})
            binding_state["native_message_id"] = native_message_id
            run.context_snapshot = {**(run.context_snapshot or {}), "openwebui": binding_state}
            db.commit()
            log_event("openwebui.delivery.continue", "原生任务继续补齐交付缺项", run_id=run.id,
                      missing=delivery.status()["missing"] + task_guard.status()["missing"], continuation=delivery.continuations)
            return True

        last_check = time.monotonic()
        disconnect_attempt = 0
        while True:
            checkpoint()
            try:
                kind, incoming, future = client.events.get(timeout=0.25)
            except queue.Empty:
                # 丢事件或上游进程退出时核对持久化状态，不以等待时长终止有效任务。
                if time.monotonic() - last_check > 10:
                    last_check = time.monotonic()
                    tasks = client.request("GET", f"/api/tasks/chat/{chat_id}").get("task_ids", [])
                    if not tasks:
                        saved = client.request("GET", f"/api/v1/chats/{chat_id}")
                        native_message = saved["chat"]["history"]["messages"].get(client.message_id, {})
                        if native_message.get("done") and not native_message.get("error"):
                            last_text = native_message.get("content") or last_text
                            if finish_or_continue():
                                last_text = ""
                                yield event("message.reset", {"iteration": 0})
                                yield event("task.progress", {"message": "正在继续完成：" + "、".join(
                                    delivery.status()["missing"] + task_guard.status()["missing"] or ["最终答复"])})
                                continue
                            break
                        raise BackendUnavailable("原生任务已经停止，尚未形成完整回复")
                continue
            # 补交任务切换后，丢弃已经排队的上一条原生消息事件。
            source_message_id = incoming.get("_native_message_id")
            if source_message_id and source_message_id != client.message_id:
                if future is not None and not future.done():
                    future.set_result({"error": "该原生消息已结束"})
                continue
            if kind == "trajectory":
                trace(incoming["event"], incoming)
                if incoming.get("event") == "model.completed":
                    disconnect_attempt = 0
                if incoming.get("event") == "model.retrying":
                    attempt = incoming.get("attempt") or 1
                    delay = incoming.get("backoff_seconds") or 1
                    yield event("task.progress", {"message":
                        f"模型连接暂时中断，正在继续第 {attempt} 次恢复（约 {delay} 秒后重试）",
                        "progress": None})
                continue
            if kind == "disconnect":
                # 原生连接断开不等于上游任务终止；保留同一 chat/message 绑定并持续重连。
                disconnect_attempt += 1
                delay = min(2 ** min(disconnect_attempt - 1, 4), 30)
                trace("openwebui.reconnecting", {"attempt": disconnect_attempt,
                    "backoff_seconds": delay, "reason": incoming.get("reason")})
                log_event("openwebui.reconnecting", "原生连接暂时中断，任务继续重连",
                          level=logging.WARNING, run_id=run.id, attempt=disconnect_attempt,
                          backoff_seconds=delay)
                yield event("task.progress", {"message":
                    f"原生连接暂时中断，正在继续第 {disconnect_attempt} 次重连（约 {delay} 秒后继续）",
                    "progress": None})
                _wait_for_model_recovery(control, delay)
                try:
                    client.reconnect()
                except Exception as exc:
                    log_event("openwebui.reconnect.failed", "本次重连未建立，继续等待下一次恢复",
                              level=logging.WARNING, run_id=run.id,
                              error_code=type(exc).__name__, attempt=disconnect_attempt)
                continue
            if kind == "model_error":
                if kind == "model_error" and incoming.get("request_id"):
                    trace("model.failed", {"request_id": incoming["request_id"], "error_code": incoming.get("code")})
                checkpoint()
                raise BackendUnavailable("模型请求未完成")
            if kind == "callback":
                disconnect_attempt = 0
                callback = incoming.get("data") or {}
                if incoming["type"] == "request:chat:completion":
                    # 模型线程继承取消和日志上下文；业务工具仍在本线程串行执行。
                    model_context = copy_context()
                    native_request_id = new_id()
                    trace("model.started", {"request_id": native_request_id})
                    def infer(data=callback, callback_future=future, request_id=native_request_id):
                        try:
                            from ...providers.model import stream_chat, chat_for_run
                            def generate(form, on_delta):
                                usage = {"run_id": run_id, "purpose": "openwebui.native"}
                                # 每次尝试先缓存增量，成功后再发送，避免连接中断重连时把半截答案重复显示。
                                attempt = 0
                                backoff = 1.0
                                while True:
                                    checkpoint()
                                    attempt += 1
                                    buffered_deltas = []
                                    try:
                                        if not form.get("stream"):
                                            result = chat_for_run(profile, form["messages"], form.get("tools"),
                                                workspace_id=workspace_id, usage_context=usage, max_retries=0)
                                        else:
                                            result = {}
                                            stream_chat(profile, form["messages"], form.get("tools"),
                                                temperature=form.get("temperature", 0.2),
                                                on_delta=buffered_deltas.append, on_done=result.update,
                                                workspace_id=workspace_id, usage_context=usage,
                                                request_timeout=None, max_retries=0)
                                        for delta in buffered_deltas:
                                            on_delta(delta)
                                        client.events.put(("trajectory", {"event": "model.completed",
                                            "request_id": request_id, "attempt": attempt,
                                            "usage": result.get("usage") or {}}, None))
                                        return result
                                    except ChatCancelled:
                                        raise
                                    except Exception as exc:
                                        if not _retryable_model_failure(exc):
                                            raise
                                        delay = min(backoff, 30.0)
                                        log_event("model.connection.recovery", "模型连接暂时失败，任务继续恢复",
                                                  level=logging.WARNING, run_id=run_id,
                                                  request_id=request_id, attempt=attempt,
                                                  error_code=getattr(exc, "code", type(exc).__name__),
                                                  backoff_seconds=delay)
                                        client.events.put(("trajectory", {"event": "model.retrying",
                                            "request_id": request_id, "attempt": attempt,
                                            "error_code": getattr(exc, "code", type(exc).__name__),
                                            "backoff_seconds": delay}, None))
                                        _wait_for_model_recovery(control, delay)
                                        backoff = min(backoff * 2.0, 30.0)
                            response = client.stream_model(data, generate, incremental=True)
                            if not data["form_data"].get("stream"):
                                callback_future.set_result(response)
                        except Exception as exc:
                            if not isinstance(exc, ChatCancelled):
                                logger.exception("Open WebUI 模型传输失败")
                            client.events.put(("model_error", {"code": type(exc).__name__, "request_id": request_id}, None))
                    thread = threading.Thread(target=lambda fn=infer, ctx=model_context: ctx.run(fn), daemon=True,
                                              name="openwebui-model-" + run.id)
                    model_threads.append(thread)
                    thread.start()
                    if callback["form_data"].get("stream"):
                        future.set_result({"status": True})
                    continue
                name, args = callback["name"], callback.get("params") or {}
                native_call_id = callback["id"]
                # 提供方可能在不同消息重复 call_0；数据库唯一键、页面节点和缓存都按运行与原生消息隔离。
                call_id = "ow-" + sha256(json.dumps([run.id, client.message_id, native_call_id],
                                                    ensure_ascii=False).encode("utf-8")).hexdigest()
                if call_id in tools_seen:
                    future.set_result(tools_seen[call_id])
                    continue
                sequence += 1
                step = start_step(db, workspace_id=run.workspace_id, run_id=run.id,
                    sequence=sequence, step_type="tool", name=name, input_data=args)
                call = record_tool_call(db, workspace_id=run.workspace_id, run_id=run.id, step_id=step.id,
                                        tool_name=name, tool_version_id=None, arguments=args)
                call.idempotency_key = call_id
                db.commit()
                yield event("tool.started", {"tool": name, "arguments": args, "tool_call_id": call_id})
                if name not in available:
                    result = ToolResult(status="failed", error_code="tool_not_available", error_summary="本次对话未开放该工具")
                else:
                    context["allow_network"] = available[name].provider in {"http", "mcp"}
                    # 使用数据库中同一 ToolCall 的稳定键，断线重试不能重新提交外部副作用。
                    context["idempotency_key"] = call.idempotency_key
                    try:
                        result = gateway.execute(run.workspace_id, name, args, context)
                        persist_artifacts(db, run, name, result, artifact_cache)
                    except ChatCancelled:
                        raise
                    except AppError as exc:
                        # 参数校验错误必须给出可修正信息，不能抹成模型无法处理的通用失败。
                        result = ToolResult(status="failed", error_code=exc.code, error_summary=exc.message,
                                            metadata={key: value for key, value in exc.extra.items()
                                                      if key in {"presentation_progress", "requires_user_input", "clarification"}})
                    except Exception as exc:
                        logger.exception("Open WebUI 业务工具失败")
                        result = ToolResult(status="failed", error_code=getattr(exc, "code", "tool_error"),
                                            error_summary="工具未完成，请核对参数或查看运行日志")
                delivery.observe(db, run, name, args, result)
                failure_ledger.observe_tool(client.message_id, native_call_id, name, args, result)
                run.context_snapshot = {**(run.context_snapshot or {}), "delivery": delivery.status(),
                                        "tool_failures": failure_ledger.state()}
                payload = result.to_dict()
                finish_tool_call(db, call, payload, error_code=result.error_code if result.status != "success" else None,
                                 error_summary=result.error_summary)
                if result.status == "success":
                    complete_step(db, step, payload)
                else:
                    fail_step(db, step, result.error_code or "tool_failed", result.error_summary or "工具未完成")
                parts.extend([tool_call_part(call_id, name, args), tool_result_part(call_id, name, payload)])
                parts.extend(artifact_part(item) for item in result.artifacts)
                if name == "search_knowledge" and isinstance(result.data, dict):
                    citations = result.data.get("results") or []
                    record_citations(db, workspace_id=run.workspace_id, run_id=run.id,
                                     message_id=message.id, citations=citations)
                    parts.extend(citation_part({key: item.get(key) for key in ("chunk_id", "file_id", "file_name")})
                                 for item in citations)
                message.parts_json = list(parts)
                db.commit()
                tools_seen[call_id] = payload
                future.set_result(payload)
                yield event("tool.completed", {"tool": name, "tool_call_id": call_id, "status": result.status,
                    "artifact_ids": [item["id"] for item in result.artifacts], "error_code": result.error_code,
                    "summary": result.error_summary or "已完成"})
                continue
            event_type = incoming.get("type")
            data = incoming.get("data") or {}
            if event_type == "chat:message:tasks":
                tasks = data.get("tasks") or []
                task_guard.observe(tasks)
                tasks = task_guard.status()["tasks"]
                run.context_snapshot = {**(run.context_snapshot or {}), "openwebui_tasks": tasks,
                                        "task_completion": task_guard.status()}
                db.commit()
                completed = sum(item.get("status") == "completed" for item in tasks)
                active = next((item.get("content") for item in tasks if item.get("status") == "in_progress"), None)
                yield event("task.progress", {"message": active or f"已完成 {completed}/{len(tasks)} 项任务",
                                             "progress": round(100 * completed / len(tasks)) if tasks else 0})
            if event_type == "response:completion" and data.get("type") == "response.output_item.done":
                item = data.get("item") or {}
                if item.get("type") == "function_call":
                    log_event("openwebui.tool.event", "收到原生工具节点事件", run_id=run.id,
                              tool_name=item.get("name"), status=item.get("status"))
            if event_type == "response:completion" and data.get("type") == "response.output_text.delta":
                delta = data.get("delta") or ""
                if delta:
                    last_text += delta
                    yield event("message.delta", {"delta": delta, "iteration": 0, "run_id": run.id})
                continue
            if event_type == "chat:completion":
                if data.get("usage") and not data.get("done"):
                    for key in usage_total:
                        usage_total[key] += int(data["usage"].get(key) or 0)
                text = data.get("content")
                if data.get("done") and isinstance(data.get("output"), list):
                    replies = [item for item in data["output"]
                               if item.get("type") == "message" and item.get("role", "assistant") == "assistant"]
                    # 只显示最后的回答，不把每次工具调用前的计划文字拼成最终结论。
                    text = "\n\n".join(block.get("text", "") for block in (replies[-1].get("content", []) if replies else [])
                                       if block.get("type") == "output_text")
                if isinstance(text, str) and text != last_text:
                    if text.startswith(last_text):
                        yield event("message.delta", {"delta": text[len(last_text):], "iteration": 0, "run_id": run.id})
                    else:
                        yield event("message.reset", {"iteration": 0})
                        yield event("message.delta", {"delta": text, "iteration": 0, "run_id": run.id})
                    last_text = text
                    message.content = text
                    message.parts_json = parts + [text_part(text)]
                    db.commit()
                if data.get("error"):
                    raise BackendUnavailable("原生对话执行失败")
                if data.get("done"):
                    if finish_or_continue():
                        last_text = ""
                        yield event("message.reset", {"iteration": 0})
                        yield event("task.progress", {"message": "正在继续完成：" + "、".join(
                            delivery.status()["missing"] + task_guard.status()["missing"] or ["最终答复"])})
                        continue
                    break
            elif event_type == "chat:tasks:cancel":
                raise ChatCancelled("原生对话任务已停止")
            elif event_type in {"chat:error", "chat:message:error", "error"}:
                raise BackendUnavailable("原生对话执行失败")
        checkpoint()
        if not last_text.strip():
            raise BackendUnavailable("原生任务没有返回可展示的回答")
        message.content, message.parts_json, message.status = last_text, parts + [text_part(last_text)], "completed"
        # 交付落地校验（业务增强）：正文数字须能溯源到本次工具回执/工件。
        from app.modules.runtime.grounding_check import run_delivery_audit
        audit = run_delivery_audit(db, run, last_text)
        yield event("delivery.audit", {"status": audit.get("status"), "warnings": audit.get("warnings") or [],
                                       "checked": audit.get("checked", 0), "matched": audit.get("matched", 0)})
        finalize_run(db, run, status="completed", model_usage=usage_total if any(usage_total.values()) else None)
        touch_conversation(db, run.conversation_id)
        db.commit()
        log_event("chat.message.assistant", "AI 回复", human_label="AI", human_content=last_text,
                  run_id=run.id, conversation_id=run.conversation_id)
        log_event("openwebui.completed", "原生对话已完成并同步界面记录", run_id=run.id)
        yield event("run.completed", {"status": "completed", "message_id": message.id, "engine": "openwebui"})
    except Exception as exc:
        cancelled = isinstance(exc, ChatCancelled)
        if cancelled:
            log_event("openwebui.cancelled", "用户停止了原生对话", run_id=run.id)
        else:
            logger.exception("Open WebUI 对话未完成")
        db.rollback()
        run.context_snapshot = {**(run.context_snapshot or {}), "delivery": delivery.status(),
                                "openwebui_tasks": task_guard.status()["tasks"],
                                "task_completion": task_guard.status(), "tool_failures": failure_ledger.state()}
        summary = (str(exc) if isinstance(exc, DeliveryIncomplete) else
                   "已停止本次任务，完成的结果已保留。" if cancelled else
                   "本次对话未能完成，已完成的工具结果和附件已保留。可查看运行记录定位原因。")
        code = "chat_cancelled" if cancelled else "delivery_incomplete" if isinstance(exc, DeliveryIncomplete) else "openwebui_failed"
        if message is None:
            message = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                     role="assistant", content=summary, parts=[error_part(summary, code)])
        else:
            message.content = last_text or summary
            message.parts_json = parts + ([text_part(last_text)] if last_text else []) + [error_part(summary, code)]
        message.status = "cancelled" if cancelled else "failed"
        if last_text.strip():
            log_event("chat.message.assistant.partial", "AI 已输出部分内容", human_label="AI",
                      human_content=last_text, run_id=run.id, conversation_id=run.conversation_id)
        log_event("chat.message.error", "对话失败", level=logging.ERROR, human_label="失败",
                  human_content=summary, run_id=run.id, conversation_id=run.conversation_id,
                  error_code=code)
        for step in db.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.status == "running")):
            fail_step(db, step, code, summary)
        for call in db.scalars(select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.status == "running")):
            finish_tool_call(db, call, {"status": message.status}, error_code=code, error_summary=summary)
        # 收尾中断节点，保留调用身份和原因，避免历史轨迹仍显示“正在执行”。
        pending_nodes = trajectory.snapshot()
        for call_id in pending_nodes["open_tool_calls"]:
            trajectory.observe("tool.failed", {"tool_call_id": call_id, "status": message.status, "error_code": code})
        for request_id in pending_nodes["open_model_calls"]:
            trajectory.observe("model.failed", {"request_id": request_id, "status": message.status, "error_code": code})
        finalize_run(db, run, status=message.status, error_code=code, error_summary=summary)
        db.commit()
        yield event("run.failed", {"status": message.status, "error_code": code, "error": summary})
    finally:
        if control and any(thread.is_alive() for thread in model_threads):
            control.cancelled.set()
        if client:
            try:
                if run.status != "completed":
                    client.stop()
            except Exception:
                logger.exception("原生任务取消请求未完成")
            client.close()
        if control_token is not None:
            current.reset(control_token)


def archive_chat(db, workspace_id, user_id, conversation_id):
    """幂等归档原生副本，防止已隐藏会话仍出现在原生历史检索中。"""
    from types import SimpleNamespace
    binding = native_binding(db, SimpleNamespace(workspace_id=workspace_id,
        started_by_user_id=user_id, conversation_id=conversation_id))
    if not binding:
        return
    client = OpenWebUIClient(workspace_id, user_id)
    try:
        client.connect()
        chat = client.request("GET", f"/api/v1/chats/{binding['chat_id']}")
        if not chat.get("archived"):
            client.request("POST", f"/api/v1/chats/{binding['chat_id']}/archive")
    finally:
        client.close()
