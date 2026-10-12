# -*- coding: utf-8 -*-
"""Safe JSON-only HTTP tool provider with pinned DNS and credential redaction.

HTTP 工具 provider：把连接器里保存的"固定请求"（base_url + path + method +
body_mapping）与本次入参拼装成一个受限 HTTP 请求，只解析 JSON 响应。

安全约束（改动前必读，实际校验在 modules/collection/transport.py）：
- 必须先 ``ctx["allow_network"]`` 为真，否则直接拒绝——沙箱/无网执行上下文
  一律不允许发起外呼。
- 地址固定：base_url 与 path 来自已发布的连接器版本，用户入参只能作为查询串或
  JSON 请求体，不能改写 host/路径；``validate_url`` 在解析域名之前就拒绝
  控制字符、非 http(s) 协议、内嵌用户名密码、片段、变量占位符与元数据域名。
- SSRF/私网：``allow_private`` 默认关闭，transport 的 resolve_destination 会
  在连接前逐个解析并拒绝私网/回环/链路本地地址；重定向一律不跟随
  （结果 metadata 固定写 redirectsFollowed=0）。
- 上限：单条响应 4 MB、请求体 1 MB、请求头 40 项/16 KB；超时取
  min(120, max(1, spec.timeout_seconds 或连接器 timeout_seconds 或 30)) 秒。
- 凭据只以 ``${secret.名称}`` 引用写入头部/请求体，明文会被 validate_headers /
  validate_body_secrets 拒绝；响应中的凭据以脱敏占位符回填。
- 只支持 GET/POST 与 json/query 两种请求体映射，历史配置不支持则要求重建版本。
"""
from __future__ import annotations

import base64
import time
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from ...errors import ToolExecutionError, ValidationError
from ...modules.collection.transport import request_json, validate_body_secrets, validate_headers, validate_url
from .base import ToolResult
from .side_effects import exception_is_uncertain, idempotency_key, uncertain_result


def _json_value(value):
    """把驱动返回值递归转换为可 JSON 序列化的结构（Decimal 按精度选择 int/float）。"""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _query(arguments: dict) -> str:
    """把入参编码为查询串；只允许标量，且拒绝原型污染键名。

    嵌套 dict/list 一律拒绝而不是序列化，避免把结构化业务参数误塞进 URL 后被
    日志或网关缓存记录；布尔值统一小写（true/false）以匹配多数 REST 服务约定。
    """
    values = {}
    for key, value in arguments.items():
        if not isinstance(key, str) or not key or key in {"__proto__", "prototype", "constructor"}:
            raise ValidationError("HTTP 查询参数名不正确")
        if isinstance(value, (dict, list)):
            raise ValidationError("HTTP 查询参数只允许字符串、数字、布尔值或空值")
        values[key] = "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
    return urlencode(values)


