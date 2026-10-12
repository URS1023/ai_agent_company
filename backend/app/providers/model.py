# -*- coding: utf-8 -*-
# 模型调用网关（模块 docstring 见下）：所有 chat/embedding/vision 调用都从这里出去。
#
# 位置：被 `runtime/agent.py`（对话主体）、`runtime/agent._generate_followups`（追问生成）、
# `modules/knowledge`（向量化）、`modules/files/vision`（识图）等调用；上层不认识具体模型名，
# 只传 profile 名（如 reasoning / chat / vision / embedding）。
#
# 四条贯穿本模块的约束：
# 1. 配置解析优先级：DB 的 model_profiles（密钥按名字引用，绝不出现在返回值之外）→ 环境默认
#    （AGNES_*/SILICONFLOW_*）；工作区上下文缺失时用 __no_workspace__ 兜底，
#    避免误取其他工作区最新的 profile。
# 2. 用量与预算：每次调用前后都经 `_persist_usage` 落库，并在调用前 `_governed_call` 检查预算；
#    用量落库失败只告警，不影响业务回答。
# 3. 错误分类：由 `_raise_balance_error` 把 402/429/连接类错误转成带用户可见文案的
#    ModelBalanceError/ModelRateLimitError/ModelUnavailableError，供 agent 映射错误码与提示。
# 4. 重试边界：对话内的连接级重试最多一次且只能发生在"请求尚未建立"时
#    （`_create_chat_completion`），流式过程中断不重放，避免重复执行已发生的工具副作用。
"""Model Gateway.

Unified access to chat / embedding / vision models through the OpenAI
compatible protocol. Profile resolution:
  1. enabled model_profiles row in DB (secret referenced by name)
  2. environment defaults (AGNES_*, SILICONFLOW_*)
Plugin / runtime code never hard-codes a concrete model name.
"""
from __future__ import annotations

from app.core.observability import log_event, observe

import base64
from contextlib import contextmanager
import json
import logging
import time
from threading import Event, Lock, Thread
from typing import List, Optional
from urllib.parse import urlsplit

import httpx
from openai import OpenAI, APIConnectionError
from sqlalchemy import select

from ..config import settings
from ..errors import AppError, ModelBalanceError, ModelError, ModelRateLimitError, ModelUnavailableError, ModelResponseIncompleteError

logger = logging.getLogger("model")


def _raise_balance_error(exc: Exception) -> None:
    """把供应商异常分类成用户可见的模型错误（只在匹配时抛出，匹配不到则原样返回）。

    覆盖：HTTP 402（余额不足）、429（限流）、连接/超时/502-504（服务不可用）。
    文案统一强调"已完成的结果仍然保留"，因为对话里已发生的查询/工具结果确实不会回滚。
    `retryable=False` 表示上层不应自动重放：重放会重复消耗额度或重复执行工具。
    副作用：抛出 ModelBalanceError/ModelRateLimitError/ModelUnavailableError（带 provider_status）。
    """
    if isinstance(exc, AppError):
        return  # Application integrity errors are not upstream HTTP 502 failures.
    if getattr(exc, 'status_code', None) == 402:
        raise ModelBalanceError(
            '当前模型账户余额不足，暂时无法生成回答。请充值后重试，或在对话右侧切换到可用模型。'
            '已完成的结果仍然保留。', provider_status=402, retryable=False,
        ) from exc
    if getattr(exc, 'status_code', None) == 429:
        raise ModelRateLimitError(
            '当前模型服务限流，暂时无法继续生成。请稍后重试，或在对话右侧切换模型。已完成的查询结果仍然保留。',
            provider_status=429, retryable=False,
        ) from exc
    # SSE iteration can expose raw HTTPX errors after the SDK has returned the
    # stream. Classify these without replaying the stream or any completed tool.
    if isinstance(exc, (APIConnectionError, httpx.TimeoutException, httpx.NetworkError,
                        httpx.RemoteProtocolError)) or getattr(exc, 'status_code', None) in {502, 503, 504}:
        raise ModelUnavailableError(
            '当前模型服务连接暂时不可用，请稍后重试或切换模型。已完成的查询结果仍然保留。',
            retryable=False,
        ) from exc


