"""隔离守卫的内部配对回归：受限端口只用纯桩，不连接在用服务或数据库。"""
from collections import deque
import os
import inspect
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch


GUARD = Path(__file__).parent / "python_guard" / "sitecustomize.py"


class SocketFixture:
    """用假套接字执行真实标准库 fallback，保留握手和异常清理控制流。"""

    def __init__(self, ports, *, connection_error=None, native=False, isolation=True):
        self.ports = deque(ports)
        self.sockets = []
        self.connections = []
        fixture = self

        class FixtureSocket:
            def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
                self.family, self.type, self.proto = family, type, proto
                self.bound = self.peer = self.pending = None
                self.close_count = 0
                fixture.sockets.append(self)

            def bind(self, address):
                self.bound = (address[0], fixture.ports.popleft() if address[1] == 0 else address[1])

            def listen(self):
                """假监听器不打开系统端口。"""

            def getsockname(self):
                return self.bound

            def getpeername(self):
                return self.peer

            def setblocking(self, value):
                """保留标准库接口，不启动线程或真实 I/O。"""

            def connect(self, address):
                fixture.connections.append(address)
                if connection_error is not None:
                    raise connection_error
                self.peer = address
                self.bound = (address[0], 62001)
                listener = next(item for item in fixture.sockets if item.bound == address)
                listener.pending = self

            def connect_ex(self, address):
                return self.connect(address) or 0

            def accept(self):
                accepted = FixtureSocket(self.family, self.type, self.proto)
                accepted.bound, accepted.peer = self.bound, self.pending.bound
                return accepted, self.pending.bound

            def close(self):
                self.close_count += 1

        self.module = types.ModuleType("socket")
        self.module.socket = FixtureSocket
        self.module.AF_INET = socket.AF_INET
        self.module.AF_INET6 = socket.AF_INET6
        self.module.SOCK_STREAM = socket.SOCK_STREAM
        # 复制函数环境而非握手实现，避免测试把产品式伪逻辑当成标准库行为。
        original = socket._fallback_socketpair
        self.fallback = types.FunctionType(
            original.__code__, {**original.__globals__, "socket": FixtureSocket},
            original.__name__, original.__defaults__, original.__closure__,
        )
        self.module._fallback_socketpair = self.fallback
        self.original_pair = Mock(name="native_socketpair") if native else self.fallback
        self.module.socketpair = self.original_pair
        self.original_connect = FixtureSocket.connect
        self.mysql = types.ModuleType("pymysql")
        self.postgres = types.ModuleType("psycopg")
        self.mysql.connect = self.mysql.Connection = Mock(name="mysql_connect")
        self.postgres.connect = Mock(name="postgres_connect")
        with (patch.dict(sys.modules, {"socket": self.module, "pymysql": self.mysql, "psycopg": self.postgres}),
              patch.dict(os.environ, {"DSH_TEST_ISOLATION": "1" if isolation else "0"})):
            self.guard = runpy.run_path(str(GUARD))

    def assert_closed(self, testcase):
        testcase.assertTrue(self.sockets)
        testcase.assertEqual([item.close_count for item in self.sockets], [1] * len(self.sockets))


