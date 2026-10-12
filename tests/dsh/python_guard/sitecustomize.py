"""原生平台隔离验收专用：禁用外部数据库与非回环网络连接。"""
import ipaddress
import os
import socket

if os.environ.get("DSH_TEST_ISOLATION") == "1":
    import pymysql
    import psycopg

    def blocked_database(*args, **kwargs):
        """平台元数据只能使用本轮临时 SQLite。"""
        raise AssertionError("DSH 隔离验收禁止 MySQL/PostgreSQL 连接")

    pymysql.connect = blocked_database
    pymysql.Connection = blocked_database
    psycopg.connect = blocked_database
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex

    class ProtectedLoopbackPort(AssertionError):
        """数字回环目标命中保留端口；普通调用仍直接拒绝。"""

    def check_target(address):
        """只允许数字回环地址，同时排除在用平台端口。"""
        if not isinstance(address, tuple) or len(address) < 2:
            raise AssertionError("DSH 验收只允许本机 HTTP 桩")
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise AssertionError("DSH 验收禁止外部网络及在用平台连接")
        if address[1] in {9, 8011, 8012}:
            raise ProtectedLoopbackPort("DSH 验收禁止连接保留的本机端口")

    def connect(sock, address):
        check_target(address)
        return _connect(sock, address)

    def connect_ex(sock, address):
        check_target(address)
        return _connect_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

    _socketpair = socket.socketpair
    if _socketpair is getattr(socket, "_fallback_socketpair", None):
        def socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
            """Windows 内部配对命中保留端口时有界重建，不重试普通连接或业务请求。"""
            for attempt in range(32):
                try:
                    return _socketpair(family, type, proto)
                except ProtectedLoopbackPort:
                    # 标准库在连接失败后先关闭 client 和 listener，尚未 accept。
                    # 仅重建此次内部配对，不临时关闭全局守卫；其他异常立即传播。
                    if attempt == 31:
                        raise

        socket.socketpair = socketpair