# 只对"建连失败"（APIConnectionError）做最多一次重试，且额度由本次对话的
# TaskControl 统一发放（`claim_model_connection_retry`），因此整轮对话最多补一次连接。
# 重试继承调用方的连接/读取配置，并禁用 SDK 自身重试；无限生成等待不会被
# 重试偷偷改回 60 秒。有限任务仍由 TaskControl 的检查点管理。
# 边界：流式响应中途失败、任何 HTTP 状态错误都不在此重试——那些情况可能已产生正文或工具调用。
def _create_chat_completion(client, **kwargs):
    """One connection recovery shared by a chat turn; no tool/SQL is replayed."""
    from app.modules.runtime.task_control import current
    try:
        return client.chat.completions.create(**kwargs)
    except APIConnectionError:
        control = current.get()
        if control is None or not control.claim_model_connection_retry():
            raise
        log_event("model.connection.retry", "模型建连失败，使用本轮唯一重试额度", level=logging.WARNING, attempt=2)
        # Creation has not delivered text or a tool call. A streaming response
        # which fails later is never replayed here, nor is any HTTP status error.
        return client.with_options(max_retries=0).chat.completions.create(**kwargs)


def _governed_call(workspace_id: Optional[str]) -> None:
    """调用前的预算闸门：不通过则抛 ModelError，阻止继续消耗额度。无副作用（只读校验）。"""
    from app.modules.model.service import enforce_budget
    enforce_budget(workspace_id)


def _persist_usage(*, workspace_id: Optional[str], profile_name: str, cfg: dict,
                   capability: str, usage: Optional[dict], started: float,
                   status: str, usage_context: Optional[dict], error_code: str | None = None) -> None:
    """落地一次调用的用量/时延/结果，供费用统计与模型管理页展示。

    参数：`started` 是调用开始时的 perf_counter（用于算 latency_ms）；`status` 为 completed/failed；
    `usage_context` 附加上下文（run_id、purpose、source 等），其中 source/source_id 单独成列，
    其余键进 metadata。
    失败处理：整段 try/except —— 用量记录属于旁路，写不进去也不能影响模型调用与用户回答，
    仅打 warning（日志里不带供应商原文，避免泄漏密钥或请求内容）。
    """
    try:
        from app.modules.model.service import record_usage
        context = usage_context or {}
        record_usage(
            workspace_id=workspace_id, profile_name=profile_name,
            provider=cfg.get("provider") or "openai-compatible", model=cfg.get("model") or "unknown",
            capability=capability, usage=usage, pricing=(cfg.get("capabilities") or {}).get("pricing"),
            source=str(context.get("source") or "model_gateway"),
            source_id=context.get("source_id"), status=status,
            latency_ms=int((time.perf_counter() - started) * 1000), error_code=error_code,
            metadata={key: value for key, value in context.items() if key not in {"source", "source_id"}},
        )
    except Exception as exc:
        logger.warning("model usage persistence failed (%s)", type(exc).__name__)


def _client(base_url: str, api_key: str) -> OpenAI:
    """构造 OpenAI 兼容客户端（默认 120s 超时、SDK 层重试 2 次）。

    opencode 网关要求每个会话一个随机 session 头，因此按 host 特征附加
    `x-opencode-session`；其他端点不加，避免把无关头带到第三方服务。
    """
    import uuid
    headers = {}
    if "opencode" in (base_url or "").lower():
        headers["x-opencode-session"] = str(uuid.uuid4())
    return OpenAI(base_url=base_url, api_key=api_key, timeout=120, max_retries=2, default_headers=headers or None)


# 当调用方传的是通用档位名（reasoning/chat/default）且当前对话已选定具体档位时，
# 用对话选定的档位替换——保证追问生成、内部补全等"嵌套调用"与用户选中的模型一致。
# 工作区必须匹配，防止跨工作区串档位。只读，无副作用。
def _chat_profile(profile_name: str, workspace_id: Optional[str]) -> str:
    """Nested reasoning calls inherit this chat job within its workspace."""
    from app.modules.runtime.task_control import current
    control = current.get()
    if (control and control.model_profile and control.model_workspace_id == workspace_id
            and profile_name in {'reasoning', 'chat', 'default'}):
        return control.model_profile
    return profile_name


def _chat_protocol_options(cfg: dict) -> dict:
    """按端点的协议差异补充请求体参数（DeepSeek 官方 + 显式 thinking 开关需要）。"""
    # Stored chat history has content/tools but no reasoning replay protocol.
    # Official DeepSeek supports non-thinking tools; select that explicitly
    # to avoid its default thinking mode rejecting a subsequent tool turn.
    if urlsplit(cfg.get('base_url') or '').hostname == 'api.deepseek.com':
        return {'extra_body': {'thinking': {'type': 'disabled'}}}
    # 大上下文任务若开启 thinking，部分模型会输出超长推理流导致网关超时失败；
    # profile 显式声明 thinking=disabled 时同样关闭（OpenCode etc.，见 R 类排查）。
    runtime = (cfg.get("capabilities") or {}).get("runtime_options") or {}
    if runtime.get("thinking") == "disabled":
        return {'extra_body': {'thinking': {'type': 'disabled'}}}
    return {}


