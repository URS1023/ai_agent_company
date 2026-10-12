# -*- coding: utf-8 -*-
"""Chat HTTP 入口（前缀 `/api/chat`）：会话 CRUD、发送消息、SSE 事件流与运行轨迹。

在系统中的位置：
- 前端只与本路由和 `runtime/router.py`（/api/runs）交互；本文件承担"鉴权 + 参数校验 +
  事务边界"的职责，领域规则下沉到 `chat/service.py`，真正的执行在 `runtime/chat_jobs.py`
  的分离线程里，模型和工具循环由完整 Open WebUI 后端执行。
- 鉴权统一走 `common.deps.get_current_context`（AuthContext 提供 db / workspace_id / user_id），
  所有查询都按 workspace_id 过滤，跨工作区 id 一律表现为 404。

对外路由与 SSE 语义：
- `GET  /api/chat/business-modules`、`GET /api/chat/models`：选择器数据。
- `POST /api/chat/conversations`(201)、`GET /api/chat/conversations`、
  `GET|DELETE /api/chat/conversations/{id}`：会话列表/详情/归档（删除即置 archived_at）。
- `POST /api/chat/conversations/{id}/send`：先落库用户消息与 Run，再用文件租约抢占执行权，
  最后返回 `text/event-stream`——事件流由 `chat_jobs.replay()` 产出（缓冲 + 持久化重放），
  事件名见 `chat_jobs`/`agent.py`：run.started / run.created / message.delta / tool.started /
  tool.completed / message.question / message.followups / context.compacted / run.failed /
  run.completed / done；每帧带 `id:`，客户端可用 `?after=` 断点续传。
- `GET  /api/chat/runs/{id}/events`、`POST /api/chat/runs/{id}/cancel`、
  `GET  /api/chat/runs/{id}/trace`：断线重放、协作式取消、轨迹查询。

重要约束：
- 发送接口在持有执行租约的前提下提交"用户消息 + Run + chatLease"，不存在"已提交但无人执行"的窗口；
  任何异常都会回滚并把 Run 标记为中断。
- 取消是协作式的：`cancel_job` 只置取消标志，返回 `stopping`，真正的终态由执行线程写回。
- `/send` 与 `/events` 都只允许发起者本人（`_owned_run`）；`/trace` 额外允许管理员。
"""
from __future__ import annotations

from app.core.observability import log_event, observe