def _headers(conn: dict, secrets: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """构造请求头并返回需要脱敏的值，供响应回填时替换。

    认证模板采用 ``${secret.token}`` 占位式写法：由网关在此展开为明文；
    basic 认证直接生成 Base64 串，因此额外把该串登记到 redactions，
    防止服务端回显 Authorization 时把凭据带入日志或模型上下文。
    返回 (headers, redactions)，本函数不发起网络请求。
    """
    headers = dict(conn.get("headers") or {})
    validate_headers(headers)
    auth = conn.get("auth") or {}
    auth_type = auth.get("type", "none")
    secret_id = auth.get("secret_id")
    secret = secrets.get(secret_id, "") if secret_id else ""
    redactions = {}
    if auth_type == "bearer":
        headers["Authorization"] = "Bearer ${secret.token}"
    elif auth_type == "header":
        headers[auth.get("header_name") or "X-API-Key"] = "${secret.token}"
    elif auth_type == "basic":
        encoded = base64.b64encode((str(auth.get("username") or "") + ":" + secret).encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + encoded
        redactions["basicAuthorization"] = encoded
    if auth_type != "basic":
        validate_headers(headers)
    return headers, redactions


def invoke(spec, args: dict, ctx: dict) -> ToolResult:
    """执行一次 HTTP 工具调用并返回 JSON 结果。

    参数：``spec`` 为工具声明（提供 timeout_seconds），``args`` 为模型入参
    （GET/query 模式作为查询串，POST/json 模式整体作为请求体），``ctx`` 需含
    ``connector_spec``（固定请求定义）、``allow_network`` 与网关注入的
    ``secret_values``。
    返回：status=success 的 ToolResult，data 为解析后的 JSON，metadata 含
    响应摘要与 redirectsFollowed=0。
    异常：无网权限抛 ToolExecutionError；地址/头部/请求体不合规抛 ValidationError；
    网络、超时、非 2xx、响应超限等由 transport 抛业务异常（不返回明文凭据）。
    副作用：对外最多发起一次 HTTP 请求。POST 请求带稳定的 ``Idempotency-Key``；
    请求已发出但回执不明时返回 ``tool_result_unconfirmed``，上层不得自动重做。
    """
    if not ctx.get("allow_network"):
        raise ToolExecutionError("当前执行上下文未授权访问外部网络")
    conn = ctx.get("connector_spec") or {}
    base = str(conn.get("base_url") or "").rstrip("/")
    path = str(conn.get("path") or "/")
    method = str(conn.get("method") or "POST").upper()
    body_mode = str(conn.get("body_mapping") or "json")
    if method not in {"GET", "POST"} or body_mode not in {"json", "query"}:
        raise ValidationError("已保存的 HTTP 配置不再受支持，请创建新版本")
    url = base + path
    query = _query(args) if method == "GET" or body_mode == "query" else ""
    if query:
        url += ("&" if "?" in url else "?") + query
    validate_url(url)
    credentials = dict(ctx.get("secret_values") or {})
    headers, extra_redactions = _headers(conn, credentials)
    call_key = idempotency_key(ctx, spec, args)
    if method == "POST":
        # 运行期键覆盖配置中的同名头，防止管理员复制连接器后意外复用旧键。
        headers = {name: value for name, value in headers.items() if name.lower() != "idempotency-key"}
        headers["Idempotency-Key"] = call_key
    body = args if method == "POST" and body_mode == "json" else None
    if body is not None:
        validate_body_secrets(body)
    timeout = min(120, max(1, int(spec.timeout_seconds or conn.get("timeout_seconds") or 30)))
    config = SimpleNamespace(
        url=url,
        method=method,
        headers=headers,
        allow_private=bool(conn.get("allow_private", False)),
        timeout_seconds=timeout,
    )
    try:
        data, metadata = request_json(
            config, body, credentials,
            deadline=time.monotonic() + timeout,
            response_redactions=extra_redactions,
            before_request=(lambda: ctx["_before_tool_dispatch"](provider="http", external_side_effect=method == "POST"))
                if callable(ctx.get("_before_tool_dispatch")) else None,
        )
    except (ToolExecutionError, ValidationError) as exc:
        # 断连、超时、5xx 或响应解析失败都可能发生在远端已经执行之后；
        # 仅凭客户端异常不能安全重放 POST，因此把状态明确标成未知。
        status = (getattr(exc, "extra", {}) or {}).get("http_status")
        uncertain = method == "POST" and (exception_is_uncertain(exc) or (isinstance(status, int) and status >= 500))
        if uncertain:
            return ToolResult(**uncertain_result(
                key=call_key,
                provider="http",
                error_summary="HTTP 请求可能已送达，但回执未确认；请按幂等键核实，系统不会自动重做。",
            ))
        raise
    return ToolResult(
        status="success",
        data=_json_value(data),
        metadata={"tool": spec.name, **metadata, "redirectsFollowed": 0,
                  **({"operationOutcome": "confirmed", "idempotencyKey": call_key} if method == "POST" else {})},
    )