def _profile_config(profile_name: str, workspace_id: Optional[str] = None) -> dict:
    """Resolve provider config (base_url, api_key, model) for a profile name.

    Falls back to environment default mapping when no DB profile registered.

    返回：{'base_url','api_key','model','capabilities','source','provider',['profile_id']}，
    其中 `source` 为 database/environment，用于判断"是否需要额外能力校验"。
    异常：
    - 查到但 `enabled=False` → ModelError("模型配置已停用…")；
    - 密钥找不到/无法解密 → ModelError（见 `_resolve_key_for_profile`）；
    - 数据库读取异常 → ModelError（不吞异常降级到环境默认，避免静默换模型）。
    工作区隔离：查询条件强制 `workspace_id == (workspace_id or "__no_workspace__")`，
    缺少上下文时宁可查不到也不取其他工作区的最新 profile。
    """
    try:
        from ..db import new_session
        from ..models import ModelProfile
        session = new_session()
        try:
            conditions = [
                ModelProfile.profile_name == profile_name,
                ModelProfile.status == "active",
                # A missing context must not select another workspace's newest
                # profile. Context-free startup checks use environment defaults.
                ModelProfile.workspace_id == (workspace_id or "__no_workspace__"),
            ]
            row = session.execute(
                select(ModelProfile)
                .where(*conditions)
                .order_by(ModelProfile.updated_at.desc())
            ).scalars().first()
            if row:
                if not row.enabled:
                    raise ModelError("此模型配置已停用，请启用模型或选择其他配置")
                api_key = _resolve_key_for_profile(session, row)
                return {
                    "base_url": row.endpoint.rstrip("/"),
                    "api_key": api_key,
                    "model": row.model,
                    "capabilities": row.capabilities_json or {},
                    "profile_id": row.id,
                    "source": "database",
                    "provider": row.provider,
                }
        finally:
            session.close()
    except ModelError:
        raise
    except Exception as exc:
        logger.warning("db profile lookup failed (%s)", type(exc).__name__)
        raise ModelError("模型配置读取失败，请检查数据库与工作区模型配置") from exc

    name = (profile_name or "reasoning").lower()
    if name in ("embed", "embedding"):
        return {
            "base_url": settings.SILICONFLOW_BASE_URL,
            "api_key": settings.SILICONFLOW_API_KEY,
            "model": settings.SILICONFLOW_EMBED_MODEL,
            "capabilities": {"capabilities": ["embedding"]},
            "source": "environment",
            "provider": "siliconflow",
        }
    if name in ("vision",):
        return {
            "base_url": settings.SILICONFLOW_BASE_URL,
            "api_key": settings.SILICONFLOW_VISION_API_KEY,
            "model": settings.SILICONFLOW_VISION_MODEL,
            "capabilities": {"capabilities": ["vision", "ocr"]},
            "source": "environment",
            "provider": "siliconflow",
        }
    # chat / reasoning / default
    return {
        "base_url": settings.AGNES_BASE_URL,
        "api_key": settings.AGNES_API_KEY,
        "model": settings.AGNES_CHAT_MODEL,
        "capabilities": {"capabilities": ["chat", "reasoning"]},
        "source": "environment",
        "provider": "agnes",
    }


def _embedding_config(profile_name: str | None, workspace_id: Optional[str] = None) -> tuple[str, dict]:
    """Resolve the selected knowledge embedding profile and provider config.

    ``KnowledgeProfile.embedding_model`` predates the model profile registry and
    is still used by the UI as a free-form model/profile selector.  A value that
    names an active DB profile must be an embedding-capable profile.  A value
    that is not registered is treated as a concrete model override on the
    workspace's normal embedding endpoint, which keeps existing deployments
    compatible while making the selection reach the provider call.

    返回：(用于记量的档位名, 配置字典)；未注册的名字按"具体模型名覆盖"处理，
    但 endpoint 与密钥仍取自工作区受管的 embedding 档位——不允许用任意名字绕过受管端点。
    异常：目标 profile 不具备 embedding 能力时抛 ModelError（明确到档位名，便于管理员修复）。
    """
    selected = (profile_name or "embedding").strip() or "embedding"
    if selected.lower() in {"embed", "embedding"}:
        resolved = _profile_config("embedding", workspace_id=workspace_id)
        if resolved.get("source") == "database":
            capabilities = (resolved.get("capabilities") or {}).get("capabilities") or []
            if "embedding" not in capabilities:
                raise ModelError("默认模型 Profile 不支持向量嵌入")
        return "embedding", resolved

    requested = _profile_config(selected, workspace_id=workspace_id)
    if requested.get("source") == "database":
        capabilities = set((requested.get("capabilities") or {}).get("capabilities") or [])
        if "embedding" not in capabilities:
            raise ModelError(f"模型 Profile“{selected}”不支持向量嵌入")
        return selected, requested

    # Unknown names are accepted as model identifiers, but the endpoint and
    # credentials still come from the governed workspace embedding profile.
    base = _profile_config("embedding", workspace_id=workspace_id)
    base["model"] = selected
    base["selected_profile"] = selected
    return selected, base


