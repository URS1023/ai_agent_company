# -*- coding: utf-8 -*-
"""MCP streamable-HTTP provider; arbitrary web-configured stdio commands are disabled.

MCP 工具 provider：通过 streamable-HTTP 连接外部 MCP 服务，发现工具（fetch_tools）
或调用指定工具（invoke）。

安全约束：
- 只允许 http / streamable_http 传输；网页里配置的 stdio 命令一律拒绝，
  避免把"网页配置"变成任意本机命令执行入口。
- 与 HTTP provider 同源的校验：必须先有 ``allow_network``；``validate_url``
  拒绝含糊地址；连接前用 ``resolve_destination`` 逐个解析地址（默认拒绝私网/回环，
  仅 allow_private 显式开启时放行）；httpx 客户端 trust_env=False、
  follow_redirects=False，避免走系统代理或被重定向到内网。
- 认证头部只接受 ``${secret.名称}`` 引用（网关解密后注入 secret_values），
  响应内容经 redact_values 脱敏后才进入模型上下文。
- 每次调用都新建并关闭连接（asyncio.run 包裹），不做跨请求连接复用；
  超时取 min(120, max(1, 连接器 timeout_seconds 或 30)) 秒。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List

from ...errors import ToolExecutionError, ValidationError
from ...modules.collection.transport import redact_values, resolve_destination, validate_headers, validate_url
from .base import ToolSpec, ToolResult
from .side_effects import idempotency_key, uncertain_result

logger = logging.getLogger("mcp")


def _spec_from_mcp_tool(item: dict, tool_id: str = None) -> ToolSpec:
    """把 MCP 服务返回的工具描述转成内部 ToolSpec（provider 固定为 mcp）。

    权限字段在此留空：MCP 工具的执行权限由连接器资源与 ``connectors.execute``
    共同约束（见 gateway 的 require_resource），不能由远端服务自述声明。
    """
    return ToolSpec(
        name=item.get("name", ""),
        description=item.get("description") or "",
        provider="mcp",
        input_schema=item.get("inputSchema") or item.get("input_schema") or {"type": "object", "properties": {}},
        output_schema={},
        timeout_seconds=120,
        tool_id=tool_id,
    )


def _request_headers(conn_spec: dict, execution_context: dict, call_key: str | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """构造 MCP 请求头，并把解密后的凭据值登记为脱敏集合。

    与 HTTP provider 的差异：MCP 客户端直接使用明文头部（不做 ${secret.x} 二次展开），
    因此这里只允许凭据来自网关注入的 ``secret_values``，且返回的第二个字典用于
    对响应做 redact_values 脱敏。本函数不发起网络请求。
    """
    headers = dict(conn_spec.get("headers") or {})
    validate_headers(headers)
    auth = conn_spec.get("auth") or {}
    secret_id = auth.get("secret_id")
    secrets = execution_context.get("secret_values") or {}
    secret = secrets.get(secret_id, "") if secret_id else ""
    auth_type = auth.get("type", "none")
    if auth_type == "bearer":
        headers["Authorization"] = "Bearer " + secret
    elif auth_type == "header":
        headers[auth.get("header_name") or "X-API-Key"] = secret
    elif auth_type == "basic":
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(
            (str(auth.get("username") or "") + ":" + secret).encode("utf-8")
        ).decode("ascii")
    if call_key:
        # MCP 一次调用可能触发远端写操作，统一通过请求头传递稳定幂等键。
        headers = {name: value for name, value in headers.items() if name.lower() != "idempotency-key"}
        headers["Idempotency-Key"] = call_key
    return headers, {key: value for key, value in secrets.items() if value}


async def _connect(conn_spec: dict, execution_context: dict, call_key: str | None = None):
    """建立 MCP 会话，返回 (client, session, http_client, redactions) 四元组。

    调用方必须在 finally 中调用 ``_close``：MCP 客户端使用异步上下文管理器链
    （httpx → streamable_http → ClientSession），任一环失败都要按相反顺序退出，
    否则会残留未关闭的连接与后台任务。
    异常：无网权限抛 ToolExecutionError；传输方式/地址/请求头不合规抛
    ValidationError；依赖缺失（mcp/httpx2 未安装）也会转成中文业务提示。
    """
    if not execution_context.get("allow_network"):
        raise ToolExecutionError("当前执行上下文未授权访问外部网络")
    transport = str(conn_spec.get("transport") or "streamable_http")
    if transport not in {"http", "streamable_http"}:
        raise ValidationError("网页配置的 MCP 不允许启动任意本机命令")
    url = str(conn_spec.get("url") or "")
    validate_url(url)
    timeout = min(120, max(1, int(conn_spec.get("timeout_seconds") or 30)))
    # Validate every resolved address immediately before the MCP client connects.
    # The client is also configured without proxies or redirects.
    resolve_destination(
        url, allow_private=bool(conn_spec.get("allow_private", False)),
        deadline=time.monotonic() + timeout,
    )
    headers, redactions = _request_headers(conn_spec, execution_context, call_key)
    try:
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise ToolExecutionError("MCP 运行依赖未安装，请执行项目依赖安装") from exc
    http_client = httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(timeout, read=timeout),
        follow_redirects=False,
        trust_env=False,
    )
    client = session = None
    try:
        await http_client.__aenter__()
        client = streamable_http_client(url, http_client=http_client, terminate_on_close=True)
        streams = await client.__aenter__()
        session = await ClientSession(streams[0], streams[1]).__aenter__()
        await session.initialize()
        return client, session, http_client, redactions
    except Exception:
        await _close(client, session, http_client)
        raise


async def _close(client, session, http_client):
    """按 session → client → http_client 的顺序关闭，失败只记日志不覆盖主回执。"""
    # 关闭阶段异常不能覆盖 call_tool 已经得到的成功或 uncertain 回执；
    # 每个资源仍独立尝试，以免一个 SDK 清理错误泄漏后续连接。
    for resource, label in ((session, "session"), (client, "client"), (http_client, "http_client")):
        if resource is None:
            continue
        try:
            await resource.__aexit__(None, None, None)
        except Exception as exc:
            logger.warning("MCP %s 关闭失败：%s", label, type(exc).__name__)


async def _list_tools(conn_spec: dict, execution_context: dict) -> List[ToolSpec]:
    """连上 MCP 服务并拉取工具清单（连接器"发现工具"用）。

    已识别的业务异常（ValidationError / ToolExecutionError）原样上抛以保留
    中文原因；其余异常统一折叠为"MCP 连接或工具发现失败"并带上异常类型名，
    不把远端堆栈暴露给调用方。finally 保证连接被关闭。
    """
    client = session = http_client = None
    try:
        client, session, http_client, _redactions = await _connect(conn_spec, execution_context)
        response = await session.list_tools()
        specs = []
        for item in getattr(response, "tools", []):
            data = item.model_dump() if hasattr(item, "model_dump") else {
                "name": item.name,
                "description": getattr(item, "description", ""),
                "inputSchema": getattr(item, "inputSchema", {"type": "object", "properties": {}}),
            }
            specs.append(_spec_from_mcp_tool(data))
        return specs
    except (ValidationError, ToolExecutionError):
        raise
    except Exception as exc:
        raise ToolExecutionError("MCP 连接或工具发现失败", error_type=type(exc).__name__) from exc
    finally:
        await _close(client, session, http_client)


async def _call_tool(conn_spec: dict, tool_spec: ToolSpec, args: dict, execution_context: dict) -> ToolResult:
    """调用一个 MCP 工具并把返回内容转成 ToolResult。

    远端以 ``isError=True`` 表示工具级失败：此时仍返回结果（status=failed +
    error_code=mcp_tool_error）而不是抛异常，让模型看到失败原因后决定是否重试。
    返回内容的文本/结构化片段统一经 redact_values 脱敏，metadata 记录传输方式、
    是否报错以及 redirectsFollowed=0（客户端本身不跟随重定向）。
    """
    client = session = http_client = None
    call_started = False
    call_key = idempotency_key(execution_context, tool_spec, args)
    try:
        client, session, http_client, redactions = await _connect(conn_spec, execution_context, call_key)
        if callable(execution_context.get("_before_tool_dispatch")):
            # 初始化握手、权限及目标预检完成后，业务 call_tool 发送前提交派发意图。
            execution_context["_before_tool_dispatch"](provider="mcp", external_side_effect=True)
        call_started = True
        response = await session.call_tool(name=tool_spec.name, arguments=args)
        parts = []
        for content in getattr(response, "content", []):
            if getattr(content, "type", "") == "text":
                parts.append(getattr(content, "text", ""))
            elif hasattr(content, "model_dump"):
                parts.append(content.model_dump())
        data = redact_values(parts, redactions)
        failed = bool(getattr(response, "isError", False))
        return ToolResult(
            status="failed" if failed else "success",
            data=data,
            metadata={"isError": failed, "transport": "streamable_http", "redirectsFollowed": 0,
                      "operationOutcome": "rejected" if failed else "confirmed",
                      "idempotencyKey": call_key, "retryable": False if failed else True},
            error_code="mcp_tool_error" if failed else None,
            error_summary="MCP 服务返回工具错误" if failed else None,
        )
    except (ValidationError, ToolExecutionError) as exc:
        if call_started:
            return ToolResult(**uncertain_result(
                key=call_key,
                provider="mcp",
                error_summary="MCP 工具请求可能已送达，但回执未确认；请按幂等键核实，系统不会自动重做。",
            ))
        raise
    except Exception as exc:
        if call_started:
            return ToolResult(**uncertain_result(
                key=call_key,
                provider="mcp",
                error_summary="MCP 工具请求可能已送达，但回执未确认；请按幂等键核实，系统不会自动重做。",
            ))
        raise ToolExecutionError("MCP 工具调用失败", error_type=type(exc).__name__) from exc
    finally:
        await _close(client, session, http_client)


def fetch_tools(connector_spec: dict, execution_context: dict | None = None) -> List[ToolSpec]:
    """同步入口：供连接器配置页保存前"发现工具"调用，内部以 asyncio.run 执行。

    注意：本函数在事件循环内部（异步请求处理中）调用会报错，因此只能在同步代码路径
    （如 FastAPI 的线程池端点）中使用；不使用 async 端点。
    """
    return asyncio.run(_list_tools(connector_spec, execution_context or {}))


def invoke(spec: ToolSpec, args: dict, ctx: dict) -> ToolResult:
    """同步入口：网关分发给 mcp provider 时调用，连接器定义取自 ctx["connector_spec"]。"""
    return asyncio.run(_call_tool(ctx.get("connector_spec") or {}, spec, args, ctx))
