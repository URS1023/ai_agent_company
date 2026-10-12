"""Regression checks for cleanup after a Windows peer resets its connection.

中文说明：Windows 下对端重置连接后的资源清理回归。
隔离方式：事件循环 + monkeypatch + 子进程桩，不启动真实服务；平台相关用例在不支持时会跳过。
"""
import asyncio
import importlib.util
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "windows_asyncio", Path(__file__).resolve().parents[1] / "backend/app/core/windows_asyncio.py"
)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def reset_error(code=10054):
    error = ConnectionResetError("peer reset")
    error.winerror = code
    return error


def make_transport():
    sock = Mock()
    sock.fileno.return_value = 7
    server, protocol = Mock(), Mock()
    transport = SimpleNamespace(
        _called_connection_lost=False, _sock=sock, _server=server, _protocol=protocol
    )
    return transport, sock, server, protocol


@pytest.mark.parametrize("error", [None, reset_error()])
def test_close_and_detach_once_even_after_peer_reset(error):
    transport, sock, server, protocol = make_transport()
    sock.shutdown.side_effect = error
    compat._call_connection_lost(transport, None)
    compat._call_connection_lost(transport, None)
    protocol.connection_lost.assert_called_once_with(None)
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    sock.close.assert_called_once_with()
    server._detach.assert_called_once_with()
    assert transport._sock is None and transport._server is None
    assert transport._called_connection_lost


@pytest.mark.parametrize("error", [reset_error(10053), OSError("unexpected shutdown error")])
def test_other_socket_errors_propagate_but_still_release_resources(error):
    transport, sock, server, _ = make_transport()
    sock.shutdown.side_effect = error
    with pytest.raises(type(error)) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is error
    sock.close.assert_called_once_with()
    server._detach.assert_called_once_with()


@pytest.mark.parametrize("error", [reset_error(), RuntimeError("application protocol failed")])
def test_protocol_errors_are_not_suppressed(error):
    transport, sock, server, protocol = make_transport()
    protocol.connection_lost.side_effect = error
    sock.shutdown.side_effect = reset_error()
    with pytest.raises(type(error)) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is error
    sock.close.assert_called_once_with()
    server._detach.assert_called_once_with()


def test_already_closed_socket_without_server():
    transport, sock, _, _ = make_transport()
    sock.fileno.return_value = -1
    transport._server = None
    compat._call_connection_lost(transport, None)
    sock.shutdown.assert_not_called()
    sock.close.assert_called_once_with()


def test_non_windows_is_unchanged(monkeypatch):
    monkeypatch.setattr(compat.sys, "platform", "linux")
    assert compat.install_windows_socket_cleanup() is False


@pytest.mark.skipif(sys.platform != "win32" or sys.version_info[:2] != (3, 12), reason="Windows Python 3.12")
def test_installation_and_original_regression(monkeypatch):
    from asyncio import proactor_events

    base = proactor_events._ProactorBasePipeTransport._call_connection_lost
    transport, sock, server, _ = make_transport()
    sock.shutdown.side_effect = reset_error()
    with pytest.raises(ConnectionResetError):
        base(transport, None)
    sock.close.assert_not_called()
    server._detach.assert_not_called()
    cls = proactor_events._ProactorSocketTransport
    # Track the original class state, then exercise actual first installation.
    monkeypatch.setattr(cls, "_call_connection_lost", base)
    del cls._call_connection_lost
    assert compat.install_windows_socket_cleanup() is True
    assert compat.install_windows_socket_cleanup() is True
    assert proactor_events._ProactorBasePipeTransport._call_connection_lost is base
    cls._call_connection_lost(transport, None)
    sock.close.assert_called_once_with()
    server._detach.assert_called_once_with()


@pytest.mark.skipif(sys.platform != "win32" or sys.version_info[:2] != (3, 12), reason="Windows Python 3.12")
def test_install_preserves_external_override(monkeypatch):
    from asyncio import proactor_events

    custom = Mock()
    monkeypatch.setattr(proactor_events._ProactorSocketTransport, "_call_connection_lost", custom)
    assert compat.install_windows_socket_cleanup() is False
    assert proactor_events._ProactorSocketTransport._call_connection_lost is custom


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor subprocess")
def test_async_subprocess_still_works(monkeypatch):
    from asyncio import proactor_events

    cls = proactor_events._ProactorSocketTransport
    monkeypatch.setattr(cls, "_call_connection_lost", cls._call_connection_lost)
    del cls._call_connection_lost
    compat.install_windows_socket_cleanup()
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print('subprocess-ok')", stdout=asyncio.subprocess.PIPE
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        assert process.returncode == 0 and output.strip() == b"subprocess-ok"
    asyncio.run(run())