def _resolve_key_for_profile(session, profile) -> str:
    """解析 profile 对应的明文密钥（按引用名查 secrets 表，最后回退环境密钥）。

    查找顺序：capabilities.secret_id（显式引用）→ profile.id → profile_name →
    `model-key-<profile_name>`（历史命名兼容）→ 环境密钥。
    权限/状态校验：密钥必须属于同一工作区、status=active、未软删除，否则视为不可用，
    防止跨工作区引用他人密钥。
    异常：显式引用但不可用/解密失败抛 ModelError；都没有时，
    若该端点声明 `auth_required=False` 返回占位串 "not-required"（本地/内网端点），
    否则抛 ModelError("此模型端点尚未配置专用密钥")。
    """
    # Profile secret is stored under secrets with name == profile_id or profile_name.
    from ..models import Secret
    from ..crypto import decrypt_secret
    capabilities = profile.capabilities_json or {}
    secret_id = capabilities.get("secret_id")
    if secret_id:
        row = session.get(Secret, secret_id)
        if (
            row
            and row.workspace_id == profile.workspace_id
            and row.status == "active"
            and row.deleted_at is None
        ):
            try:
                return decrypt_secret(row.ciphertext, row.nonce, row.key_version)
            except Exception:
                logger.warning("model profile secret could not be decrypted: %s", profile.id)
                raise ModelError("模型密钥无法解密，请重新配置")
        raise ModelError("模型引用的密钥不存在、已停用或不属于当前工作区")
    for name in (profile.id, profile.profile_name, f"model-key-{profile.profile_name}"):
        row = session.execute(select(Secret).where(
            Secret.workspace_id == profile.workspace_id,
            Secret.name == name,
            Secret.status == "active",
            Secret.deleted_at.is_(None),
        )).scalars().first()
        if row:
            try:
                return decrypt_secret(row.ciphertext, row.nonce, row.key_version)
            except Exception:
                continue
    environment_key = _environment_key_for_profile(profile)
    if environment_key:
        return environment_key
    if capabilities.get("auth_required") is False:
        return "not-required"
    raise ModelError("此模型端点尚未配置专用密钥")


# 仅当 profile 的端点与某个环境默认端点"逐字符一致"时才复用该环境密钥，
# 使模型管理页显示的"已配置密钥"与真实可执行性一致；端点不同则返回空串，
# 由调用方按"未配置密钥"处理，避免拿 A 端点的密钥去请求 B 端点。
def _environment_key_for_profile(profile) -> str:
    """Use the same endpoint boundary for execution and administration status."""
    if profile.provider == "agnes" and profile.endpoint.rstrip("/") == settings.AGNES_BASE_URL.rstrip("/"):
        return settings.AGNES_API_KEY
    if profile.endpoint.rstrip("/") == settings.SILICONFLOW_BASE_URL.rstrip("/") and profile.profile_name.lower() == "vision":
        return settings.SILICONFLOW_VISION_API_KEY
    if profile.endpoint.rstrip("/") == settings.SILICONFLOW_BASE_URL.rstrip("/"):
        return settings.SILICONFLOW_API_KEY
    return ""


def _response_usage(response) -> dict:
    value = getattr(response, 'usage', None)
    if not value:
        return {}
    return {key: getattr(value, key, 0) or 0 for key in
            ('prompt_tokens', 'completion_tokens', 'total_tokens')}


def _incomplete_response(reason: str, *, retryable: bool = True):
    raise ModelResponseIncompleteError(
        '模型返回内容不完整，尚未作为最终回答或执行操作。',
        reason=reason, retryable=retryable,
    )


