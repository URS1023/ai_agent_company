"""运行真实 Harness 会话并投影业务回执，平台不实现第二套 Agent 循环。"""
from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
from hashlib import sha256
import json
import logging
import queue
import time

from sqlalchemy import or_, select

from app.config import settings
from app.core.parts import artifact_part, citation_part, error_part, text_part, tool_call_part, tool_result_part
from app.errors import AppError
from app.ids import new_id
from app.models import Artifact, File, Message, ModelCall, Run, RunStep, ToolCall
from app.modules.runtime.service import (create_message, start_step, complete_step, fail_step,
    record_tool_call, finish_tool_call, finalize_run, touch_conversation, record_citations)
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, TaskControl, current
from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway, build_context
from app.providers.model import _profile_config, _persist_usage
from app.modules.model.service import enforce_budget
from app.modules.chat_artifacts.artifacts import persist_artifacts
from .client import HarnessClient, HarnessUnavailable
from .context import model_configuration, system_prompt
from .projection import NativeProjection, native_usage

logger = logging.getLogger(__name__)
_SCOPE_KEYS = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")


def _restore_data_scope(db, previous):
    """恢复进入内部审计或业务工具前的 Session 数据范围。"""
    for key in _SCOPE_KEYS:
        if key in previous:
            db.info[key] = previous[key]
        else:
            db.info.pop(key, None)


@contextmanager
def business_data_scope(db):
    """业务调用期间保留资源授权过滤，退出后不让它污染内部运行回执查询。"""
    previous = {key: db.info[key] for key in _SCOPE_KEYS if key in db.info}
    try:
        yield
    finally:
        _restore_data_scope(db, previous)