import json
import re
from hashlib import sha256
from typing import List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ...core.parts import file_part, text_part
from ...models import Message, ModelCall, Run, RunStep, ToolCall
from . import service as chat_service
from .business_modules import BusinessModuleId, PRESET_VERSION, public_modules
from ..openwebui.resume import (
    is_continue_request, latest_chat_run, original_request, resume_snapshot,
    same_resource_scope, unfinished_task_for_continue,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ConversationCreate(BaseModel):
    """创建会话的请求体；business_module 只在创建时绑定，之后不可改。"""

    title: str = Field(default="新对话", max_length=200)
    business_module: Optional[BusinessModuleId] = None


class SendRequest(BaseModel):
    """发送消息的请求体，同时承载"本次资源选择"与运行参数。

    这些字段会被整体写入 Run 的 `context_snapshot`（见 `send`），供执行期与
    历史重放读取：file_ids / knowledge_base_ids / datasource_ids（本次明确选择，
    不允许执行期静默换源）、template_id 与 dashboard_template_*（PPT 与数字大屏模板）、
    plugin_version_id / plugin_inputs（插件与它的输入）、mode（web/deep）。
    载荷校验：内容为空但带附件时自动补"请分析我上传的文件。"，避免空消息进入执行队列。
    """

    # 单条消息允许较长业务说明，但拒绝把无限正文直接写入运行上下文。
    content: str = Field(max_length=120_000)
    request_id: Optional[str] = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    # profile 名以字母开头，与 model_profiles.profile_name 的命名约定保持一致。
    model_profile: Optional[str] = Field(default=None, min_length=2, max_length=100, pattern=r'^[A-Za-z][A-Za-z0-9_-]+$')
    file_ids: List[str] = Field(default_factory=list, max_length=100)
    knowledge_base_ids: List[str] = Field(default_factory=list, max_length=100)
    # 上限 8 与前端选择器一致；去重与存在性校验在 send 中完成。
    datasource_ids: List[str] = Field(default_factory=list, max_length=8)
    plugin_version_id: Optional[str] = Field(default=None, max_length=128)
    template_id: Optional[str] = Field(default=None, max_length=128)
    dashboard_template_mode: Literal["auto", "manual", "none"] = "auto"
    dashboard_template_id: Optional[str] = Field(default=None, max_length=100)
    plugin_inputs: dict = Field(default_factory=dict, max_length=100)
    mode: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_message_or_attachment(self):
        """允许"只上传文件不写字"：此时补一句默认指令，保证 Run 始终有可读的用户诉求。"""
        if not self.content.strip():
            if not self.file_ids:
                raise ValueError("请输入消息内容或上传文件")
            self.content = "请分析我上传的文件。"
        return self


def _unfinished_presentation_for_continue(ctx, conversation_id: str, payload: SendRequest):
    """仅为纯继续指令查找当前身份、相同资源下最近一轮尚未交付的 PPT 草稿。"""
    if not is_continue_request(payload.content):
        return None
    from ...models import Artifact
    from ..presentation.draft import draft_exists

    previous = latest_chat_run(ctx.db, ctx.workspace_id, ctx.user_id, conversation_id)
    if previous is None or not same_resource_scope(previous, payload):
        return None
    snapshot = previous.context_snapshot or {}
    request = original_request(snapshot)
    if not re.search(r"ppt|幻灯片|演示", request, re.I):
        return None
    if not draft_exists(ctx.workspace_id, previous.id):
        return None
    delivered = ctx.db.scalar(select(Artifact.id).where(
        Artifact.workspace_id == ctx.workspace_id,
        Artifact.owner_user_id == ctx.user_id,
        Artifact.run_id == previous.id,
        Artifact.type == "presentation",
        Artifact.status == "ready",
    ).limit(1))
    return None if delivered else previous


class RunOut(BaseModel):
    """占位响应模型（保留给 OpenAPI 文档/兼容既有引用），当前路由未使用。"""

    pass


def _sse(event: str, data: dict) -> str:
    """把一条事件序列化为 SSE 帧；`ensure_ascii=False` 保证中文可读、便于排障。

    注意：这是本文件自用的简易编码器；`/send`、`/events` 的事件流实际由
    `runtime/chat_jobs.replay()` 产出，两者须保持相同的事件名与 JSON 结构。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/business-modules")
def business_modules(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/business-modules —— 返回可选业务模块（不含服务端指令）。需登录。"""
    return {"items": public_modules()}


@router.get('/models')
def available_chat_models(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/models —— 当前工作区可用的对话模型档位，默认档为 reasoning。需登录。"""
    from .model_selection import chat_models
    return {'items': chat_models(ctx.db, ctx.workspace_id), 'default_profile': 'reasoning'}


@router.post("/conversations", status_code=201)
def create_conversation(payload: ConversationCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/chat/conversations —— 创建会话，可一次性绑定业务模块。需登录。

    201 返回 {id, title, business_module}；未知 business_module 由 service 抛 ValidationError。
    """
    conv = chat_service.create_conversation(ctx.db, workspace_id=ctx.workspace_id,
                                            owner_user_id=ctx.user_id, title=payload.title,
                                            business_module=payload.business_module)
    ctx.db.commit()
    return {"id": conv.id, "title": conv.title, "business_module": payload.business_module}


@router.get("/conversations")
def list_conversations(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/conversations —— 当前用户的会话列表（更新倒序，不含归档）。需登录。

    标题/摘要用 `conversation_display_titles` 的读取期投影，历史占位标题在这里被还原；
    `business_module` 缺失（None）表示普通对话。
    """
    rows = chat_service.list_conversations(ctx.db, ctx.workspace_id, ctx.user_id)
    titles = chat_service.conversation_display_titles(ctx.db, rows)
    modules = chat_service.conversation_modules(ctx.db, [row.id for row in rows])
    return {"items": [{"id": c.id, "title": titles[c.id], "summary": titles[c.id],
                       "business_module": modules.get(c.id),
                       "updated_at": c.updated_at.isoformat() if c.updated_at else None} for c in rows],
            "count": len(rows)}


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/conversations/{id} —— 会话详情（消息 + 最近一次 Run 的摘要）。需登录。

    仅会话所有者可见；非所有者按 NotFoundError 处理（不区分"不存在"与"无权"，避免枚举）。
    读取时会调用 `recover_abandoned_chat`：若最近一次 Run 的执行者已确认死亡，则就地标记中断，
    随后隐藏该 Run 起始时间之后产生的助手消息——否则刷新页面会看到残缺的半截回答。
    返回 `last_run.context_snapshot` 供前端恢复"上次用的模型/数据源/模板"等选择。
    """
    conv = chat_service.get_conversation(ctx.db, ctx.workspace_id, conversation_id)
    if conv.owner_user_id != ctx.user_id:
        from app.errors import NotFoundError
        raise NotFoundError("会话不存在")
    rows = _owned_chat_runs(ctx.workspace_id, ctx.user_id, conversation_id=conversation_id, latest=True)
    latest_run = rows[0] if rows else None
    messages = chat_service.list_messages(ctx.db, conversation_id)
    display_title = chat_service.conversation_display_titles(ctx.db, [conv])[conv.id]
    if latest_run and latest_run.status in {"created", "running"}:
        messages = [m for m in messages if not (m.role == "assistant" and m.created_at >= latest_run.created_at)]
    return {"id": conv.id, "title": display_title,
            "business_module": chat_service.conversation_modules(ctx.db, [conv.id]).get(conv.id),
            "messages": [chat_service.message_out(m) for m in messages],
            "last_run": ({
                "id": latest_run.id,
                "status": latest_run.status,
                "error_code": latest_run.error_code,
                "context_snapshot": latest_run.context_snapshot or {},
                "plugin_version_id": latest_run.plugin_version_id,
                "model_profile": latest_run.model_profile,
            } if latest_run else None)}


from datetime import datetime as _dtt


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/chat/conversations/{id} —— 归档会话（软删除）。需登录+会话所有者。

    只置 `archived_at`，消息与 Run 记录全部保留（审计与历史重放依赖它们）；
    非所有者抛 AuthError，与详情接口的 404 语义不同，因为删除是显式写操作。
    """
    conv = chat_service.get_conversation(ctx.db, ctx.workspace_id, conversation_id)
    if conv.owner_user_id != ctx.user_id:
        from ...errors import AuthError
        raise AuthError("只能删除自己的会话")
    from ..runtime.engine import archive_chat
    archive_chat(ctx.db, ctx.workspace_id, ctx.user_id, conversation_id)
    conv.archived_at = _dtt.utcnow()
    ctx.db.commit()
    return {"ok": True}


def _owned_chat_runs(workspace_id, user_id, *, conversation_id=None, run_id=None, active=False, latest=False):
    """聊天状态属于发起者自己的会话；内部短事务按身份读取，独立于运行管理菜单权限。"""
    from app.db import new_session
    from ..runtime.chat_liveness import recover_abandoned_chat
    with new_session() as internal:
        query = select(Run).where(Run.workspace_id == workspace_id, Run.started_by_user_id == user_id)
        if conversation_id is not None:
            query = query.where(Run.conversation_id == conversation_id)
        if run_id is not None:
            query = query.where(Run.id == run_id)
        if active:
            query = query.where(Run.status.in_(["created", "running"]))
        query = query.order_by(Run.created_at.desc(), Run.id.desc())
        if latest:
            query = query.limit(1)
        rows = internal.scalars(query).all()
        for run in rows:
            recover_abandoned_chat(internal, run)
        for run in rows:
            internal.expunge(run)
        return rows


def _active_conversation_run(db, workspace_id, conversation_id, user_id):
    """在会话所有者身份下检查存活任务，缺少运行菜单权限也不能重复启动同一会话。"""
    rows = _owned_chat_runs(workspace_id, user_id, conversation_id=conversation_id, active=True)
    for run in rows:
        snapshot = run.context_snapshot or {}
        owner = snapshot.get("chatLease") if isinstance(snapshot, dict) else None
        if not isinstance(owner, dict) or owner.get("runId") != run.id or not owner.get("host"):
            continue
        if run.status in {"created", "running"}:
            return run
    return None


def _submission_fingerprint(payload: SendRequest) -> str:
    """同一提交标识绑定原始正文和完整资源选择，不能悄悄变成另一项任务。

    排除 ``title``（会话改名属界面元数据，不影响任务身份），避免同 request_id
    因标题变化被误判为不同载荷。
    """
    value = json.dumps(payload.model_dump(mode="json", exclude={"request_id", "title"}),
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(value.encode("utf-8")).hexdigest()


def _replay_submission(ctx, conversation_id: str, payload: SendRequest):
    """从持久化请求身份恢复原 Run；明确按三重归属查询，不要求运行管理菜单权限。

    去重语义：同一 ``request_id`` + 相同载荷 → 重放原运行（无论是否已结束）；
    同一 ``request_id`` + 不同载荷 → 仅当原运行仍活跃（created/running）时拒绝（409），
    已终态的历史运行允许复用该标识发起新任务，避免自动重试客户端被永久卡死。
    """
    if not payload.request_id:
        return None
    from app.errors import ConflictError
    from app.db import new_session
    from ..runtime.chat_jobs import replay
    table = Run.__table__
    fingerprint = _submission_fingerprint(payload)
    # 读接单锁取得后的最新提交，避免请求鉴权先开启的可重复读快照遗漏刚完成的任务。
    with new_session() as internal:
        rows = internal.execute(select(table.c.id, table.c.status, table.c.context_snapshot).where(
            table.c.workspace_id == ctx.workspace_id, table.c.started_by_user_id == ctx.user_id,
            table.c.conversation_id == conversation_id,
        ).order_by(table.c.created_at.desc()).execution_options(yield_per=100))
        for run_id, run_status, snapshot in rows:
            if (snapshot or {}).get("client_request_id") != payload.request_id:
                continue
            if snapshot.get("client_request_fingerprint") == fingerprint:
                return StreamingResponse(replay(run_id, ctx.workspace_id), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            if str(run_status or "").lower() in {"created", "running"}:
                raise ConflictError("同一请求标识不能用于不同的消息或资源选择。", run_id=run_id)
            return None
    return None


@router.post("/conversations/{conversation_id}/send")
def send(conversation_id: str, payload: SendRequest, ctx: AuthContext = Depends(get_current_context)):
    """每个会话串行接单；同一提交无论是否已结束，都只重放原运行。"""
    conv = chat_service.get_conversation(ctx.db, ctx.workspace_id, conversation_id)
    if conv.owner_user_id != ctx.user_id:
        from app.errors import NotFoundError
        raise NotFoundError("会话不存在")
    from app.errors import ConflictError
    from ..runtime.chat_liveness import conversation_send_lease
    admission = conversation_send_lease(conversation_id)
    if not admission.acquire():
        previous = _replay_submission(ctx, conversation_id, payload)
        if previous is not None:
            return previous
        active = _active_conversation_run(ctx.db, ctx.workspace_id, conversation_id, ctx.user_id)
        raise ConflictError("本次发送正在处理，正在恢复任务进度。",
                            run_id=active.id if active else None)
    try:
        previous = _replay_submission(ctx, conversation_id, payload)
        if previous is not None:
            return previous
        active = _active_conversation_run(ctx.db, ctx.workspace_id, conversation_id, ctx.user_id)
        if active:
            raise ConflictError("该会话的任务仍在执行，正在恢复任务进度。", run_id=active.id)
        return _send_admitted(conversation_id, payload, ctx)
    finally:
        admission.release()


@observe("chat.admission", "接收对话消息", fields={"workspace_id": "ctx.workspace_id", "conversation_id": "conversation_id", "profile": "payload.model_profile"})
def _send_admitted(conversation_id: str, payload: SendRequest, ctx: AuthContext):
    """POST /api/chat/conversations/{id}/send —— 发送消息并返回 SSE 事件流。需登录+会话所有者。

    执行顺序（每一步都为后续步骤留下可恢复状态）：
    1. 校验会话归属、解析模型档位、去重并校验数据源/大屏模板等本次资源选择；
    2. 落库用户消息与 Run（同一事务），把本次选择写进 `context_snapshot`；
    3. 取得 Run 级文件租约（`chat_lease`）后才提交 `chatLease` 身份并启动后台执行线程——
       保证不会出现"已提交但没有执行者"的孤儿 Run；
    4. 返回 `text/event-stream`，由 `chat_jobs.replay()` 从持久化事件表 + 内存缓冲推流。

    重试语义：`resolved_user_request` 会把"重新生成/重试"这类控制指令还原为上一轮的实质性用户请求
    写入快照，使大屏/PPT 生成能沿用原始范围与模板；可见的用户消息仍是这次发出的重试文本。
    纯继续指令恢复最近未完成任务的原请求、进度与成功回执，资源改变时不继承。
    PPT 重试还会尝试从同会话历史 Run 恢复草稿（要求请求文本与所有业务资源一致）。

    异常：租约抢占失败抛 RuntimeError("无法建立任务执行租约…")；启动阶段任何异常都会回滚事务、
    把 Run 标记为中断并释放租约，因此不会残留"running"的僵尸记录。
    副作用：写 messages、runs、run_events；写审计事件 `chat.send`（resource_type=run）。
    """
    conv = chat_service.get_conversation(ctx.db, ctx.workspace_id, conversation_id)
    if conv.owner_user_id != ctx.user_id:
        from app.errors import NotFoundError
        raise NotFoundError("会话不存在")
    # 重试与继续是控制指令，快照保留实质需求，可见用户消息仍保留本次发送的原文。
    from .model_selection import selected_model
    from ..runtime.engine import selected_engine
    chat_engine = selected_engine()
    model_profile = selected_model(ctx.db, ctx.workspace_id, payload.model_profile)
    # Harness 直接恢复原生历史，不把用户原文经旧引擎的正则转换成补交指令。
    presentation_source = (_unfinished_presentation_for_continue(ctx, conversation_id, payload)
                           if chat_engine == "openwebui" else None)
    continuation_source = ((unfinished_task_for_continue(ctx, conversation_id, payload) or presentation_source)
                           if chat_engine == "openwebui" else None)
    resolved_user_request = payload.content
    if continuation_source is not None:
        previous_snapshot = continuation_source.context_snapshot or {}
        resolved_user_request = original_request(previous_snapshot)
    # 判定"这条消息只是控制指令"：整条消息仅为重新生成/重试等词，不含任何新的业务诉求。
    # 边界：只要夹带了其他内容（例如"重新生成，但要按季度汇总"）就不算重试，按新需求处理。
    is_retry = chat_engine == "openwebui" and bool(re.fullmatch(r"(?:请|帮我|再)?\s*(?:重新生成|重试|继续生成|继续完成)(?:一下|吧|一次)?[。！!\s]*", payload.content.strip()))
    if is_retry and continuation_source is None:
        previous_user = ctx.db.execute(
            select(Message.content).where(
                Message.workspace_id == ctx.workspace_id,
                Message.conversation_id == conversation_id,
                Message.role == "user",
            ).order_by(Message.sequence.desc()).limit(20)
        ).scalars().all()
        for previous in previous_user:
            acknowledgement = re.sub(r'[\s，,。.!！?？；;]+', '', str(previous)).casefold()
            # 纯应答（"好的""谢谢"）不构成实质性请求，跳过以免把寒暄当成原始需求。
            if re.fullmatch(r'(?:谢谢(?:你|您)?|感谢|好的?|明白了?|知道了|收到|了解了?|嗯|ok|thanks|thankyou)+', acknowledgement):
                continue
            # 询问"有哪些模板"属于选择过程，不是要做大屏/PPT 的原始诉求，同样跳过。
            if re.search(r"模板", str(previous)) and re.search(r"有哪些|可选|检查.*库|查看.*模板|列出|搜索", str(previous)):
                continue
            # 只回溯到最近一条非控制指令的用户消息，再往前的历史更可能已改变主题。
            if not is_continue_request(str(previous)) and not re.fullmatch(r"(?:请|帮我|再)?\s*(?:重新生成|重试|继续生成|继续完成)(?:一下|吧|一次)?[。！!\s]*", str(previous).strip()):
                resolved_user_request = str(previous)
                break
    datasource_ids = list(dict.fromkeys(payload.datasource_ids))
    business_module = chat_service.conversation_modules(ctx.db, [conv.id]).get(conv.id)
    if len(datasource_ids) != len(payload.datasource_ids):
        from ...errors import ValidationError
        raise ValidationError("本次选择的数据源不能重复")
    if datasource_ids:
        from ...modules.analytics.sources import get_source
        for datasource_id in datasource_ids:
            # The AI composer is a unified resource selector.  Provider-specific
            # execution is selected later by the governed business-data tool.
            get_source(ctx, datasource_id, active=True, provider="any")
    if payload.dashboard_template_mode == "manual" and not payload.dashboard_template_id:
        from ...errors import ValidationError
        raise ValidationError("手动模板模式必须明确选择一套大屏模板")
    # manual 与 dashboard_template_id 必须同时出现/同时缺席，避免"锁定了模板却没传 id"的歧义状态。
    if payload.dashboard_template_mode != "manual" and payload.dashboard_template_id:
        from ...errors import ValidationError
        raise ValidationError("只有手动模板模式可以提交 dashboard_template_id")
    if payload.dashboard_template_id:
        from ...modules.reports.templates import get_template
        get_template(ctx, payload.dashboard_template_id)
    if payload.title:
        chat_service.rename_conversation(ctx.db, conv, payload.title)
    elif chat_service.is_placeholder_title(conv.title):
        chat_service.rename_conversation(ctx.db, conv, chat_service.first_sentence_summary(payload.content))

    file_parts = [file_part({"id": fid, "name": "file", "mime_type": ""}) for fid in payload.file_ids]
    user_parts = [text_part(payload.content)] + file_parts
    user_msg = chat_create_msg(ctx.db, conv, "user", payload.content, user_parts)
    # User message, Run, and lease identity commit atomically below.

    from ...modules.runtime.service import create_run
    run = create_run(ctx.db, workspace_id=ctx.workspace_id, conversation_id=conversation_id,
                     user_id=ctx.user_id,
                     plugin_version_id=payload.plugin_version_id,
                     context_snapshot={"user_request": resolved_user_request,
                                       "client_request_id": payload.request_id,
                                       "client_request_fingerprint": _submission_fingerprint(payload) if payload.request_id else None,
                                       "chat_engine": chat_engine,
                                       "user_message_id": user_msg.id,
                                       "user_message_sequence": user_msg.sequence,
                                       "model_profile": model_profile,
                                       "file_ids": payload.file_ids,
                                       "knowledge_base_ids": payload.knowledge_base_ids,
                                       "datasource_ids": datasource_ids,
                                       "template_id": payload.template_id,
                                       "dashboard_template_mode": payload.dashboard_template_mode,
                                       "dashboard_template_id": payload.dashboard_template_id,
                                       "plugin_inputs": payload.plugin_inputs,
                                       "mode": payload.mode,
                                       "business_module": business_module,
                                       "business_preset_version": PRESET_VERSION if business_module else None},
                     model_profile=model_profile)
    run_id = run.id
    import socket
    from ..runtime.chat_liveness import chat_lease, mark_interrupted
    from ..runtime.chat_jobs import start_job, replay
    execution_lease = chat_lease(run_id)
    if not execution_lease.acquire():
        ctx.db.rollback()
        raise RuntimeError("无法建立任务执行租约，请稍后重试")
    # 租约在提交 chatLease 与启动执行线程之前持有，避免同一 Run 被两个执行者同时启动。
    try:
        # The executor identity and the new run become visible together while
        # the file lease is held; there is no committed ownerless start window.
        run.context_snapshot = {**run.context_snapshot,
            "chatLease": {"host": socket.gethostname(), "runId": run_id}}
        if continuation_source is not None:
            run.context_snapshot = {**run.context_snapshot, **resume_snapshot(ctx.db, continuation_source)}
            # 通用任务无需存在 PPT 草稿；已交付的 PPT 不因补交其他文件而重新制作。
            if presentation_source is not None and presentation_source.id == continuation_source.id:
                from ..presentation.draft import load_draft, resume_draft
                saved_draft = load_draft(ctx.workspace_id, presentation_source.id)
                progress = resume_draft(ctx.workspace_id, presentation_source.id, run_id,
                                        template_id=payload.template_id)
                run.context_snapshot = {**run.context_snapshot,
                    "template_id": payload.template_id or saved_draft.get("template_id"),
                    "presentation_resume": progress}
        elif is_retry and re.search(r"ppt|幻灯片|演示", resolved_user_request, re.I):
            from ..presentation.draft import draft_exists, resume_draft, load_draft
            candidates = ctx.db.scalars(select(Run).where(
                Run.workspace_id == ctx.workspace_id, Run.conversation_id == conversation_id,
                Run.started_by_user_id == ctx.user_id, Run.id != run_id,
            ).order_by(Run.created_at.desc()).limit(20)).all()
            for previous in candidates:
                if draft_exists(ctx.workspace_id, previous.id):
                    if (previous.context_snapshot or {}).get('user_request') != resolved_user_request:
                        continue
                    if not same_resource_scope(previous, payload):
                        break
                    saved_draft = load_draft(ctx.workspace_id, previous.id)
                    if payload.template_id and payload.template_id != saved_draft.get('template_id'):
                        break
                    status = resume_draft(ctx.workspace_id, previous.id, run_id, template_id=payload.template_id)
                    run.context_snapshot = {**run.context_snapshot, "resumed_from_run_id": previous.id,
                        "template_id": payload.template_id or saved_draft.get('template_id'),
                        "presentation_resume": status}
                    break
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="chat.send", resource_type="run", resource_id=run_id, run_id=run_id)
        ctx.db.commit()
        start_job(run_id, ctx.workspace_id, ctx.user_id, payload, execution_lease=execution_lease)
    except Exception:
        try:
            ctx.db.rollback()
            persisted = ctx.db.get(Run, run_id)
            if persisted:
                mark_interrupted(ctx.db, persisted)
        finally:
            execution_lease.release()
        raise
    return StreamingResponse(replay(run_id, ctx.workspace_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _owned_run(ctx, run_id):
    """取当前用户自己的 Run，否则统一抛 NotFoundError（跨工作区/他人 Run 不暴露存在性）。"""
    rows = _owned_chat_runs(ctx.workspace_id, ctx.user_id, run_id=run_id)
    if not rows:
        from app.errors import NotFoundError
        raise NotFoundError("任务不存在")
    return rows[0]


@router.get("/runs/{run_id}/events")
def replay_run(run_id: str, after: int = 0, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/runs/{id}/events —— 断线续传的事件流。需登录+Run 发起者。

    `after` 是最后收到的事件 id（对应 SSE 的 `id:` 行），服务端只补推更大的 id；
    若执行者已消失会先做一次孤儿 Run 判定（`recover_abandoned_chat`），
    使客户端能立即拿到 run.failed 终态而不是永久挂起。
    """
    run = _owned_run(ctx, run_id)
    from ..runtime.chat_jobs import replay
    return StreamingResponse(replay(run_id, ctx.workspace_id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/chat/runs/{id}/cancel —— 请求停止正在执行的 Run。需登录+Run 发起者。

    取消是协作式的：只向执行线程的 TaskControl 置取消标志，立即返回 `status="stopping"`；
    真正的终态（cancelled/failed）由执行线程在下一个检查点写入，客户端应从事件流确认。
    当本进程没有该 Run 的执行者（如重启后），返回当前数据库状态并顺带做一次孤儿判定。
    """
    run = _owned_run(ctx, run_id)
    from ..runtime.chat_jobs import cancel_job
    requested = cancel_job(run_id)
    return {"run_id": run_id, "status": "stopping" if requested else run.status}


def chat_create_msg(db, conv, role, content, parts):
    """以会话为上下文写入一条消息（沿用会话的 workspace_id，避免调用方传错）。不提交事务。"""
    from ...modules.runtime.service import create_message
    return create_message(db, workspace_id=conv.workspace_id, conversation_id=conv.id,
                          role=role, content=content, parts=parts)


def reduce_payload(ev: dict) -> dict:
    """把事件 payload 与事件层 run_id 合并成扁平结构，便于前端统一读取 run_id。

    优先使用 payload 内的 run_id（执行期写入的权威值），事件层仅作兜底。
    """
    p = ev.get("payload") or {}
    return {**p, "run_id": ev.get("run_id") or p.get("run_id")}


@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/chat/runs/{id}/trace —— 运行轨迹（步骤 / 工具调用 / 模型调用）。需登录。

    权限：Run 发起者本人或工作区管理员，其余按 NotFoundError 处理。
    返回原始 input/output（含 SQL、模型错误码等内部细节），用于排障，因此比会话详情更严格。
    只读；结果按 sequence / started_at 排序。
    """
    from sqlalchemy import select as _sel
    run = ctx.db.get(Run, run_id)
    if not run or run.workspace_id != ctx.workspace_id:
        from ...errors import NotFoundError
        raise NotFoundError("Run 不存在")
    if run.started_by_user_id != ctx.user_id and ctx.role != "admin":
        from ...errors import NotFoundError
        raise NotFoundError("Run 不存在")
    steps = ctx.db.execute(_sel(RunStep).where(RunStep.run_id == run_id)
                           .order_by(RunStep.sequence)).scalars().all()
    tool_calls = ctx.db.execute(_sel(ToolCall).where(ToolCall.run_id == run_id)
                                .order_by(ToolCall.started_at)).scalars().all()
    model_calls = ctx.db.execute(_sel(ModelCall).where(ModelCall.run_id == run_id)
                                 .order_by(ModelCall.started_at)).scalars().all()
    return {
        "run": {"id": run.id, "status": run.status, "model_profile": run.model_profile,
                "model_usage": run.model_usage, "cost": run.cost,
                "error_summary": run.error_summary,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None},
        "octop": (run.context_snapshot or {}).get("octop"),
        "completion_check": (run.context_snapshot or {}).get("completion_check"),
        "delivery": (run.context_snapshot or {}).get("delivery"),
        "task_completion": (run.context_snapshot or {}).get("task_completion"),
        "tool_failure_check": (run.context_snapshot or {}).get("tool_failure_check"),
        "resumed_from_run_id": (run.context_snapshot or {}).get("resumed_from_run_id"),
        "steps": [{"id": s.id, "sequence": s.sequence, "step_type": s.step_type, "name": s.name,
                   "status": s.status, "input": s.input_json, "output": s.output_json,
                   "error_summary": s.error_summary,
                   "started_at": s.started_at.isoformat() if s.started_at else None,
                   "completed_at": s.completed_at.isoformat() if s.completed_at else None}
                  for s in steps],
        "tool_calls": [{"id": t.id, "tool_name": t.tool_name, "status": t.status,
                        "arguments": t.arguments_json, "result": t.result_json,
                        "error_summary": t.error_summary}
                       for t in tool_calls],
        "model_calls": [{"provider": m.provider, "model": m.model, "status": m.status,
                         "usage": m.usage_json, "error_code": m.error_code} for m in model_calls],
    }