def _validate_chat_response(content, tool_calls: list, finish_reason, tools=None) -> None:
    """Only terminal, nonempty responses and complete JSON object calls may pass."""
    if finish_reason == 'content_filter':
        _incomplete_response('content_filter', retryable=False)
    if finish_reason not in {'stop', 'tool_calls', 'function_call'}:
        _incomplete_response('truncated_response' if finish_reason == 'length' else 'missing_finish_reason')
    if not isinstance(content, str):
        _incomplete_response('invalid_content')
    if not tool_calls:
        if finish_reason in {'tool_calls', 'function_call'}:
            _incomplete_response('invalid_tool_call')
        if not content.strip():
            _incomplete_response('empty_response')
        return
    allowed = {item.get('function', {}).get('name') for item in (tools or [])
               if isinstance(item, dict) and isinstance(item.get('function'), dict)}
    ids = set()
    for call in tool_calls:
        function = call.get('function') or {}
        call_id, name = call.get('id'), function.get('name')
        if (call.get('type') != 'function' or not isinstance(call_id, str) or not call_id.strip()
                or call_id in ids or not isinstance(name, str) or name not in allowed):
            _incomplete_response('invalid_tool_call')
        ids.add(call_id)
        arguments = function.get('arguments')
        if not isinstance(arguments, str):
            _incomplete_response('invalid_tool_arguments')
        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate argument key')
                result[key] = value
            return result
        def invalid_constant(value):
            raise ValueError('non-finite JSON constant')
        try:
            parsed = json.loads(arguments, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
        except (TypeError, ValueError, RecursionError):
            _incomplete_response('invalid_tool_arguments')
        if not isinstance(parsed, dict):
            _incomplete_response('invalid_tool_arguments')


def _model_wait_timeout(request_timeout):
    # A model may take arbitrarily long to think or generate. Connection, write,
    # and pool acquisition remain bounded independently of generation duration.
    if request_timeout is None:
        return httpx.Timeout(connect=15, read=None, write=30, pool=15)
    return request_timeout


@contextmanager
def _managed_chat_client(client, cancel_event=None):
    """Close on user cancellation even while the reader awaits the next chunk."""
    finished = Event()
    close_lock = Lock()
    closed = False

    def close():
        nonlocal closed
        with close_lock:
            if closed:
                return
            closed = True
        try:
            client.close()
        except Exception as exc:
            logger.warning('model_client_close_failed (%s)', type(exc).__name__)

    def watch_cancel():
        while not finished.wait(0.05):
            if cancel_event.is_set():
                close()
                return

    if cancel_event is not None:
        Thread(target=watch_cancel, name='model-cancel-watch', daemon=True).start()
    try:
        yield
    finally:
        finished.set()
        close()


def _check_model_cancel(cancel_event=None):
    from app.modules.runtime.task_control import checkpoint
    checkpoint()
    if cancel_event is not None and cancel_event.is_set():
        raise TimeoutError('Model request cancelled')


def _close_model_stream(stream):
    try:
        stream.close()
    except Exception as exc:
        # A failed close must never hide the actual provider/cancellation error.
        logger.warning('model_stream_close_failed (%s)', type(exc).__name__)


@observe("model.chat", "非流式模型调用", fields={"profile": "profile_name", "workspace_id": "workspace_id", "run_id": "usage_context.run_id", "purpose": "usage_context.purpose"})
def chat_for_run(profile_name: str, messages: List[dict], tools: Optional[List[dict]] = None,
                 temperature: float = 0.2, *, workspace_id: Optional[str] = None,
                 usage_context: Optional[dict] = None,
                 response_format: Optional[dict] = None,
                 request_timeout: Optional[float] = None,
                 max_retries: Optional[int] = None) -> dict:
    """返回完整回答或已校验的工具调用，截断响应不能作为成功结果。

    未指定请求时限的对话等待生成或用户取消；工具与辅助调用保留其明确时限。
    响应恢复由运行时负责，避免重复执行已经完成的业务工具。
    """
    from app.business_worker import transport as business_transport
    if business_transport.active():
        return business_transport.generate(profile_name, messages, tools, temperature,
            workspace_id=workspace_id, usage_context=usage_context, response_format=response_format,
            request_timeout=request_timeout)
    from app.modules.runtime.task_control import checkpoint, remaining_timeout, current
    checkpoint()
    control = current.get()
    if control:
        request_timeout = remaining_timeout(request_timeout)
        max_retries = 0
    profile_name = _chat_profile(profile_name, workspace_id)
    cfg = _profile_config(profile_name, workspace_id=workspace_id)
    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        raise ModelError('模型端点、名称或密钥未配置')
    _governed_call(workspace_id)
    log_event("model.request.ready", "模型参数就绪，准备发送请求", profile=profile_name,
              model=cfg.get("model"), provider=cfg.get("provider"),
              message_count=len(messages), tool_count=len(tools or []))
    started = time.perf_counter()
    client = _client(cfg['base_url'], cfg['api_key'])
    options = {'timeout': _model_wait_timeout(request_timeout)}
    if max_retries is not None:
        options['max_retries'] = max_retries
    if options:
        client = client.with_options(**options)
    kwargs = dict(model=cfg['model'], messages=messages, temperature=temperature)
    kwargs.update(_chat_protocol_options(cfg))
    if response_format is not None:
        kwargs['response_format'] = response_format
    if tools:
        kwargs.update(tools=tools, tool_choice='auto')
    usage = {}
    try:
        with _managed_chat_client(client, control.cancelled if control else None):
            resp = _create_chat_completion(client, **kwargs)
            checkpoint()
            usage = _response_usage(resp)
            choices = getattr(resp, 'choices', None)
            if not choices:
                _incomplete_response('empty_response')
            msg = getattr(choices[0], 'message', None)
            if msg is None:
                _incomplete_response('empty_response')
            tool_calls = []
            for tc in (getattr(msg, 'tool_calls', None) or []):
                function = getattr(tc, 'function', None)
                tool_calls.append({
                    'id': getattr(tc, 'id', None), 'type': getattr(tc, 'type', None),
                    'function': {'name': getattr(function, 'name', None),
                                 'arguments': getattr(function, 'arguments', None)},
                })
            content = getattr(msg, 'content', None) or ''
            finish_reason = getattr(choices[0], 'finish_reason', None)
            _validate_chat_response(content, tool_calls, finish_reason, tools)
    except Exception as exc:
        _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                       capability='chat', usage=usage, started=started, status='failed',
                       usage_context=usage_context, error_code=getattr(exc, 'code', type(exc).__name__))
        checkpoint()
        _raise_balance_error(exc)
        raise
    _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                   capability='chat', usage=usage, started=started, status='completed',
                   usage_context=usage_context)
    return {'content': content, 'tool_calls': tool_calls, 'usage': usage, 'finish_reason': finish_reason,
            'model': cfg['model'], 'provider': cfg.get('provider') or 'openai-compatible'}


