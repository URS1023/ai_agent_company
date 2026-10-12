"""DSH 会话的业务建单与视图投影；不启动任何 Python 对话执行循环。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError as ContractError, model_validator
from sqlalchemy import func, select

from app.config import settings
from app.common.ordered_rows import ordered_payload_rows
from app.core.parts import text_part, file_part, error_part, tool_call_part, tool_result_part
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import Message, Run, RunEvent
from app.modules.chat import service as chat_service
from app.modules.runtime.service import create_message, create_run, finalize_run, touch_conversation
from .context import authenticate, lock_for, release_control, session
from . import registry, tasks


# 历史接口返回的是展示窗口，不是数据库归档接口；限制窗口可以防止一条会话的正文和事件无限膨胀。
# 需要完整归档时仍通过数据库/导出接口读取，不能让浏览器详情接口一次加载全部大对象。
def _history_limit(name: str, default: int, maximum: int = 100_000) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


MAX_HISTORY_RUNS = _history_limit("DSH_HISTORY_MAX_RUNS", 1000, 10_000)
MAX_HISTORY_MESSAGES = _history_limit("DSH_HISTORY_MAX_MESSAGES", 2000, 20_000)
MAX_HISTORY_EVENTS = _history_limit("DSH_HISTORY_MAX_EVENTS", 10_000, 100_000)


def run_timing(run):
    """用平台已保存的 UTC 时间给出耗时，历史回放与实时事件使用同一口径。"""
    def stamp(value):
        return value.isoformat(timespec="milliseconds") + "Z" if value else None
    return {"started_at": stamp(run.started_at), "completed_at": stamp(run.completed_at),
            "duration_ms": max(0, round((run.completed_at - run.started_at).total_seconds() * 1000))
            if run.started_at and run.completed_at else None}


class SendInput(BaseModel):
    """保留企业资源选择，区分会话模式、消息投递模式与稳定任务。"""
    # 与 HTTP 层保持相同的正文上限，防止内部调用绕过请求模型后塞入无限上下文。
    content: str = Field(max_length=120_000)
    request_id: str | None = Field(None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    model_profile: str | None = Field(default=None, max_length=100)
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=100)
    datasource_ids: list[str] = Field(default_factory=list, max_length=8)
    plugin_version_id: str | None = Field(default=None, max_length=128)
    template_id: str | None = Field(default=None, max_length=128)
    dashboard_template_mode: Literal["auto", "manual", "none"] = "auto"
    dashboard_template_id: str | None = Field(default=None, max_length=128)
    plugin_inputs: dict = Field(default_factory=dict, max_length=100)
    mode: Literal["standard", "web", "deep"] | None = "standard"
    delivery_mode: Literal["queue", "steer"] = "queue"
    skill_ids: list[str] = Field(default_factory=list, max_length=100)
    reasoning_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    task_id: str | None = None
    required_deliveries: list[dict] | None = Field(default=None, max_length=24)
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.content.strip():
            if not self.file_ids:
                raise ValueError("请输入消息内容或上传文件")
            self.content = "请分析我上传的文件。"
        return self


def session_identity(workspace_id, user_id, conversation_id):
    return "company-" + hashlib.sha256(json.dumps([workspace_id, user_id, conversation_id]).encode()).hexdigest()


def owned_conversation(ctx, conversation_id):
    conv = chat_service.get_conversation(ctx.db, ctx.workspace_id, conversation_id)
    if conv.owner_user_id != ctx.user_id:
        raise NotFoundError("会话不存在")
    return conv


def run_actor(db, run):
    """后台调用重查客户端与原令牌状态，当前授权只能收窄建单时的范围。"""
    from app.common.deps import AuthContext, _workspace_is_active, resolve_service_principal
    from app.common.data_scope import bind_data_scope
    from app.models import User, Workspace, WorkspaceMember
    from app.errors import AuthError
    user = db.get(User, run.started_by_user_id, populate_existing=True)
    workspace = db.get(Workspace, run.workspace_id, populate_existing=True)
    member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == run.workspace_id,
                                                    WorkspaceMember.user_id == run.started_by_user_id))
    if not user or user.status != "active" or not workspace or not member or not _workspace_is_active(db, workspace):
        raise AuthError("业务运行身份或工作区已不可用")
    actor = (run.context_snapshot or {}).get("actor") or {}
    client_id = actor.get("client_id")
    scopes = frozenset(actor["client_scopes"]) if actor.get("client_scopes") is not None else None
    if client_id:
        from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
        # 快照缺少范围时按空权限处理，绝不能让客户端回退成普通管理员身份。
        scopes = scopes or frozenset()
        if actor.get("client_token_id"):
            service = db.get(IntegrationAccessToken, actor["client_token_id"], populate_existing=True)
            principal = resolve_service_principal(db, service)
            if (principal.workspace_id != run.workspace_id or principal.user.id != run.started_by_user_id
                    or principal.client_id != client_id):
                raise AuthError("业务运行的服务令牌归属不一致")
            scopes = scopes.intersection(principal.client_scopes)
        else:
            # 旧快照无法反查唯一原令牌：仅重查客户端，不伪称已核验单令牌吊销或有效期。
            client = db.scalar(select(IntegrationClient).where(IntegrationClient.client_id == client_id,
                IntegrationClient.workspace_id == run.workspace_id, IntegrationClient.created_by == run.started_by_user_id,
                IntegrationClient.enabled.is_(True)).execution_options(populate_existing=True))
            if client is None:
                raise AuthError("业务运行的服务客户端已不可用")
            scopes = scopes.intersection(client.scopes or [])
    elif scopes is not None or actor.get("client_token_id"):
        raise AuthError("业务运行缺少可验证的服务客户端标识")
    ctx = AuthContext(db, user, workspace, member.role, "", scopes, client_id)
    if client_id:
        from app.common.authorization import require_permission
        # 新工具与模型轮次都先经过此处；收回对话执行权即停止继续消耗模型或发起业务动作。
        require_permission(ctx, "chat.execute")
    bind_data_scope(ctx)
    return ctx


def actor_snapshot(ctx):
    """保存已认证服务令牌的内部标识供长任务续查，不把明文令牌或摘要写入运行快照。"""
    actor = {"client_id": ctx.client_id, "client_scopes": sorted(ctx.client_scopes) if ctx.client_scopes is not None else None}
    if ctx.client_id:
        from app.common.deps import resolve_service_principal
        from app.modules.system_tools.models import IntegrationAccessToken
        token = ctx.db.scalar(select(IntegrationAccessToken).where(
            IntegrationAccessToken.token_digest == hashlib.sha256(ctx.token.encode("utf-8")).hexdigest()))
        principal = resolve_service_principal(ctx.db, token)
        if principal.workspace_id != ctx.workspace_id or principal.user.id != ctx.user_id or principal.client_id != ctx.client_id:
            from app.errors import AuthError
            raise AuthError("业务运行的服务令牌归属不一致")
        actor["client_token_id"] = token.id
        actor["client_scopes"] = sorted((ctx.client_scopes or frozenset()).intersection(principal.client_scopes))
    return actor


def get_run(db, identity):
    row = db.get(Run, identity)
    if not row or (row.context_snapshot or {}).get("chat_engine") != "dsh":
        raise NotFoundError("DSH 运行不存在")
    return row


def authorize(request, conversation_id=None, run_id=None):
    """宿主 SSE/取消/队列操作使用原始请求认证，再验证会话归属。"""
    with session() as db:
        raw = dict(request)
        if conversation_id:
            raw.update(method="GET", path=f"/api/chat/conversations/{conversation_id}")
        elif run_id:
            raw.update(method="GET", path=f"/api/chat/runs/{run_id}/events")
        ctx = authenticate(db, raw)
        if run_id:
            run = get_run(db, run_id)
            if run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
                raise NotFoundError("运行不存在")
            conversation_id = run.conversation_id
        owned_conversation(ctx, conversation_id)
        return {"workspace_id": ctx.workspace_id, "user_id": ctx.user_id, "conversation_id": conversation_id,
                "session_id": session_identity(ctx.workspace_id, ctx.user_id, conversation_id), "run_id": run_id,
                # 旧运行可能已超出会话展示窗口；保留其真实状态，供 SSE 回放正确结束。
                "run_status": run.status if run_id else None}


def validate_resources(ctx, payload):
    from app.common.authorization import require_resource
    from app.models import File, KnowledgeBase
    from app.modules.analytics.sources import get_source
    if len(set(payload.datasource_ids)) != len(payload.datasource_ids):
        raise ValidationError("本次选择的数据源不能重复")
    for identity in payload.datasource_ids:
        get_source(ctx, identity, active=True, provider="any")
    for model, ids, permission in ((File, payload.file_ids, "files.view"), (KnowledgeBase, payload.knowledge_base_ids, "knowledge.view")):
        for identity in ids:
            row = ctx.db.get(model, identity)
            if row is None or row.workspace_id != ctx.workspace_id:
                raise NotFoundError("选择的资料不存在")
            require_resource(ctx, permission, row)
    if (payload.dashboard_template_mode == "manual") != bool(payload.dashboard_template_id):
        raise ValidationError("手动模板模式必须明确选择模板，其它模式不能指定模板")
    if payload.dashboard_template_id:
        from app.modules.reports.templates import get_template
        get_template(ctx, payload.dashboard_template_id)
    if payload.mode not in {None, "standard"}:
        raise ValidationError("当前部署尚未启用该对话模式，请选择标准模式")
    registry.tool_names(payload.skill_ids)


def prepared(db, run, *, duplicate=False):
    """从持久运行重建工具与模型，冷恢复不依赖进程内缓存。"""
    from app.providers.model import _profile_config
    from app.providers.tool.gateway import ToolGateway, build_context
    from app.modules.harness.context import system_prompt
    from .models import model_configuration
    ctx = run_actor(db, run)
    snapshot = run.context_snapshot or {}
    model = model_configuration(_profile_config(run.model_profile or "reasoning", run.workspace_id))
    effort = snapshot.get("reasoning_effort")
    if effort:
        if model["protocol"] == "deepseek-messages" and effort not in {"off", "low", "high", "max"}:
            raise ValidationError("此模型不支持选择的推理等级")
        model["reasoningEffort"] = effort
        model["thinking"] = "disabled" if effort == "off" else "enabled"
        if model["protocol"] == "openai-completions":
            model["reasoningEfforts"] = False if effort == "off" else {"off": None, effort: effort}
            if effort == "off":
                model.pop("reasoningEffort", None)
    context = build_context(db=db, workspace_id=run.workspace_id, user_id=run.started_by_user_id,
        run_id=run.id, conversation_id=run.conversation_id, attached_file_ids=snapshot.get("file_ids") or [],
        selected_datasource_ids=snapshot.get("datasource_ids") or [],
        selected_dashboard_template_id=snapshot.get("dashboard_template_id"),
        selected_dashboard_template_mode=snapshot.get("dashboard_template_mode") or "auto")
    context["auth_context"] = ctx
    names = registry.tool_names(snapshot.get("skill_ids"), snapshot.get("business_module"))
    specs = [spec for spec in ToolGateway(db).list_available(run.workspace_id, execution_context=context)
             if spec.provider != "sandbox" and (spec.name in names or
                 (not snapshot.get("skill_ids") and spec.provider in {"http", "mcp", "database"}))]
    identity = session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id)
    home = Path(settings.STORAGE_ROOT).resolve() / "dsh" / "sessions" / identity
    content = snapshot.get("submitted_content") or snapshot.get("user_request") or ""
    content_blocks = [{"type": "text", "text": content}]
    from app.models import File
    from app.common.authorization import require_resource
    from app.common.storage import get_storage
    from app.modules.files.service import MAX_FILE_BYTES
    has_images = False
    for file_id in snapshot.get("file_ids") or []:
        attached = db.get(File, file_id)
        if not attached or attached.workspace_id != run.workspace_id or attached.deleted_at or attached.status != "ready":
            raise NotFoundError("本轮附件已不可用")
        require_resource(ctx, "files.view", attached)
        if attached.mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            continue
        has_images = True
        if "image" in model.get("inputModalities", []):
            if attached.size > MAX_FILE_BYTES:
                raise ValidationError("图片超过附件大小上限")
            image_data = get_storage().read_bytes(attached.storage_key)
            if len(image_data) > MAX_FILE_BYTES:
                raise ValidationError("图片超过附件大小上限")
            content_blocks.append({"type": "image", "mediaType": attached.mime_type,
                                   "data": base64.b64encode(image_data).decode("ascii"), "name": attached.name})
    prompt = system_prompt(db, run, content, snapshot.get("file_ids") or [], snapshot.get("knowledge_base_ids") or [], run.plugin_version_id)
    if has_images and "image" not in model.get("inputModalities", []):
        prompt += "\n本轮有图片附件，但当前主模型未声明直接识图能力。请调用 read_file 通过视觉业务服务读取图片，不能仅凭文件名宣称已看图。"
    skill_text = registry.skill_instructions(snapshot.get("skill_ids"))
    task = snapshot.get("business_task") or {}
    prompt += "\n当前业务任务与交付约束：" + json.dumps(task, ensure_ascii=False)
    prompt += "\n业务工具明确失败后可修正参数继续。结果未知的具体操作需要核实；不要把普通工具失败当作整个会话失效。"
    if skill_text:
        prompt += "\n\n" + skill_text
    history_seed = []
    old_runs = db.scalars(select(Run).where(Run.conversation_id == run.conversation_id,
        Run.workspace_id == run.workspace_id, Run.started_by_user_id == run.started_by_user_id)).all()
    native_messages = {identity for row in old_runs if (row.context_snapshot or {}).get("chat_engine") == "dsh"
                       for identity in [(row.context_snapshot or {}).get("user_message_id"), (row.context_snapshot or {}).get("assistant_message_id")] if identity}
    if old_runs:
        earlier = ordered_payload_rows(db, Message, (Message.conversation_id == run.conversation_id,
            Message.workspace_id == run.workspace_id, Message.id.not_in(native_messages)),
            (Message.sequence.desc(), Message.id.desc()), limit=40)
        entries, size = [], 0
        for message in earlier:
            entry = {"role": message.role, "content": message.content or "", "status": message.status}
            size += len(json.dumps(entry, ensure_ascii=False))
            if size > 50000:
                break
            entries.append(entry)
        history_seed = list(reversed(entries))
    user_message = db.get(Message, snapshot.get("user_message_id")) if snapshot.get("user_message_id") else None
    return {"run_id": run.id, "request_id": run.id, "client_request_id": snapshot.get("client_request_id"),
            "conversation_id": run.conversation_id, "session_id": identity,
            "workspace_id": run.workspace_id, "user_id": run.started_by_user_id,
            "message_id": snapshot["assistant_message_id"], "user_message_id": snapshot.get("user_message_id"), "status": run.status, "duplicate": duplicate,
            "user_message": {**chat_service.message_out(user_message), "run_id": run.id,
                             "request_id": snapshot.get("client_request_id")} if user_message else None,
            "model": model, "system_prompt": prompt, "tools": [spec.to_openai_function()["function"] for spec in specs],
            "content": content_blocks, "home": str(home), "cwd": str(home / "workspace"),
            "history_seed": {"source": "legacy-platform", "messages": history_seed},
            "task": task, "resources": {key: snapshot.get(key) for key in ("file_ids", "knowledge_base_ids", "datasource_ids", "template_id", "dashboard_template_id", "dashboard_template_mode")},
            "mode": snapshot.get("mode") or "standard", "delivery_mode": snapshot.get("delivery_mode") or "queue"}


def prepare_existing_plugin_run(db, run, content):
    """既有插件试运行复用原记录，仅把执行所有权转交 DSH。"""
    user = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                          role="user", content=content, parts=[text_part(content)])
    assistant = create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                               role="assistant", content="", parts=[])
    from app.ids import new_id
    identity = new_id()
    task = {"id": identity, "task_id": identity, "draft_id": identity, "title": content[:120], "status": "queued",
            "required_deliveries": tasks.requirements(content), "missing_deliveries": [],
            "created_at": datetime.utcnow().isoformat(), "updated_at": datetime.utcnow().isoformat()}
    task = tasks.update_contract(task, content)
    run.context_snapshot = {**(run.context_snapshot or {}), "chat_engine": "dsh", "execution_owner": "dsh-host",
        "submitted_content": content, "user_request": content, "user_message_id": user.id, "user_message_sequence": user.sequence,
        "assistant_message_id": assistant.id, "task_id": identity, "business_task": task,
        "mode": "standard", "delivery_mode": "queue"}
    run.status, assistant.status = "queued", "queued"
    run.model_profile = run.model_profile or "reasoning"
    result = prepared(db, run)
    db.commit()
    return result


def prepare(request):
    match = re.fullmatch(r"/api/chat/conversations/([^/]+)/send", request.get("path", ""))
    if not match:
        raise ValidationError("对话发送路径无效")
    conversation_id = match.group(1)
    try:
        payload = SendInput.model_validate(json.loads(base64.b64decode(request.get("body_base64") or "")))
    except (ContractError, ValueError, TypeError) as exc:
        raise ValidationError("对话请求内容或参数无效") from exc
    fingerprint = hashlib.sha256(payload.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
    with lock_for("conversation:" + conversation_id), session() as db:
        ctx = authenticate(db, request)
        conv = owned_conversation(ctx, conversation_id)
        if payload.request_id:
            candidates = db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.workspace_id == ctx.workspace_id,
                Run.started_by_user_id == ctx.user_id)).all()
            previous = next((row for row in candidates if (row.context_snapshot or {}).get("client_request_id") == payload.request_id), None)
            if previous:
                if previous.context_snapshot.get("client_request_fingerprint") != fingerprint:
                    raise ConflictError("相同请求标识不能提交不同内容")
                return prepared(db, previous, duplicate=True)
        validate_resources(ctx, payload)
        from app.modules.chat.model_selection import selected_model
        model_profile = selected_model(db, ctx.workspace_id, payload.model_profile)
        task = tasks.choose_task(db, ctx, conv.id, payload)
        previous_task_run = next((row for row in ordered_payload_rows(db, Run, (Run.conversation_id == conv.id,
            Run.workspace_id == ctx.workspace_id, Run.started_by_user_id == ctx.user_id), (Run.created_at.desc(), Run.id.desc()))
            if (row.context_snapshot or {}).get("task_id") == task["task_id"] or row.id == task.get("resume_from_run_id")), None)
        if previous_task_run:
            previous_resources = previous_task_run.context_snapshot or {}
            for key in ("file_ids", "knowledge_base_ids", "datasource_ids", "template_id", "dashboard_template_id"):
                if getattr(payload, key) in (None, [], "") and previous_resources.get(key):
                    setattr(payload, key, previous_resources[key])
            if payload.dashboard_template_id:
                payload.dashboard_template_mode = previous_resources.get("dashboard_template_mode") or "manual"
            validate_resources(ctx, payload)
        user_message = create_message(db, workspace_id=ctx.workspace_id, conversation_id=conv.id,
            role="user", content=payload.content, parts=[text_part(payload.content)] + [file_part({"id": identity, "name": "file", "mime_type": ""}) for identity in payload.file_ids])
        assistant = create_message(db, workspace_id=ctx.workspace_id, conversation_id=conv.id, role="assistant", content="", parts=[])
        assistant.status = "queued"
        snapshot = {**payload.model_dump(), "chat_engine": "dsh", "execution_owner": "dsh-host",
                    "submitted_content": payload.content, "user_request": payload.content,
                    "client_request_id": payload.request_id, "client_request_fingerprint": fingerprint,
                    "user_message_id": user_message.id, "user_message_sequence": user_message.sequence, "assistant_message_id": assistant.id,
                    "task_id": task["task_id"], "business_task": task,
                    "business_module": chat_service.conversation_modules(db, [conv.id]).get(conv.id),
                    "actor": actor_snapshot(ctx)}
        run = create_run(db, workspace_id=ctx.workspace_id, conversation_id=conv.id, user_id=ctx.user_id,
                         plugin_version_id=payload.plugin_version_id, context_snapshot=snapshot, model_profile=model_profile)
        run.status = "queued"
        if task.get("resume_from_run_id"):
            from app.modules.presentation.draft import resume_draft
            from .context import run_context
            with run_context(run):
                resume_draft(ctx.workspace_id, task["resume_from_run_id"], run.id, template_id=payload.template_id)
        if payload.title or chat_service.is_placeholder_title(conv.title):
            chat_service.rename_conversation(db, conv, payload.title or chat_service.first_sentence_summary(payload.content))
        response = prepared(db, run)
        db.commit()
        return response


def steer(request, conversation_id, run_id):
    """将补充原话保存到同一运行；执行仍由原生 DSH 下一步收取。"""
    try:
        raw = json.loads(base64.b64decode(request.get("body_base64") or ""))
        payload = SendInput.model_validate(raw)
    except (ContractError, ValueError, TypeError) as exc:
        raise ValidationError("补充请求内容或参数无效") from exc
    if not payload.request_id:
        raise ValidationError("运行中补充必须携带稳定请求标识")
    fingerprint = hashlib.sha256(payload.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
    with lock_for("conversation:" + conversation_id), lock_for("events:" + run_id), session() as db:
        ctx = authenticate(db, request)
        conv = owned_conversation(ctx, conversation_id)
        run = get_run(db, run_id)
        if run.conversation_id != conv.id or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
            raise NotFoundError("运行不存在")
        snapshot = dict(run.context_snapshot or {})
        previous = list(snapshot.get("steering_messages") or [])
        duplicate = next((item for item in previous if item["request_id"] == payload.request_id), None)
        if duplicate:
            if duplicate["fingerprint"] != fingerprint:
                raise ConflictError("相同请求标识不能提交不同补充内容")
            item = duplicate
        else:
            if run.status != "running" or snapshot.get("dsh_settled"):
                raise ConflictError("当前任务已结束，请作为新消息发送")
            if snapshot.get("client_request_id") == payload.request_id:
                raise ConflictError("补充请求不能复用原始消息标识")
            for key in ("file_ids", "knowledge_base_ids", "datasource_ids", "template_id", "dashboard_template_id",
                        "skill_ids", "plugin_version_id", "reasoning_effort", "task_id"):
                value = getattr(payload, key)
                if value not in (None, [], "") and value != snapshot.get(key):
                    raise ConflictError("运行中不能更换资源或执行配置，请将该消息加入队列")
            if payload.model_profile and payload.model_profile != run.model_profile:
                raise ConflictError("运行中不能切换模型，请将该消息加入队列")
            message = create_message(db, workspace_id=ctx.workspace_id, conversation_id=conv.id,
                role="user", content=payload.content, parts=[text_part(payload.content)])
            item = {"request_id": payload.request_id, "message_id": message.id, "sequence": message.sequence, "content": payload.content,
                    "fingerprint": fingerprint, "accepted": True}
            task = tasks.ensure_contract(db, run, dict(snapshot.get("business_task") or {}))
            task = tasks.update_contract(task, payload.content, payload.required_deliveries)
            run.context_snapshot = {**snapshot, "steering_messages": previous + [item],
                                    "user_request": payload.content, "business_task": task}
            touch_conversation(db, conv.id)
            db.commit()
        return {"run_id": run.id, "request_id": item["request_id"], "duplicate": duplicate is not None,
                "content": [{"type": "text", "text": item["content"]}],
                "user_message": {"id": item["message_id"], "role": "user", "content": item["content"],
                                 "request_id": item["request_id"], "run_id": run.id, "status": "completed"}}


def append_event(run_id, event, data=None, native_sequence=None):
    """每个运行独立串行写事件；DB 主键作为跨运行稳定游标。"""
    with lock_for("events:" + run_id), session() as db:
        run = get_run(db, run_id)
        snapshot = run.context_snapshot or {}
        if event in {"tool.started", "tool.completed", "tool.failed"}:
            from . import receipts
            call_id = (data or {}).get("call_id") or (data or {}).get("id")
            canonical = next((row for row in db.execute(receipts.rows_for_run(db, run).where(
                RunEvent.__table__.c.receipt_key.is_not(None))).all()
                if row.payload.get("data", {}).get("call_id") == call_id), None)
            if canonical:
                owner = canonical.payload["receipt"]["identity"]
                agent, seq = (data or {}).get("agent_session_id"), (data or {}).get("invocation_seq")
                if agent is None or seq is None:
                    # 旧传输失败没有完整身份，只阻止覆盖，不能猜测并广播其他代理的结果。
                    return {"id": canonical.id, "sequence": canonical.sequence, "run_id": run.id,
                            "event": "tool.receipt.ignored", "data": {"run_id": run.id, "call_id": call_id}}
                if agent != owner["native_agent_session_id"] or seq != owner["invocation_seq"]:
                    raise ConflictError("工具事件身份与已有规范回执冲突")
                # 事件投影入口不承担结果读取鉴权；即使身份一致也不提前回传旧业务载荷。
                return {"id": canonical.id, "sequence": canonical.sequence, "run_id": run.id,
                        "event": "tool.receipt.ignored", "data": {"run_id": run.id, "call_id": call_id}}
        if native_sequence is not None:
            duplicate = ordered_payload_rows(db, RunEvent, (RunEvent.run_id == run.id,
                RunEvent.event_type == "chat.stream"), (RunEvent.id.desc(),))
            previous = next((row for row in duplicate if row.payload.get("event") == event and row.payload.get("native_sequence") == native_sequence), None)
            if previous:
                return {"id": previous.id, "sequence": previous.sequence, "run_id": run.id,
                        "session_id": session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id),
                        "data": previous.payload.get("data") or {}}
        identity = session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id)
        body = {**(data or {}), "run_id": run.id, "session_id": identity, "message_id": snapshot["assistant_message_id"]}
        message = db.get(Message, snapshot["assistant_message_id"])
        if event == "run.started":
            run.status = "running"
            run.started_at = run.started_at or datetime.utcnow()
            message.status = "running"
        if event in {"run.started", "run.completed", "run.failed", "run.cancelled"}:
            body.update(run_timing(run))
        if event in {"message.delta", "message.replace"}:
            message.content = (message.content or "") + str(body.get("delta") or "") if event == "message.delta" else str(body.get("content") or "")
            message.parts_json = [part for part in (message.parts_json or []) if part.get("type") != "text"] + [text_part(message.content)]
            if event == "message.replace" and body.get("committed") is False:
                message.parts_json = [part for part in message.parts_json if part.get("type") != "reasoning"]
        if event in {"reasoning.delta", "reasoning.replace"}:
            existing = next((part for part in message.parts_json or [] if part.get("type") == "reasoning"), {})
            text = str(body.get("delta") or body.get("text") or "")
            if event == "reasoning.delta":
                text = str(existing.get("text") or "") + text
            else:
                text = str(body.get("content") or body.get("text") or "")
            message.parts_json = [part for part in message.parts_json or [] if part.get("type") != "reasoning"] + [
                {"type": "reasoning", "text": text, "attempt_id": body.get("attempt_id")}]
        if event in {"tool.started", "tool.completed", "tool.failed"}:
            call_id = body.get("call_id") or body.get("id")
            name = body.get("tool_name") or body.get("name") or "tool"
            parts = list(message.parts_json or [])
            if call_id:
                if event == "tool.started":
                    part = {**tool_call_part(call_id, name, body.get("arguments") or {}), "native": bool(body.get("native")), "status": "running"}
                else:
                    outcome = body.get("result") or ({"status": "failed", "error_summary": body.get("error") or "工具未完成"}
                                                      if event == "tool.failed" else {"status": "success"})
                    if isinstance(outcome, dict):
                        outcome = {"status": body.get("status") or ("failed" if event == "tool.failed" else "success"), **outcome}
                    part = {**tool_result_part(call_id, name, outcome), "native": bool(body.get("native"))}
                    parts = [{**item, "status": outcome.get("status", "failed")} if item.get("id") == call_id and item.get("type") == "tool_call" else item for item in parts]
                message.parts_json = [item for item in parts if not (item.get("id") == call_id and item.get("type") == part["type"])] + [part]
        if event == "todo.updated":
            message.parts_json = [part for part in message.parts_json or [] if part.get("type") != "native_todos"] + [
                {"type": "native_todos", "todos": body.get("todos") or [], "run_id": run.id}]
        if event in {"task.updated", "queue.updated", "question.requested", "question.pending", "question.resolved", "todo.updated"}:
            projections = dict(snapshot.get("dsh_projections") or {})
            projections[event] = body
            run.context_snapshot = {**snapshot, "dsh_projections": projections}
        sequence = int(db.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0) + 1
        row = RunEvent(workspace_id=run.workspace_id, run_id=run.id, event_type="chat.stream", sequence=sequence,
                       payload={"event": event, "data": body, "native_sequence": native_sequence}, created_at=datetime.utcnow())
        db.add(row)
        db.commit()
        return {"id": row.id, "sequence": sequence, "run_id": run.id, "session_id": identity, "data": body}


def settle(run_id, status, content="", error=None, usage=None):
    if status not in {"completed", "failed", "cancelled", "partial", "interrupted"}:
        raise ValidationError("运行终态无效")
    with lock_for("tool:" + run_id), lock_for("events:" + run_id), session() as db:
        run = get_run(db, run_id)
        snapshot = run.context_snapshot or {}
        message = db.get(Message, snapshot["assistant_message_id"])
        if snapshot.get("dsh_settled"):
            result = {"status": run.status, "native_status": snapshot.get("native_status", run.status), "run_id": run.id, "message_id": message.id,
                      "content": message.content or "", "task": snapshot.get("business_task") or {},
                      "deliveries": tasks.deliveries(db, run), "duplicate": True,
                      **({"error": {"code": run.error_code, "message": run.error_summary}} if run.error_code else {})}
            release_control(run.id)
            return result
        from .dispatch_state import reconcile_dispatches, unconfirmed_operations
        # 先等正在执行的工具释放锁，再把遗留回执收尾，不能把活跃请求提前判成未知。
        counts = reconcile_dispatches(db, run)
        unresolved = unconfirmed_operations(db, run)
        native_status = status
        if status == "completed" and (unresolved or any(counts.values())):
            status = "interrupted"
            if unresolved or counts["unknown"]:
                error = {"code": "tool_result_unconfirmed", "message": "本次任务仍有工具结果未核实，不能确认全部完成。请先核实原操作，避免重复执行。"}
            elif counts["not_dispatched"]:
                error = {"code": "tool_not_dispatched", "message": "本次任务仍有工具在派发前中断，尚未全部执行。"}
            else:
                error = {"code": "tool_interrupted", "message": "本次任务仍有工具执行中断，尚未全部完成。"}
        status, task, deliveries = tasks.verify(db, run, status)
        if task.get("native_status") != native_status:
            task = {**task, "native_status": native_status}
            run.context_snapshot = {**(run.context_snapshot or {}), "business_task": task}
        if status == "partial":
            error = {"code": "delivery_incomplete", "message": "本轮执行已结束，但要求的文件或业务成果尚未全部通过交付核验。"}
        message.content = content or message.content or (error or {}).get("message", "")
        from app.modules.business_ai.alert_evidence import verified_alert_reply
        from app.modules.business_ai.inventory_evidence import verified_inventory_reply
        grounded = verified_alert_reply(db, run, message.content, status, error)
        if grounded is None:
            grounded = verified_inventory_reply(db, run, message.content, status, error)
        if grounded:
            message.content, status, error = grounded["content"], grounded["status"], grounded["error"]
            if status == "completed" and (task.get("missing_deliveries") or task.get("constraint_failures")):
                status = "partial"
                error = {"code": "delivery_incomplete", "message": "本轮执行已结束，但要求的文件或业务成果尚未全部通过交付核验。"}
            if task.get("status") != status:
                task = {**task, "status": status}
                run.context_snapshot = {**(run.context_snapshot or {}), "business_task": task}
        if (status == "partial" and not tasks.clarification_choices(message.content)
                and any(item["type"] == "dashboard" for item in task.get("missing_deliveries") or [])):
            reasons = list(dict.fromkeys(item.get("validation_error") for item in deliveries
                                        if item.get("type") == "dashboard" and item.get("validation_error")))
            note = "大屏交付核验未通过：" + ("；".join(reasons) if reasons else "尚未保存可用的大屏成果") + "。"
            # 服务端验收结果置于正文首行，避免模型的完成宣称掩盖未交付事实。
            message.content = note + "\n\n" + message.content
        if status == "partial" and task.get("constraint_failures"):
            reasons = list(dict.fromkeys(item["message"] for item in task["constraint_failures"] if item.get("message")))
            message.content = "交付核验仍有缺项：" + "；".join(reasons) + "。已生成文件仍可下载。\n\n" + message.content
        message.status = status
        from app.core.parts import artifact_part
        preserved = [part for part in message.parts_json or [] if part.get("type") not in {"text", "artifact", "error", "business_result", "delivery_check"}]
        ready = [item for item in deliveries if item["status"] == "ready"]
        message.parts_json = preserved + [artifact_part(item) for item in ready if item.get("artifact_id")] + [
            {"type": "business_result", "id": item["id"], "data": item["business_result"]}
            for item in ready if item.get("business_result")] + [
            {"type": "delivery_check", "id": "check-" + item["id"], "report_id": item["report_id"],
             "status": item["status"], "validation_error": item.get("validation_error")}
            for item in deliveries if item.get("report_id")] + [text_part(message.content)]
        if error:
            message.parts_json += [error_part(error.get("message") or "本次任务未完成", error.get("code") or "task_failed")]
        finalize_run(db, run, status=status, error_code=(error or {}).get("code"), error_summary=(error or {}).get("message"), model_usage=usage)
        run.context_snapshot = {**(run.context_snapshot or {}), "dsh_settled": True, "native_status": native_status}
        touch_conversation(db, run.conversation_id)
        db.commit()
        result = {"status": status, "native_status": native_status, "run_id": run.id, "message_id": message.id, "content": message.content, "task": task,
                  "deliveries": deliveries, **({"error": error} if error else {})}
        release_control(run.id)
        return result


def history_for_context(ctx, conversation_id, after=0):
    conv = owned_conversation(ctx, conversation_id)
    rows = ordered_payload_rows(ctx.db, Run, (Run.workspace_id == ctx.workspace_id,
        Run.started_by_user_id == ctx.user_id, Run.conversation_id == conversation_id),
        (Run.created_at.desc(), Run.id.desc()), limit=MAX_HISTORY_RUNS + 1)
    runs_truncated = len(rows) > MAX_HISTORY_RUNS
    # 查询按新到旧排列；额外一条只用于判断截断，不能挤掉最新运行。
    rows = list(reversed(rows[:MAX_HISTORY_RUNS]))
    mappings = {identity: run.id for run in rows for identity in [(run.context_snapshot or {}).get("user_message_id"),
                (run.context_snapshot or {}).get("assistant_message_id")] if identity}
    requests = {(run.context_snapshot or {}).get("user_message_id"): (run.context_snapshot or {}).get("client_request_id") for run in rows}
    for run in rows:
        for extra in (run.context_snapshot or {}).get("steering_messages") or []:
            if isinstance(extra, dict) and extra.get("message_id"):
                mappings[extra["message_id"]] = run.id
                requests[extra["message_id"]] = extra.get("request_id")
    timings = {run.id: run_timing(run) for run in rows}
    message_rows = chat_service.list_messages(ctx.db, conversation_id, limit=MAX_HISTORY_MESSAGES + 1)
    messages_truncated = len(message_rows) > MAX_HISTORY_MESSAGES
    message_rows = message_rows[-MAX_HISTORY_MESSAGES:]
    messages = [{**chat_service.message_out(message), "run_id": mappings.get(message.id), "request_id": requests.get(message.id),
                 "timing": timings.get(mappings.get(message.id))}
                for message in message_rows]
    ids = [run.id for run in rows]
    all_events = ordered_payload_rows(ctx.db, RunEvent, (RunEvent.workspace_id == ctx.workspace_id,
        RunEvent.run_id.in_(ids), RunEvent.event_type == "chat.stream"),
        (RunEvent.id.desc(),), limit=MAX_HISTORY_EVENTS + 1) if ids else []
    events_truncated = len(all_events) > MAX_HISTORY_EVENTS
    all_events = all_events[:MAX_HISTORY_EVENTS]
    all_events = list(reversed(all_events))
    events = [{"id": row.id, "event": row.payload.get("event"), "data": row.payload.get("data") or {}, "run_id": row.run_id}
              for row in all_events if row.id > int(after or 0)]
    grouped = {}
    for run in rows:
        if (run.context_snapshot or {}).get("task_id"):
            grouped[run.context_snapshot["task_id"]] = run
    business_tasks, produced = [], []
    for run in grouped.values():
        task = dict((run.context_snapshot or {}).get("business_task") or {})
        task["run_ids"] = [item.id for item in tasks.task_runs(ctx.db, run)]
        business_tasks.append(task)
        produced.extend(tasks.deliveries(ctx.db, run))
    latest = rows[-1] if rows else None
    snapshot = latest.context_snapshot or {} if latest else {}
    pending, todos = {}, []
    active_ids = {row.id for row in rows if row.status in {"created", "queued", "running", "waiting", "steering"}}
    for row in all_events:
        event, data = row.payload.get("event"), row.payload.get("data") or {}
        identity = data.get("question_id") or data.get("id")
        if event in {"question.requested", "question.pending"} and identity and row.run_id in active_ids:
            pending[identity] = {**data, "native": event == "question.pending"}
        if event == "question.resolved":
            pending.pop(identity, None)
        if event == "todo.updated":
            todos = data.get("todos") or []
    return {"id": conv.id, "title": conv.title, "business_module": chat_service.conversation_modules(ctx.db, [conv.id]).get(conv.id),
            "session_id": session_identity(ctx.workspace_id, ctx.user_id, conversation_id), "messages": messages,
            "runs": [{"id": row.id, "run_id": row.id, "status": row.status,
                      "native_status": (row.context_snapshot or {}).get("native_status"), **run_timing(row)} for row in rows],
            "last_run": {"id": latest.id, "status": latest.status, "error_code": latest.error_code,
                         **run_timing(latest),
                         "context_snapshot": snapshot, "model_profile": latest.model_profile,
                         "plugin_version_id": latest.plugin_version_id} if latest else None,
            "events": events, "cursor": all_events[-1].id if all_events else 0,
            "history_window": {"runs_limit": MAX_HISTORY_RUNS, "messages_limit": MAX_HISTORY_MESSAGES,
                               "events_limit": MAX_HISTORY_EVENTS, "runs_truncated": runs_truncated,
                               "messages_truncated": messages_truncated,
                               "events_truncated": events_truncated},
            "projections": {"mode": snapshot.get("mode") or "standard", "model": latest.model_profile if latest else None,
                "queue": [{"run_id": row.id, "id": row.id, "content": (row.context_snapshot or {}).get("submitted_content"),
                           "request_id": (row.context_snapshot or {}).get("client_request_id"),
                           "status": row.status, "delivery_mode": (row.context_snapshot or {}).get("delivery_mode", "queue")}
                          for row in rows if row.status in {"queued", "created"}],
                "tasks": business_tasks, "deliveries": produced, "pending_questions": list(pending.values()), "todos": todos}}


def history(request, conversation_id, after=0):
    raw = {**request, "method": "GET", "path": f"/api/chat/conversations/{conversation_id}"}
    with session() as db:
        return history_for_context(authenticate(db, raw), conversation_id, after)


def recovery_request_owners(db, run):
    """从同会话持久运行取轻量请求归属，不受 runs.view 展示过滤或历史窗口限制。"""
    runs = Run.__table__
    owners = {}
    steering = {}
    rows = db.execute(select(runs.c.id, runs.c.context_snapshot["steering_messages"]).where(
        runs.c.workspace_id == run.workspace_id, runs.c.started_by_user_id == run.started_by_user_id,
        runs.c.conversation_id == run.conversation_id))
    for identity, messages in rows:
        owners.setdefault(identity, set()).add(identity)
        steering[identity] = []
        for item in messages if isinstance(messages, list) else []:
            request = item.get("request_id") if isinstance(item, dict) else None
            if isinstance(request, str) and request and item.get("accepted") is True:
                owners.setdefault(request, set()).add(identity)
                if request not in steering[identity]:
                    steering[identity].append(request)
    return owners, steering


def recover():
    """只重建非终态运行；是否接续由宿主读取原生历史后决定。"""
    recovered = []
    with session() as db:
        ownership = {}
        for run in ordered_payload_rows(db, Run, (Run.status.in_(["queued", "created", "running"]),), (Run.created_at, Run.id)):
            if ((run.context_snapshot or {}).get("chat_engine") == "dsh"
                    and not (run.context_snapshot or {}).get("dsh_model_only")):
                try:
                    from .dispatch_state import summarize_dispatches
                    prepared_run = prepared(db, run)
                    prepared_run["tool_dispatches"] = summarize_dispatches(db, run)
                    key = (run.workspace_id, run.started_by_user_id, run.conversation_id)
                    if key not in ownership:
                        ownership[key] = recovery_request_owners(db, run)
                    owners, steering = ownership[key]
                    prepared_run["recovery_identity"] = {"root_request_id": run.id,
                        "steering_request_ids": steering.get(run.id, []),
                        "other_request_ids": sorted(identity for identity, identities in owners.items() if identities - {run.id}),
                        "ambiguous_request_ids": sorted(identity for identity, identities in owners.items() if len(identities) > 1)}
                    recovered.append(prepared_run)
                except Exception as exc:
                    code = getattr(exc, "code", "recovery_failed")
                    summary = "运行条件已变化，无法恢复本次任务。"
                    from .dispatch_state import reconcile_dispatches
                    reconcile_dispatches(db, run)
                    finalize_run(db, run, status="failed", error_code=code, error_summary=summary)
                    # 冷恢复失败时同步关闭已经创建的助手消息，避免前端永远看到空的 queued/running 气泡。
                    assistant_id = (run.context_snapshot or {}).get("assistant_message_id")
                    assistant = db.get(Message, assistant_id) if assistant_id else None
                    if assistant and assistant.status not in {"completed", "failed", "cancelled"}:
                        assistant.status = "failed"
                        assistant.content = summary
                        assistant.parts_json = list(assistant.parts_json or []) + [error_part(summary, code)]
                    release_control(run.id)
        db.commit()
    return {"runs": recovered}


def queue_update(run_id, action, content=None):
    with lock_for("events:" + run_id), session() as db:
        run = get_run(db, run_id)
        if run.status not in {"queued", "created"}:
            raise ConflictError("只有等待中的消息可以修改")
        snapshot = dict(run.context_snapshot or {})
        if action == "remove":
            finalize_run(db, run, status="cancelled")
            db.get(Message, snapshot["assistant_message_id"]).status = "cancelled"
        elif action == "edit":
            if not isinstance(content, str) or not content.strip():
                raise ValidationError("消息内容不能为空")
            # 队列编辑与首次发送共享正文边界，避免绕过 SendInput 的校验。
            if len(content) > 120_000:
                raise ValidationError("消息内容不能超过 120000 个字符")
            snapshot.update(submitted_content=content, user_request=content, content=content)
            task = dict(snapshot.get("business_task") or {})
            task = tasks.update_contract(task, content, replace=not bool(task.get("continuation_of_run_id")))
            task.update(title=content[:120],
                        missing_deliveries=[], requires_new_delivery=False, updated_at=datetime.utcnow().isoformat())
            snapshot["business_task"] = task
            run.context_snapshot = snapshot
            user_message = db.get(Message, snapshot["user_message_id"])
            user_message.content = content
            user_message.parts_json = [part for part in user_message.parts_json or [] if part.get("type") != "text"] + [text_part(content)]
        elif action == "steer":
            run.context_snapshot = {**snapshot, "delivery_mode": "steer"}
        else:
            raise ValidationError("队列操作无效")
        response = prepared(db, run) if action == "edit" else None
        db.commit()
        if response:
            return response
        return {"run_id": run.id, "status": run.status, "content": (run.context_snapshot or {}).get("submitted_content"),
                "delivery_mode": (run.context_snapshot or {}).get("delivery_mode", "queue")}
