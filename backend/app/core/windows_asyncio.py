"""Process-local socket cleanup compatibility for Windows CPython 3.12.

The bundled asyncio transport calls shutdown() before close() without guarding
WSAECONNRESET. A peer reset can therefore skip both close and server._detach.
Keep this shim limited to the runtime and socket transport verified by tests;
reassess it when upgrading Python. No standard-library files are modified.
Upstream: https://github.com/python/cpython/blob/3.12/Lib/asyncio/proactor_events.py

职责：在 Windows + CPython 3.12 上打补丁修复 asyncio 的 TCP 连接清理缺陷，
避免对端 RST 后套接字与 `server._detach` 双双漏掉。

系统位置：由 app.main 在模块导入期（Uvicorn 接受连接之前）调用 install_windows_socket_cleanup；
这是全进程唯一一处猴子补丁，其他模块不应再改 asyncio 传输类。

关键约束：
- 只在 win32 / CPython 3.12 生效，其他平台或版本直接返回 False，不做任何改动；
- 只替换 `_ProactorSocketTransport._call_connection_lost`，保留 Proactor 子进程能力
  （不要顺手替换整个事件循环策略）；
- 若该属性已被其他库覆盖，则放弃打补丁并记 warning，绝不覆盖他人实现；
- 升级 Python 版本后应重新评估此兼容层是否仍然必要。
"""
from __future__ import annotations

import logging
import socket
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _call_connection_lost(transport: Any, exc: Exception | None) -> None:
    """替换版连接清理：保证 socket 关闭与 server._detach 一定被执行。

    参数：transport 为 asyncio 的 Proactor socket 传输，exc 为导致断开的原因（可为 None）。
    副作用：关闭底层套接字、从 server 摘除传输；并置位 `_called_connection_lost` 防重复清理。
    异常：协议回调里的异常照常向上抛出；仅在 shutdown 阶段吞掉 WSAECONNRESET(10054)。
    """
    if transport._called_connection_lost:
        return
    try:
        # Protocol failures must still propagate, including ConnectionResetError.
        transport._protocol.connection_lost(exc)
    finally:
        try:
            if transport._sock.fileno() != -1:
                try:
                    transport._sock.shutdown(socket.SHUT_RDWR)
                except ConnectionResetError as error:
                    # 10054 = 对端已重置：此时 shutdown 失败属于预期，继续走清理而不是中断。
                    if getattr(error, "winerror", None) != 10054:
                        raise
                    logger.debug("Peer reset during socket shutdown; completing cleanup")
        finally:
            # 无论 shutdown 是否成功都必须 close/detach，否则连接对象泄漏在事件循环里。
            transport._sock.close()
            transport._sock = None
            server = transport._server
            if server is not None:
                server._detach()
                transport._server = None
            transport._called_connection_lost = True


def install_windows_socket_cleanup() -> bool:
    """Patch only TCP socket cleanup, retaining Proactor subprocess support.

    返回：True 表示补丁已就位（或无需补丁），False 表示环境不匹配或已被其他库定制。
    约束：幂等——重复调用不会重复包装；调用时机必须在事件循环建立连接之前。
    """
    if (sys.platform != "win32" or sys.implementation.name != "cpython"
            or sys.version_info[:2] != (3, 12)):
        return False
    from asyncio import proactor_events

    transport_class = proactor_events._ProactorSocketTransport
    if transport_class._call_connection_lost is _call_connection_lost:
        return True
    # Do not overwrite another library's explicit socket-transport override.
    if "_call_connection_lost" in transport_class.__dict__:
        logger.warning("Windows socket cleanup shim skipped: transport already customized")
        return False
    transport_class._call_connection_lost = _call_connection_lost
    return True