@observe("model.stream", "流式模型调用", fields={"profile": "profile_name", "workspace_id": "workspace_id", "run_id": "usage_context.run_id", "purpose": "usage_context.purpose"})
def stream_chat(profile_name: str, messages: List[dict], tools: Optional[List[dict]] = None,
                temperature: float = 0.2, on_delta=None, on_done=None, *, workspace_id: Optional[str] = None,
                usage_context: Optional[dict] = None,
                request_timeout: Optional[float] = None, max_retries: Optional[int] = None,
                cancel_event=None):
    """提供文本回调，只把完整且已校验的结果交给 on_done。"""
    from app.business_worker import transport as business_transport
    if business_transport.active():
        _check_model_cancel(cancel_event)
        result = business_transport.generate(profile_name, messages, tools, temperature,
            workspace_id=workspace_id, usage_context=usage_context, request_timeout=request_timeout,
            cancel_event=cancel_event)
        _check_model_cancel(cancel_event)
        # 业务辅助调用保留回调合同；主会话逐块流由 DSH 原生订阅提供。
        if on_delta and result.get("content"):
            on_delta(result["content"])
        if on_done:
            on_done(result)
        return result
    from app.modules.runtime.task_control import current, remaining_timeout
    control = current.get()
    if control:
        request_timeout = remaining_timeout(request_timeout)
        cancel_event = cancel_event if cancel_event is not None else control.cancelled
    _check_model_cancel(cancel_event)
    profile_name = _chat_profile(profile_name, workspace_id)
    cfg = _profile_config(profile_name, workspace_id=workspace_id)
    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        raise ModelError('模型端点、名称或密钥未配置')
    _governed_call(workspace_id)
    log_event("model.request.ready", "模型参数就绪，准备发送请求", profile=profile_name,
              model=cfg.get("model"), provider=cfg.get("provider"),
              message_count=len(messages), tool_count=len(tools or []))
    started = time.perf_counter()
    client = _client(cfg['base_url'], cfg['api_key']).with_options(
        timeout=_model_wait_timeout(request_timeout),
        max_retries=0 if control or max_retries is None else max_retries,
    )
    kwargs = dict(model=cfg['model'], messages=messages, temperature=temperature, stream=True)
    kwargs.update(_chat_protocol_options(cfg))
    if tools:
        kwargs.update(tools=tools, tool_choice='auto')
    content_parts: List[str] = []
    tool_calls: dict = {}
    usage = {}
    finish_reason = None
    stream = None
    first_chunk = True
    try:
        with _managed_chat_client(client, cancel_event):
            try:
                stream = _create_chat_completion(client, **kwargs)
                for chunk in stream:
                    if first_chunk:
                        log_event("model.stream.first_chunk", "已收到模型首个响应片段",
                                  duration_ms=round((time.perf_counter() - started) * 1000, 2))
                        first_chunk = False
                    _check_model_cancel(cancel_event)
                    if getattr(chunk, 'usage', None):
                        usage = _response_usage(chunk)
                    choices = getattr(chunk, 'choices', None)
                    if not choices:
                        continue
                    choice = choices[0]
                    reason = getattr(choice, 'finish_reason', None)
                    if reason:
                        finish_reason = reason
                    delta = getattr(choice, 'delta', None)
                    content = getattr(delta, 'content', None)
                    if content:
                        if not isinstance(content, str):
                            _incomplete_response('invalid_content')
                        content_parts.append(content)
                        if on_delta:
                            on_delta(content)
                    for tc in (getattr(delta, 'tool_calls', None) or []):
                        idx = getattr(tc, 'index', None)
                        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
                            _incomplete_response('invalid_tool_call')
                        slot = tool_calls.setdefault(idx, {'id': '', 'type': 'function', 'name': '', 'arguments': ''})
                        call_id = getattr(tc, 'id', None)
                        if call_id:
                            if not isinstance(call_id, str):
                                _incomplete_response('invalid_tool_call')
                            # Gateways may send the ID later, fragmented, or repeat it.
                            if call_id.startswith(slot['id']):
                                slot['id'] = call_id
                            elif call_id != slot['id']:
                                slot['id'] += call_id
                        call_type = getattr(tc, 'type', None)
                        if call_type:
                            slot['type'] = call_type
                        function = getattr(tc, 'function', None)
                        for field in ('name', 'arguments'):
                            piece = getattr(function, field, None)
                            if piece is not None:
                                if not isinstance(piece, str):
                                    _incomplete_response('invalid_tool_arguments' if field == 'arguments' else 'invalid_tool_call')
                                slot[field] += piece
                _check_model_cancel(cancel_event)
                content = ''.join(content_parts)
                final_tool_calls = [
                    {'id': slot['id'], 'type': slot['type'],
                     'function': {'name': slot['name'], 'arguments': slot['arguments']}}
                    for _, slot in sorted(tool_calls.items())
                ]
                _validate_chat_response(content, final_tool_calls, finish_reason, tools)
            finally:
                if stream is not None:
                    _close_model_stream(stream)
    except Exception as exc:
        _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                       capability='chat', usage=usage, started=started, status='failed',
                       usage_context=usage_context, error_code=getattr(exc, 'code', type(exc).__name__))
        _check_model_cancel(cancel_event)
        _raise_balance_error(exc)
        raise
    payload = {'content': content, 'tool_calls': final_tool_calls, 'usage': usage, 'finish_reason': finish_reason,
               'model': cfg['model'], 'provider': cfg.get('provider') or 'openai-compatible'}
    _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                   capability='chat', usage=usage, started=started, status='completed',
                   usage_context=usage_context)
    if on_done:
        on_done(payload)
    return payload


