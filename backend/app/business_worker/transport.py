"""业务模块的模型调用只委托 DSH；不创建另一套模型客户端。"""
from __future__ import annotations

_rpc = None


def install(rpc):
    """Worker 启动安装传输，后台调度与 API 使用同一个宿主。"""
    global _rpc
    _rpc = rpc


def active():
    return _rpc is not None


def progress(run_id, event, data):
    """把已有业务进度交宿主排序；通知不阻塞持有事务的业务步骤。"""
    if _rpc is not None:
        _rpc.notify("business.event", {"run_id": run_id, "event": event, "data": data})


def execute_chat(prepared):
    """插件试运行交给同一 DSH 宿主，禁止启动旧 Python 对话引擎。"""
    return _rpc.request("chat.execute", {"prepared": prepared})


def generate(profile, messages, tools=None, temperature=.2, *, workspace_id=None,
             usage_context=None, response_format=None, request_timeout=None, capability="chat",
             model_options=None, cancel_event=None, **kwargs):
    """保持旧业务服务的返回合同，模型参数由统一映射生成。"""
    from app.providers.model import _profile_config, _chat_profile
    from app.modules.runtime.task_control import current, remaining_timeout
    from .models import model_configuration
    control = current.get()
    if control:
        control.check()
        workspace_id = workspace_id or getattr(control, "model_workspace_id", None)
    profile = _chat_profile(profile, workspace_id)
    config = _profile_config(profile, workspace_id)
    model = {**model_configuration(config), **(model_options or {})}
    if model.get("thinking") == "disabled":
        if model.get("protocol") == "deepseek-messages":
            model["reasoningEffort"] = "off"
        else:
            model.pop("reasoningEffort", None)
            model["reasoningEfforts"] = False
    if profile == "vision":
        model.setdefault("inputModalities", ["text", "image"])
    run_id = (usage_context or {}).get("run_id") or getattr(control, "business_run_id", None)
    from .context import request_actor
    actor = request_actor.get() or {}
    usage_context = dict(usage_context or {})
    if actor.get("workspace_id") == workspace_id and actor.get("user_id"):
        usage_context["actor_user_id"] = actor["user_id"]
    payload = {"run_id": run_id, "profile": profile, "workspace_id": workspace_id,
               "messages": messages, "tools": tools, "temperature": temperature,
               "response_format": response_format, "request_timeout": request_timeout,
               "purpose": (usage_context or {}).get("purpose") or "business", "model": model,
               "capability": capability, "usage_context": usage_context}
    from .protocol import RemoteError
    try:
        result = _rpc.request("model.generate", payload, timeout=remaining_timeout(request_timeout),
                              cancel=cancel_event if cancel_event is not None else control.cancelled if control else None)
    except RemoteError as exc:
        from app.errors import ModelError
        error = ModelError(str(exc))
        error.code = {"QUOTA": "model_balance_insufficient", "RATE_LIMIT": "model_rate_limited",
                      "AUTH": "model_authentication_failed", "SERVER": "model_unavailable",
                      "CONTEXT_WINDOW_EXCEEDED": "model_context_exceeded"}.get(exc.code, "model_unavailable")
        raise error from exc
    if control:
        control.check()
    if not isinstance(result, dict):
        from app.errors import ModelError
        raise ModelError("DSH 模型返回格式无效")
    from app.providers.model import _validate_chat_response
    _validate_chat_response(result.get("content") or "", result.get("tool_calls") or [], result.get("finish_reason"), tools)
    return {**result, "model": result.get("model") or config.get("model"),
            "provider": result.get("provider") or config.get("provider") or "openai-compatible"}