class HarnessFailure(HarnessUnavailable):
    """只携带经过映射的产品文案和稳定错误码，不向界面透传供应商原文。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _native_failure(error: dict) -> HarnessFailure:
    """原生终态按已知错误码给出可操作提示，未知错误保持通用失败。"""
    messages = {
        "QUOTA": ("model_balance_insufficient", "当前模型额度不足，请充值或选择其他模型。"),
        "AUTH": ("model_authentication_failed", "模型认证失败，请核对该模型的凭据配置。"),
        "MISSING_CREDENTIAL": ("model_authentication_failed", "该模型缺少可用凭据，请补充模型配置。"),
        "INVALID_CREDENTIAL": ("model_authentication_failed", "该模型凭据无效，请更新模型配置。"),
        "RATE_LIMIT": ("model_rate_limited", "模型服务限流，有限重试后仍不可用，请稍后再试。"),
        "CONTEXT_WINDOW_EXCEEDED": ("model_context_exceeded", "模型上下文超出配置容量，压缩后仍无法继续。"),
        "COMPANY_AUTHORIZATION_DENIED": ("model_authorization_denied", "模型调用未获授权，请核对用户权限及模型预算。"),
        "max-tokens": ("model_response_incomplete", "模型达到本次输出上限，任务尚未完成。"),
    }
    code, message = messages.get(str(error.get("code") or ""), ("harness_failed", "原生对话运行未完成。"))
    return HarnessFailure(code, message + "已完成的工具结果和附件已保留。")

# 业务工具显式白名单；平台新增通用执行能力不会自动进入企业对话。
# 名称统一小写比较（含 HARNESS_EXTRA_TOOLS），防止大小写变体绕过黑名单。
def _tool_name(name: str) -> str:
    return str(name or "").strip().lower()


BUSINESS_TOOL_NAMES = frozenset(_tool_name(name) for name in {
    "current_datetime", "list_files", "read_file", "search_knowledge", "list_business_data_sources",
    "query_business_data", "view_device_alerts", "query_workflow_results", "export_inspection_report",
    "collect_device_monitoring_snapshot", "list_dashboard_templates", "get_business_dashboard",
    "update_business_dashboard", "create_business_dashboard", "create_dashboard_from_queries",
    "render_dashboard_image", "render_document", "render_spreadsheet", "export_query_spreadsheet",
    "render_chart_image", "create_presentation_plan", "write_presentation_slides", "finalize_presentation",
    "render_presentation", "fill_template", "save_json_artifact", "list_business_skills", "read_business_skill_resource",
})
FORBIDDEN_TOOL_NAMES = frozenset(_tool_name(name) for name in {
    "python_execute", "write_file", "copy_file", "rename_file", "delete_file", "add_tool"})


def approved_tools(specs) -> dict:
    """允许已审查业务能力；额外连接器需部署显式列名，通用脚本及文件修改始终拒绝。

    规则（评审 R12/R14 落点）：
    - 白名单与禁止名单均按小写名称比较；
    - 内建业务工具必须来自 ``builtin`` provider，防止同名连接器“影子替换”内建语义
      （如停用内建 read_file 后发布同名 http 连接器）；
    - sandbox/通用执行一律拒绝；``HARNESS_EXTRA_TOOLS`` 只能按名引入非内建连接器，
      它们是否获得网络权限由 ``runtime`` 按 provider 决定（默认关闭）。
    """
    extra = frozenset(_tool_name(name) for name in settings.HARNESS_EXTRA_TOOLS)
    allowed = BUSINESS_TOOL_NAMES | extra
    selected: dict[str, object] = {}
    for spec in specs:
        name = _tool_name(spec.name)
        if name not in allowed or name in FORBIDDEN_TOOL_NAMES:
            continue
        if spec.provider == "sandbox":
            continue
        if name in BUSINESS_TOOL_NAMES and spec.provider != "builtin":
            # 同名连接器不能顶替已审查的内建工具；显式 HARNESS_EXTRA_TOOLS 才能引入连接器
            # 且必须使用未被业务名单占用的名称。
            continue
        selected.setdefault(name, spec)
    return selected


def session_identity(workspace_id: str, user_id: str, conversation_id: str) -> str:
    """目录身份同时绑定用户、工作区和会话，外部标识不能形成文件路径。"""
    return "company-" + sha256(json.dumps([workspace_id, user_id, conversation_id]).encode()).hexdigest()


def native_binding(db, run) -> dict | None:
    """仅从相同用户和工作区的历史运行寻找原生日志绑定。"""
    rows = db.scalars(select(Run).where(Run.conversation_id == run.conversation_id,
        Run.workspace_id == run.workspace_id, Run.started_by_user_id == run.started_by_user_id,
        Run.id != run.id).order_by(Run.created_at.desc())).all()
    return next(((row.context_snapshot or {}).get("harness") for row in rows
                 if (row.context_snapshot or {}).get("harness", {}).get("initialized") is True), None)


# 模型服务类故障（额度/限流/认证/服务暂时不可用/响应不完整）不是数据库故障，
# 不能据此冻结整轮工具访问（DSH 式韧性：让执行器换方式继续，而不是中断任务）。
_MODEL_FAULT_MARKERS = (
    "模型服务", "模型连接", "模型调用失败", "模型额度", "模型限流", "模型认证",
    "model_unavailable", "model_rate_limited", "model_balance_insufficient",
    "model_authentication_failed", "model_authorization_denied",
    "model_context_exceeded", "model_response_incomplete",
)


def _database_failure(result: ToolResult) -> bool:
    """一旦工具暴露连接、超时或资源异常，冻结本轮工具访问，不自动换源。

    例外：模型服务类失败（见 ``_MODEL_FAULT_MARKERS``）只是取数链路的上游故障，
    不属于数据库故障，不触发冻结——执行器可改为更小查询或换取数方式继续。
    """
    if result.status == "success":
        return False
    text = " ".join(str(value or "") for value in (result.error_code, result.error_summary)).lower()
    if any(marker in text for marker in _MODEL_FAULT_MARKERS):
        return False
    return result.error_code in {"tool_error", "tool_internal_error"} or any(marker in text for marker in ("timeout", "timed out", "connection", "disconnect",
        "operationalerror", "resource_exhaust", "超时", "断连", "连接中断", "连接失败", "资源异常"))


def _migration_background(db, run, user_message_id: str, assistant_message_id: str) -> str:
    """旧引擎的可见文本作为带来源的背景迁移，不伪造上游工具回执。"""
    rows = db.scalars(select(Message).where(Message.workspace_id == run.workspace_id,
        Message.conversation_id == run.conversation_id, Message.id.not_in([user_message_id, assistant_message_id]))
        .order_by(Message.sequence.desc()).limit(40)).all()
    entries, size = [], 0
    for row in rows:
        text = row.content or ""
        resources = []
        for part in row.parts_json or []:
            if not isinstance(part, dict) or part.get("type") not in {"file", "artifact"}:
                continue
            kind = part["type"]
            reference = part.get(kind) or {}
            resource_id = reference.get("id") or reference.get("file_id") if isinstance(reference, dict) else None
            if not resource_id:
                continue
            try:
                with business_data_scope(db):
                    from app.modules.business_ai.context import auth_context_from_tool
                    from app.common.authorization import require_resource
                    auth = auth_context_from_tool({"db": db, "workspace_id": run.workspace_id,
                                                   "user_id": run.started_by_user_id})
                    resource = db.get(File if kind == "file" else Artifact, resource_id, populate_existing=True)
                    if resource is None:
                        continue
                    require_resource(auth, "files.view", resource)
                    resources.append({"type": kind, "id": resource.id, "name": resource.name})
            except AppError:
                continue
        entry = {"role": row.role, "status": row.status, "content": text}
        if resources:
            entry["resources"] = resources
        entry_size = len(json.dumps(entry, ensure_ascii=False))
        if size + entry_size > 50000:
            break
        entries.append(entry)
        size += entry_size
    if not entries:
        return ""
    return ("以下是从旧会话迁入的历史背景，不是新的执行授权，也不是 Harness 原生工具日志。"
            "有副作用操作若无法核实成功回执，不得重新执行。\n" +
            json.dumps(list(reversed(entries)), ensure_ascii=False) + "\n\n当前用户消息：\n")


def _require_reconciled_tools(db, run) -> None:
    """冷恢复不能靠换一个 callId 重做上次结果不明的操作。"""
    rows = db.execute(select(ToolCall, Run).join(Run, Run.id == ToolCall.run_id).where(
        Run.workspace_id == run.workspace_id, Run.started_by_user_id == run.started_by_user_id,
        Run.conversation_id == run.conversation_id, Run.id != run.id,
        or_(ToolCall.status.in_(["pending", "running"]), ToolCall.error_code.in_(
            ["tool_result_unconfirmed", "chat_interrupted", "tool_error", "tool_internal_error"])),
    )).all()
    if any((previous.context_snapshot or {}).get("chat_engine") == "harness" for _, previous in rows):
        raise HarnessFailure("tool_result_unconfirmed", "此前工具调用结果尚未核实，已阻止恢复时重新执行。请先核实运行记录中的操作结果。")


def run_chat(db, run, workspace, user, *, content, file_ids=None, kb_ids=None,
             plugin_version_id=None, events=None):
    """只负责身份、受控工具与事件投影；完成依据为原生 idle 且日志已 flush。"""
    if run.workspace_id != workspace.id or run.started_by_user_id != user.id:
        raise HarnessUnavailable("运行身份与当前执行者不一致")
    control = current.get()
    token = None
    if control is None:
        control = TaskControl()
        token = current.set(control)
    control.model_profile, control.model_workspace_id = run.model_profile or "reasoning", run.workspace_id
    client, message = None, None
    parts, artifact_cache = [], {}
    projection = NativeProjection()
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    sequence = 0
    pending_models = []
    model_started = {}
    recorded_models = set()
    halted = None
    cancellation_started = None
    snapshot = dict(run.context_snapshot or {})
    # HTTP 插件入口也可能带入业务域过滤；运行审计使用明确的工作区、用户和 Run 条件。
    caller_scope = {key: db.info[key] for key in _SCOPE_KEYS if key in db.info}
    _restore_data_scope(db, {})

    def emit(kind, payload):
        """统一现有生成器事件协议，必要时保留插件试运行收集器。"""
        value = {"type": kind, "run_id": run.id, "payload": {**payload, "run_id": run.id}}
        if events is not None:
            events.append(value)
        return value

    def save_body():
        """正文结算后落库，终态或刷新时可恢复已收到的内容及工件。"""
        message.content = projection.text
        message.parts_json = list(parts) + ([text_part(projection.text)] if projection.text else [])
        run.context_snapshot = {**(run.context_snapshot or {}), "harness": {
            **(run.context_snapshot or {}).get("harness", {}), "cursor": projection.cursor}}
        db.commit()

    def settle_model(model_call, status, *, usage=None, error_code=None, native_sequence=None):
        """每次获准请求按真实结算入账一次，失败和无用量的尝试也计入调用数。"""
        if model_call.id in recorded_models:
            return
        model_call.status, model_call.completed_at = status, datetime.utcnow()
        model_call.usage_json, model_call.error_code = usage, error_code
        if native_sequence is not None:
            model_call.metadata_json = {**model_call.metadata_json, "native_sequence": native_sequence}
        if model_call in pending_models:
            pending_models.remove(model_call)
        # 用量账本使用独立事务，先提交运行审计，避免持有同一 SQLite 的写锁。
        db.commit()
        recorded_models.add(model_call.id)
        if usage:
            for key in usage_total:
                usage_total[key] += usage[key]
        _persist_usage(workspace_id=run.workspace_id, profile_name=run.model_profile or "reasoning",
            cfg={**config, "provider": model_call.provider, "model": model_call.model}, capability="chat",
            usage=usage, started=model_started.get(model_call.id, time.perf_counter()), status=status,
            error_code=error_code, usage_context={"source": "harness", "source_id": run.id,
                "model_call_id": model_call.id, **model_call.metadata_json})

    try:
        control.check()
        run.status, run.started_at = "running", datetime.utcnow()
        if not snapshot.get("user_message_id"):
            row = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                 role="user", content=content, parts=[text_part(content)])
            snapshot["user_message_id"] = row.id
        message = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                 role="assistant", content="", parts=[])
        message.status = "running"
        binding = native_binding(db, run)
        identity = session_identity(run.workspace_id, user.id, run.conversation_id)
        if binding and binding.get("session_id") != identity:
            raise HarnessUnavailable("原生会话绑定与当前身份不一致")
        run.context_snapshot = {**snapshot, "chat_engine": "harness", "harness": {
            "session_id": identity, "message_id": message.id, "protocol_version": 1, "initialized": False}}
        db.commit()
        yield emit("run.started", {"status": "running", "engine": "harness"})
        _require_reconciled_tools(db, run)
        config = _profile_config(run.model_profile or "reasoning", run.workspace_id)
        model = model_configuration(config)
        gateway = ToolGateway(db)
        context = build_context(db=db, workspace_id=run.workspace_id, user_id=user.id, run_id=run.id,
            conversation_id=run.conversation_id, attached_file_ids=file_ids or [],
            selected_datasource_ids=snapshot.get("datasource_ids") or [],
            selected_dashboard_template_id=snapshot.get("dashboard_template_id"),
            selected_dashboard_template_mode=snapshot.get("dashboard_template_mode") or "auto")
        with business_data_scope(db):
            available = approved_tools(gateway.list_available(run.workspace_id, execution_context=context))
            prompt = system_prompt(db, run, content, file_ids or [], kb_ids or [], plugin_version_id)
        background = "" if binding else _migration_background(db, run, snapshot["user_message_id"], message.id)
        client = HarnessClient(identity, control)
        yield emit("task.progress", {"message": "正在准备对话运行环境。"})
        client.start()
        yield emit("task.phase", {"phase": "environment.ready", "message": "对话运行环境就绪。"})
        initialization = client.request("initialize", {"sessionId": identity, "cwd": str(client.cwd), "model": model,
            "tools": [spec.to_openai_function()["function"] for spec in available.values()],
            "systemPrompt": prompt, "resumeExpected": bool(binding)})
        if not isinstance(initialization, dict) or initialization.get("protocolVersion") != 1:
            raise HarnessUnavailable("Harness 初始化协议版本不兼容")
        run.context_snapshot = {**run.context_snapshot, "harness": {
            **run.context_snapshot["harness"], "initialized": True}}
        db.commit()
        # 初始化快照只建立历史游标，当前请求从其后开始投影。
        yield emit("task.phase", {"phase": "executing", "message": "正在执行任务：取数、分析与交付。"})
        client.request("session.prompt", {"sessionId": identity, "requestId": run.id,
            "content": [{"type": "text", "text": background + content}], "mode": "queue"})
        while True:
            if control.cancelled.is_set() or (control.deadline is not None and time.monotonic() >= control.deadline):
                client.cancel()
                cancellation_started = cancellation_started or time.monotonic()
            if cancellation_started is not None and time.monotonic() - cancellation_started > settings.HARNESS_CANCEL_TIMEOUT_SECONDS:
                raise HarnessUnavailable("原生取消尚未完成，运行已中断，工具状态需要核实")
            try:
                packet = client.events.get(timeout=.2)
            except queue.Empty:
                continue
            method, data = packet.get("method"), packet.get("params") or {}
            if method in {"transport.closed", "transport.failed", "runtime.error"}:
                raise HarnessUnavailable("Harness 进程中断，未收到持久化终态")
            if method == "model.authorize":
                try:
                    control.check()
                    if data.get("sessionId") != identity:
                        raise HarnessUnavailable("模型调用身份不匹配")
                    if halted:
                        raise HarnessUnavailable("异常后不允许继续调用模型或工具")
                    from app.modules.business_ai.context import auth_context_from_tool
                    with business_data_scope(db):
                        auth_context_from_tool(context)
                    enforce_budget(run.workspace_id)
                    request_started = time.perf_counter()
                    # 同一逻辑轮内再授权（上游有限重试）标记 retry_of 上一条对话调用，
                    # 便于用量账本区分“重试”多计费（评审 R11）。
                    retry_of = next((prev.id for prev in pending_models
                                     if prev.metadata_json.get("purpose") == "conversation"), None)
                    model_call = ModelCall(id=new_id(), workspace_id=run.workspace_id, run_id=run.id,
                        provider=str(data.get("provider") or "harness"), model=str(data.get("model") or config["model"]),
                        status="started", metadata_json={
                            "engine": "harness", "purpose": data.get("purpose") or "conversation",
                            **({"retry_of": retry_of} if retry_of else {})},
                        started_at=datetime.utcnow())
                    db.add(model_call)
                    db.commit()
                    pending_models.append(model_call)
                    model_started[model_call.id] = request_started
                    client.respond(packet["id"], {"allowed": True})
                except Exception:
                    client.respond(packet["id"], {"allowed": False, "error_code": "model_budget_or_cancelled"})
                continue
            if method == "tool.invoke":
                name, args = str(data.get("name") or ""), data.get("arguments") or {}
                # 白名单按键为小写（R12）；执行仍用 spec 原始名，日志保留模型调用名。
                spec = available.get(_tool_name(name))
                exec_name = spec.name if spec else name
                invocation_seq = data.get("invocationSeq")
                if type(invocation_seq) is not int or invocation_seq < 0:
                    raise HarnessUnavailable("工具调用缺少有效的原生事件序号")
                call_id = "dsh-" + sha256(json.dumps([identity, invocation_seq]).encode()).hexdigest()
                # 复用成功回执前仍重新鉴权，不能用旧 callId 绕过撤权或停用。
                may_replay = data.get("sessionId") == identity and spec is not None and not halted
                if may_replay:
                    try:
                        with business_data_scope(db):
                            may_replay = gateway.resolve(run.workspace_id, exec_name, execution_context=context) is not None
                    except AppError:
                        may_replay = False
                previous = db.scalar(select(ToolCall).join(Run, Run.id == ToolCall.run_id).where(
                    ToolCall.workspace_id == run.workspace_id, Run.workspace_id == run.workspace_id,
                    Run.started_by_user_id == user.id, Run.conversation_id == run.conversation_id,
                    ToolCall.idempotency_key == call_id)) if may_replay else None
                if previous:
                    if previous.tool_name != name or previous.arguments_json != args:
                        client.respond(packet["id"], ToolResult(status="failed", error_code="tool_call_conflict",
                            error_summary="相同工具调用标识携带了不同参数，禁止执行").to_dict())
                        halted = "工具调用身份冲突，需核实后再继续。"
                        client.cancel()
                        cancellation_started = time.monotonic()
                    elif previous.status == "completed":
                        # 旧回执可能包含已撤销资源授权的数据；只确认完成，不重发敏感内容或重做副作用。
                        client.respond(packet["id"], ToolResult(status="success", data={
                            "already_completed": True,
                            "message": "此原生调用已完成，不要重新执行。历史数据未重新发送；需核实结果时按当前权限查看原运行记录。",
                            "source_run_id": previous.run_id,
                        }, metadata={"replayed": True, "sensitive_payload_omitted": True}).to_dict())
                    else:
                        client.respond(packet["id"], ToolResult(status="failed", error_code="tool_result_unconfirmed",
                            error_summary="该调用已存在但结果未确认，必须人工核实，禁止自动重做").to_dict())
                        halted = "已有工具调用结果未确认，必须核实后再继续。"
                        client.cancel()
                        cancellation_started = time.monotonic()
                    continue
                sequence += 1
                step = start_step(db, workspace_id=run.workspace_id, run_id=run.id, sequence=sequence,
                                  step_type="tool", name=name, input_data=args)
                call = record_tool_call(db, workspace_id=run.workspace_id, run_id=run.id, step_id=step.id,
                                        tool_name=name, tool_version_id=None, arguments=args)
                call.idempotency_key = call_id if may_replay else call_id + "-denied-" + new_id()
                db.commit()
                yield emit("tool.started", {"tool": name, "arguments": args, "tool_call_id": call_id})
                try:
                    control.check()
                    if data.get("sessionId") != identity or spec is None:
                        result = ToolResult(status="failed", error_code="tool_not_available", error_summary="当前会话未授权该工具")
                    elif halted:
                        result = ToolResult(status="failed", error_code="database_access_halted", error_summary=halted)
                    else:
                        # 网络权限只对显式放行的连接器开放（R14）：内建业务工具无网络，
                        # HARNESS_EXTRA_TOOLS 引入的 http/mcp 连接器由管理员按名确认。
                        context["allow_network"] = exec_name not in BUSINESS_TOOL_NAMES and spec.provider in {"http", "mcp"}
                        # 传递已持久化的调用键；网络提供方据此避免重连时重复产生外部副作用。
                        context["idempotency_key"] = call.idempotency_key
                        # execute 每次重新解析、鉴权与检查方法停用，不复用初次发现的授权。
                        with business_data_scope(db):
                            result = gateway.execute(run.workspace_id, exec_name, args, context)
                            persist_artifacts(db, run, exec_name, result, artifact_cache)
                except (ChatCancelled, ChatTaskTimeout):
                    result = ToolResult(status="failed", error_code="chat_cancelled", error_summary="本次工具已停止")
                except AppError as exc:
                    result = ToolResult(status="failed", error_code=exc.code, error_summary=exc.message)
                except Exception:
                    logger.exception("Harness 业务工具失败")
                    result = ToolResult(status="failed", error_code="tool_error", error_summary="工具结果未确认，请核实后再继续")
                if result.status != "success":
                    if result.status not in {"failed", "cancelled"}:
                        result.status, result.error_code = "failed", "tool_result_unconfirmed"
                        result.error_summary = "工具仅受理或返回未知状态，尚不能确认完成；请先核实原调用结果。"
                        halted = result.error_summary
                    else:
                        result.error_code = result.error_code or ("chat_cancelled" if result.status == "cancelled" else "tool_failed")
                if _database_failure(result):
                    halted = "本轮发生连接、超时或资源异常，已停止后续工具访问；需核实残留操作后由用户明确继续。"
                    run.context_snapshot = {**(run.context_snapshot or {}), "harness_tools_halted": True}
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
                    record_citations(db, workspace_id=run.workspace_id, run_id=run.id, message_id=message.id, citations=citations)
                    parts.extend(citation_part({key: item.get(key) for key in ("chunk_id", "file_id", "file_name")}) for item in citations)
                save_body()
                client.respond(packet["id"], payload)
                if halted:
                    client.cancel()
                    cancellation_started = cancellation_started or time.monotonic()
                yield emit("tool.completed", {"tool": name, "tool_call_id": call_id, "status": result.status,
                    "artifact_ids": [item["id"] for item in result.artifacts], "error_code": result.error_code,
                    "summary": result.error_summary or "已完成"})
                if result.status == "success" and result.artifacts:
                    yield emit("task.deliverable", {"artifact_ids": [item["id"] for item in result.artifacts],
                                                    "tool": exec_name, "count": len(result.artifacts)})
                continue
            if method == "session.frame":
                if data.get("sessionId") != identity:
                    raise HarnessUnavailable("Harness 会话事件身份不匹配")
                projected = projection.feed(data.get("frame") or {})
                native = projection.last_event
                if native and native.get("type") == "compaction/end" and (native.get("data") or {}).get("error"):
                    for model_call in list(pending_models):
                        if model_call.metadata_json.get("purpose") == "compaction":
                            settle_model(model_call, "failed", error_code="harness_compaction_failed", native_sequence=native["seq"])
                if native and native.get("type") in {"assistant/message", "assistant/attempt", "compaction/summary"}:
                    purpose = "compaction" if native["type"] == "compaction/summary" else "conversation"
                    model_call = next((row for row in pending_models if row.metadata_json.get("purpose") == purpose), None)
                    if not model_call:
                        continue
                    usage = native_usage(native)
                    if native["type"] == "assistant/attempt":
                        # 上游正常一轮的顺序是 assistant/attempt → assistant/message
                        # （见 core/agent-loop/tests/loop.spec.ts）；attempt 携带该次
                        # 请求的真实 usage（成功响应），是成功终态。中断的尝试才是失败终态；
                        # 没有任何用量回执的尝试按“未完成”记账失败，不伪造成功（R9）。
                        interrupted = bool((native.get("data") or {}).get("interrupted"))
                        if interrupted:
                            settle_model(model_call, "failed", usage=usage,
                                         error_code="chat_cancelled", native_sequence=native["seq"])
                        elif usage is not None:
                            settle_model(model_call, "completed", usage=usage, native_sequence=native["seq"])
                        else:
                            settle_model(model_call, "failed", usage=None,
                                         error_code="harness_model_attempt_failed", native_sequence=native["seq"])
                    elif native["type"] == "assistant/message":
                        # 消息事件若携带 interrupted（取消收尾），按失败结算；否则对仍待
                        # 结算的对话调用做成功结算兜底，避免成功轮剩余 pending（R9）。
                        interrupted = bool((native.get("data") or {}).get("interrupted"))
                        settle_model(model_call, "failed" if interrupted else "completed", usage=usage,
                                     error_code="chat_cancelled" if interrupted else None,
                                     native_sequence=native["seq"])
                    else:  # compaction/summary
                        settle_model(model_call, "completed", usage=usage, native_sequence=native["seq"])
                if any(kind == "message.replace" and body.get("committed") for kind, body in projected):
                    save_body()
                for kind, body in projected:
                    yield emit(kind, body)
                continue
            if method == "run.settled":
                if data.get("sessionId") != identity or data.get("requestId") != run.id:
                    continue
                if halted:
                    raise HarnessFailure("harness_tools_halted", halted)
                if data.get("status") == "cancelled":
                    if control.deadline is not None and time.monotonic() >= control.deadline and not control.cancelled.is_set():
                        raise HarnessFailure("chat_timeout", "本次任务已达到总时间上限，已完成的结果已保留。")
                    raise ChatCancelled("原生运行已停止并保存日志")
                if data.get("status") != "completed":
                    raise _native_failure(data.get("error") or {})
                if int(data.get("cursor", -1)) > projection.cursor:
                    raise HarnessUnavailable("原生终态早于事件归档，不能报告完成")
                if not projection.committed_text.strip():
                    raise HarnessUnavailable("原生运行没有已提交的可展示回答")
                break
        message.content, message.parts_json, message.status = projection.committed_text, parts + [text_part(projection.committed_text)], "completed"
        # 交付落地校验（业务增强）：正文数字须能溯源到本次工具回执/工件，异常发告警事件。
        from app.modules.runtime.grounding_check import run_delivery_audit
        audit = run_delivery_audit(db, run, projection.committed_text)
        yield emit("delivery.audit", {"status": audit.get("status"), "warnings": audit.get("warnings") or [],
                                      "checked": audit.get("checked", 0), "matched": audit.get("matched", 0)})
        for model_call in list(pending_models):
            # 原生可在压缩失败后继续回答；没有结算事实的尝试不能永远保留 started。
            settle_model(model_call, "failed", error_code="harness_model_result_unconfirmed")
        finalize_run(db, run, status="completed", model_usage=usage_total)
        touch_conversation(db, run.conversation_id)
        db.commit()
        yield emit("task.phase", {"phase": "finalized", "message": "任务完成，产物已交付。"})
        yield emit("run.completed", {"status": "completed", "message_id": message.id, "engine": "harness"})
    except Exception as exc:
        # 工具在同线程执行，此处没有仍在后台访问数据库的工具；进程失败不能伪装已取消。
        cancelled = isinstance(exc, ChatCancelled)
        status, code = ("cancelled", "chat_cancelled") if cancelled else ("failed", getattr(exc, "code", "harness_failed"))
        summary = (str(exc) if isinstance(exc, HarnessFailure) else
            "已停止本次任务，完成的结果已保留。" if cancelled else
            "本次处理未完成，已完成的工具结果和附件已保留；未确认的操作不会自动重做。")
        logger.warning("Harness 对话收尾：%s", type(exc).__name__)
        if client:
            client.cancel()
            client.close()
        db.rollback()
        if message is None:
            message = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                                     role="assistant", content=summary, parts=[])
        message.status, message.content = status, projection.text or summary
        message.parts_json = parts + ([text_part(projection.text)] if projection.text else []) + [error_part(summary, code)]
        for step in db.scalars(select(RunStep).where(RunStep.workspace_id == run.workspace_id, RunStep.run_id == run.id, RunStep.status == "running")):
            fail_step(db, step, code, summary)
        for call in db.scalars(select(ToolCall).where(ToolCall.workspace_id == run.workspace_id, ToolCall.run_id == run.id, ToolCall.status == "running")):
            finish_tool_call(db, call, {"status": "unknown", "requires_verification": True},
                             error_code="tool_result_unconfirmed", error_summary="工具结果未确认，禁止自动重做")
        for row in db.scalars(select(ModelCall).where(ModelCall.workspace_id == run.workspace_id, ModelCall.run_id == run.id, ModelCall.status == "started")).all():
            settle_model(row, "failed", error_code=code)
        finalize_run(db, run, status=status, error_code=code, error_summary=summary, model_usage=usage_total)
        db.commit()
        yield emit("run.failed", {"status": status, "error_code": code, "error": summary, "engine": "harness"})
    finally:
        try:
            if client:
                client.close()
        finally:
            _restore_data_scope(db, caller_scope)
            if token is not None:
                current.reset(token)