# 参数（原 docstring 见上）：`image_bytes` 为原始图片字节（转 data URL 后提交，不落盘）；
# `mime_type` 必须是 jpeg/png/webp/gif 之一，其他格式抛 ValueError；
# `context` 是文档位置提示（如"第 3 页"），最多取前 800 字符拼进提示词。
# 返回：{'content', 'model', 'provider', 'usage'}；content 为可检索的纯文本。
# 异常：VISION_ENABLED 关闭时 RuntimeError；端点/密钥/模型未配置时 RuntimeError；
# 模型返回空内容时 RuntimeError("识图模型未返回可用内容")。
# 约束：本函数不写文件、不改文档，只做"提取文本"这一件事，是否入库由知识库侧决定。
@observe("model.vision", "图片识别", fields={"profile": "profile_name", "workspace_id": "workspace_id", "run_id": "usage_context.run_id", "purpose": "usage_context.purpose"})
def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    *,
    context: str = "",
    workspace_id: Optional[str] = None,
    usage_context: Optional[dict] = None,
    profile_name: str = "vision",
) -> dict:
    """按选定视觉档位提取图像事实；配置、传输和用量归属保持同一档位。"""
    if not settings.VISION_ENABLED:
        raise RuntimeError("识图功能已停用")
    if not image_bytes:
        raise ValueError("识图输入不能为空")
    if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError(f"不支持的识图格式：{mime_type}")
    cfg = _profile_config(profile_name, workspace_id=workspace_id)
    if not cfg.get("base_url"):
        raise RuntimeError("识图模型端点未配置")
    if not cfg.get("api_key"):
        raise RuntimeError("识图模型密钥未配置")
    if not cfg.get("model"):
        raise RuntimeError("识图模型名称未配置")
    _governed_call(workspace_id)
    started = time.perf_counter()

    data_url = (
        f"data:{mime_type};base64,"
        + base64.b64encode(image_bytes).decode("ascii")
    )
    location_context = context.strip()[:800]
    prompt = (
        "你是企业知识库的识图与OCR引擎。请完整提取这张图片中可用于检索的信息。"
        "必须覆盖：1. 所有清晰可见的文字、数字、单位、日期和编号；"
        "2. 表格、图表的指标名称、数值、趋势和对比关系；"
        "3. 流程图、架构图、组织图中节点及连接关系；"
        "4. 截图中的系统名称、字段、状态和操作结果。"
        "只描述画面中确实存在的内容，不推测模糊信息。输出简洁的纯文本，不要使用Markdown表格。"
    )
    if location_context:
        prompt += f"\n文档位置上下文：{location_context}"
    from app.business_worker import transport as business_transport
    if business_transport.active():
        result = business_transport.generate(profile_name, [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url, "detail": settings.VISION_DETAIL}},
            {"type": "text", "text": prompt}]}], temperature=0,
            workspace_id=workspace_id, usage_context=usage_context, capability="vision",
            model_options={"maxTokens": settings.VISION_MAX_OUTPUT_TOKENS, "thinking": "disabled",
                           "reasoningEfforts": False})
        content = (result.get("content") or "").strip()
        if not content:
            raise RuntimeError("识图模型未返回可用内容")
        return {"content": content, "model": cfg["model"], "provider": cfg.get("provider") or "openai-compatible",
                "usage": result.get("usage") or {}}
    client = _client(cfg["base_url"], cfg["api_key"])
    try:
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": settings.VISION_DETAIL,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=settings.VISION_MAX_OUTPUT_TOKENS,
            extra_body={"enable_thinking": False},
        )
    except Exception as exc:
        _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                       capability="vision", usage=None, started=started, status="failed",
                       usage_context=usage_context, error_code=type(exc).__name__)
        raise
    message = response.choices[0].message
    content = (message.content or "").strip()
    if not content:
        raise RuntimeError("识图模型未返回可用内容")
    usage = {}
    if getattr(response, "usage", None):
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(response.usage, "completion_tokens", 0),
            "total_tokens": getattr(response.usage, "total_tokens", 0),
        }
    _persist_usage(workspace_id=workspace_id, profile_name=profile_name, cfg=cfg,
                   capability="vision", usage=usage, started=started, status="completed",
                   usage_context=usage_context)
    return {
        "content": content,
        "model": cfg["model"],
        "provider": cfg.get("provider") or "openai-compatible",
        "usage": usage,
    }


