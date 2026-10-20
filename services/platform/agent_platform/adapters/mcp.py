"""MCP Streamable HTTP 有界握手；参照原生客户端会话顺序，不执行工具。

协议顺序依据Dify core/mcp/session/client_session.py 与
core/mcp/client/streamable_client.py：initialize → initialized → 关闭独立会话。
保留JSON/SSE两种响应边界，不启动stdio子进程或回退到任意本地命令。
"""

import json
from typing import Any

import httpx

from agent_platform.core.errors import DomainError

from .native import NativeHTTP


def _rpc_response(content: bytes, content_type: str) -> dict[str, Any]:
    try:
        if "text/event-stream" in content_type:
            messages = []
            for block in content.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                payload = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                if payload:
                    messages.append(json.loads(payload))
            value = next((item for item in messages if item.get("id") == 1), None)
        else:
            value = json.loads(content)
        if not isinstance(value, dict) or value.get("id") != 1 or value.get("error") or not value.get("result", {}).get("protocolVersion"):
            raise ValueError("invalid handshake")
        return value["result"]
    except (ValueError, AttributeError, TypeError) as exc:
        raise DomainError(502, "mcp_initialize_failed", "MCP 服务未返回有效初始化响应") from exc


def check_mcp(client: NativeHTTP) -> dict[str, Any]:
    """仅一次初始化及通知，最大1MB响应；有会话则显式DELETE释放。"""
    session_headers: dict[str, str] = {}
    session_closed: bool | None = None
    result: dict[str, Any] = {}
    try:
        with client._client() as http:
            try:
                with http.stream("POST", client.base_url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "manufacturing-agent-platform", "version": "0.1.0"}}}, headers={"Accept": "application/json, text/event-stream"}) as response:
                    client._check(response)
                    session_id = response.headers.get("mcp-session-id")
                    if session_id:
                        session_headers["Mcp-Session-Id"] = session_id
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_000_000:
                            raise DomainError(502, "mcp_response_too_large", "MCP 初始化响应超过大小限制")
                    result = _rpc_response(bytes(content), response.headers.get("content-type", ""))
                session_headers["MCP-Protocol-Version"] = result["protocolVersion"]
                initialized = http.post(client.base_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={**session_headers, "Accept": "application/json, text/event-stream"})
                client._check(initialized)
            finally:
                if session_headers.get("Mcp-Session-Id"):
                    try:
                        closed = http.delete(client.base_url, headers=session_headers)
                        # 有些无状态实现不支持DELETE；明确记录未确认清理，不能假称成功释放。
                        session_closed = 200 <= closed.status_code < 300 or closed.status_code == 404
                    except httpx.RequestError:
                        session_closed = False
    except httpx.TimeoutException as exc:
        raise DomainError(504, "mcp_timeout", "MCP 握手超时") from exc
    except httpx.RequestError as exc:
        raise DomainError(503, "mcp_unavailable", "无法连接 MCP 服务") from exc
    return {"status": "initialized", "result": result, "session_closed": session_closed, "executed": False}