class PythonSocketpairGuardTests(unittest.TestCase):
    def test_internal_restricted_port_reallocates_after_closing_both_sockets(self):
        fixture = SocketFixture([8011, 55001])
        pair = fixture.module.socketpair()
        try:
            self.assertEqual(fixture.connections, [("127.0.0.1", 55001)])
            self.assertEqual(len(fixture.sockets), 5)
            self.assertEqual([item.close_count for item in fixture.sockets[:2]], [1, 1])
            self.assertEqual(pair[0].getpeername(), pair[1].getsockname())
            self.assertEqual(pair[1].getpeername(), pair[0].getsockname())
        finally:
            for item in pair:
                item.close()
        fixture.assert_closed(self)

    def test_direct_reserved_ports_stay_blocked_without_low_level_calls(self):
        for port in (9, 8011, 8012):
            for method in ("connect", "connect_ex"):
                with self.subTest(port=port, method=method):
                    fixture = SocketFixture([])
                    client = fixture.module.socket()
                    try:
                        with self.assertRaises(fixture.guard["ProtectedLoopbackPort"]):
                            getattr(client, method)(("127.0.0.1", port))
                        self.assertEqual(fixture.connections, [])
                        self.assertEqual(len(fixture.sockets), 1)
                    finally:
                        client.close()
                    fixture.assert_closed(self)

    def test_external_and_non_numeric_targets_stay_blocked(self):
        for address in (("192.0.2.1", 8011), ("203.0.113.9", 443), ("example.invalid", 443), "invalid"):
            for method in ("connect", "connect_ex"):
                with self.subTest(address=address, method=method):
                    fixture = SocketFixture([])
                    client = fixture.module.socket()
                    try:
                        with self.assertRaises((AssertionError, ValueError)) as raised:
                            getattr(client, method)(address)
                        self.assertNotIsInstance(raised.exception, fixture.guard["ProtectedLoopbackPort"])
                        self.assertEqual(fixture.connections, [])
                    finally:
                        client.close()
                    fixture.assert_closed(self)

    def test_exhaustion_stops_after_32_pairs_and_closes_every_socket(self):
        fixture = SocketFixture([8012] * 33)
        with self.assertRaises(fixture.guard["ProtectedLoopbackPort"]):
            fixture.module.socketpair()
        self.assertEqual(len(fixture.sockets), 64)
        self.assertEqual(list(fixture.ports), [8012])
        self.assertEqual(fixture.connections, [])
        fixture.assert_closed(self)

    def test_other_errors_are_not_retried_even_with_matching_message(self):
        for error in (AssertionError("DSH 验收禁止连接保留的本机端口"), OSError("假连接失败")):
            with self.subTest(error=type(error).__name__):
                fixture = SocketFixture([55001, 55002], connection_error=error)
                with self.assertRaises(type(error)) as raised:
                    fixture.module.socketpair()
                self.assertIs(raised.exception, error)
                self.assertEqual(fixture.connections, [("127.0.0.1", 55001)])
                self.assertEqual(list(fixture.ports), [55002])
                fixture.assert_closed(self)

    def test_fallback_signature_argument_validation_and_ipv6_are_preserved(self):
        fixture = SocketFixture([8012, 55002])
        self.assertEqual(inspect.signature(fixture.module.socketpair), inspect.signature(fixture.fallback))
        for options in ({"family": None}, {"type": socket.SOCK_DGRAM}, {"proto": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                fixture.module.socketpair(**options)
        self.assertEqual(fixture.sockets, [])
        pair = fixture.module.socketpair(family=socket.AF_INET6)
        try:
            self.assertEqual(fixture.connections, [("::1", 55002)])
        finally:
            for item in pair:
                item.close()
        fixture.assert_closed(self)

    def test_native_pair_and_disabled_isolation_are_not_wrapped(self):
        native = SocketFixture([], native=True)
        self.assertIs(native.module.socketpair, native.original_pair)
        native.module.socketpair()
        native.original_pair.assert_called_once_with()
        disabled = SocketFixture([], isolation=False)
        self.assertIs(disabled.module.socketpair, disabled.fallback)
        self.assertIs(disabled.module.socket.connect, disabled.original_connect)

    def test_database_entrypoints_remain_blocked(self):
        fixture = SocketFixture([])
        for method in (fixture.mysql.connect, fixture.mysql.Connection, fixture.postgres.connect):
            with self.assertRaisesRegex(AssertionError, "禁止 MySQL/PostgreSQL"):
                method()
        self.assertEqual(fixture.sockets, [])

    def test_real_child_process_socketpair_and_asyncio_startup(self):
        """唯一真实连接仅为标准库自建回环配对，不启动应用、业务库或模型。"""
        program = """
import asyncio
import socket
import sitecustomize
assert hasattr(sitecustomize, 'ProtectedLoopbackPort')
left, right = socket.socketpair()
try:
    left.settimeout(2)
    right.settimeout(2)
    left.sendall(b'left')
    assert right.recv(4) == b'left'
    right.sendall(b'right')
    assert left.recv(5) == b'right'
finally:
    left.close()
    right.close()
async def main():
    await asyncio.sleep(0)
    return 'isolated-event-loop-ok'
assert asyncio.run(main()) == 'isolated-event-loop-ok'
print('socketpair-and-asyncio-ok')
"""
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, encoding="utf-8", timeout=20,
            env={**os.environ, "DSH_TEST_ISOLATION": "1", "PYTHONPATH": str(GUARD.parent), "PYTHONUTF8": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "socketpair-and-asyncio-ok")


if __name__ == "__main__":
    unittest.main()