@observe("model.embedding", "文本向量生成", fields={"profile": "profile_name", "workspace_id": "workspace_id", "run_id": "usage_context.run_id", "purpose": "usage_context.purpose"})
def embed_texts(
    texts: List[str], workspace_id: Optional[str] = None,
    usage_context: Optional[dict] = None,
    *, profile_name: str = "embedding",
) -> List[List[float]]:
    """批量向量化文本，返回与入参顺序严格对应的向量列表。

    参数：`profile_name` 可指档位或具体模型名（解析规则见 `_embedding_config`）。
    返回：按 provider 返回的 `index` 排序后的向量，保证与 `texts` 下标一一对应——
    知识库靠这个顺序把向量与切片配对，顺序错位会造成检索串行。
    异常：端点/密钥/模型缺失抛 ModelError；供应商错误原样抛出（不分类为余额错误，
    向量化失败由知识库侧统一提示）。
    副作用：写用量记录（含 embedding_profile 归属）。
    """
    resolved_profile, cfg = _embedding_config(profile_name, workspace_id=workspace_id)
    if not cfg.get("base_url") or not cfg.get("api_key") or not cfg.get("model"):
        raise ModelError("向量模型端点、名称或密钥未配置")
    _governed_call(workspace_id)
    started = time.perf_counter()
    client = _client(cfg["base_url"], cfg["api_key"])
    try:
        resp = client.embeddings.create(model=cfg["model"], input=texts)
    except Exception as exc:
        _persist_usage(workspace_id=workspace_id, profile_name=resolved_profile, cfg=cfg,
                       capability="embedding", usage=None, started=started, status="failed",
                       usage_context={**(usage_context or {}), "embedding_profile": resolved_profile},
                       error_code=type(exc).__name__)
        raise
    response_usage = getattr(resp, "usage", None)
    usage = {"prompt_tokens": getattr(response_usage, "prompt_tokens", 0),
             "completion_tokens": 0,
             "total_tokens": getattr(response_usage, "total_tokens", 0)} if response_usage else {}
    _persist_usage(workspace_id=workspace_id, profile_name=resolved_profile, cfg=cfg,
                   capability="embedding", usage=usage, started=started, status="completed",
                   usage_context={**(usage_context or {}), "embedding_profile": resolved_profile})
    ordered = sorted(resp.data, key=lambda d: d.index)
    return [list(d.embedding) for d in ordered]


def embed_one(
    text: str, workspace_id: Optional[str] = None, *, profile_name: str = "embedding",
) -> List[float]:
    """单条文本向量化（检索时的查询向量）。

    注意：入参为空串时仍会发一次请求，返回空向量列表下标会 IndexError，
    调用方应保证 text 非空；如需批量请直接用 `embed_texts`。
    """
    out = embed_texts([text], workspace_id=workspace_id, profile_name=profile_name)
    return out[0]


# Aliases
# 兼容别名：历史上部分模块以 `model.chat(...)` 调用，等价于 chat_for_run。
chat = chat_for_run
