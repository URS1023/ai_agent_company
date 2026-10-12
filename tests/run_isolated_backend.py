"""Run backend integration tests using local disposable storage and SQLite only.

External database drivers are disabled, even if a saved configuration points to
localhost. HTTP fixtures may use numeric loopback addresses; external network
connections are rejected. Live MySQL/model/browser acceptance is a separate gate.

中文说明（后端集成测试入口）：只用本机一次性存储与 SQLite 跑 tests/backend 集成测试。
运行方式：仓库根目录执行本文件（或经它调用 pytest tests/backend）。
硬性约束：禁用外部数据库驱动（即使配置指向本机也如此）；HTTP 夹具只允许数字回环地址，
其它外部网络连接一律拒绝；真实 MySQL/模型/浏览器验收属于独立 gate。
"""
from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile


def configure_isolation() -> Path:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))
    os.environ.update(
        DATABASE_URL="sqlite:///:memory:", APP_ENV="test", AUTO_MIGRATE="1",
        BOOTSTRAP_ADMIN_PASSWORD="IsolatedBootstrap12345",
        BOOTSTRAP_LYNX_REPORTS="0", SCHEDULER_ENABLED="0",
        # 搜索与缓存同样使用拒绝连接的目标，避免测试沿用本机在用服务。
        REDIS_URL="redis://127.0.0.1:9/0", ES_URL="http://127.0.0.1:9",
        # 模型准入使用固定假配置；旧测试也不能依赖部署密钥存在才通过。
        AGNES_BASE_URL="http://127.0.0.1:9/v1", AGNES_API_KEY="isolated-fixture-only",
        AGNES_CHAT_MODEL="fixture-model",
        SILICONFLOW_BASE_URL="http://127.0.0.1:9/v1", SILICONFLOW_API_KEY="isolated-fixture-only",
        SILICONFLOW_VISION_API_KEY="isolated-fixture-only",
        # 测试确定性：不继承 .env.local 的 CHAT_ENGINE=harness 部署开关。
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CHAT_ENGINE="openwebui",
    )

    def no_external_database(*args, **kwargs):
        raise AssertionError("Isolated backend tests cannot connect to MySQL/PostgreSQL")

    import pymysql
    import psycopg
    pymysql.connect = no_external_database
    pymysql.Connection = no_external_database
    psycopg.connect = no_external_database

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check_address(address):
        if not isinstance(address, tuple):
            raise AssertionError("Only loopback HTTP fixtures are allowed")
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError("Isolated tests cannot connect to an external host")
        # 回环地址不等于测试实例；阻止过期 mock 意外访问当前项目的在用应用与原生聊天服务。
        if len(address) > 1 and address[1] in {9, 8011, 8012}:
            raise AssertionError("隔离测试不能连接在用的 8011/8012 服务，请使用协议夹具或独立原生验收实例")

    def connect(sock, address):
        check_address(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check_address(address)
        return original_connect_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    return root


def main() -> int:
    configure_isolation()
    with tempfile.TemporaryDirectory(prefix="test5-backend-acceptance-") as storage:
        # 配置实际读取 LOCAL_STORAGE_ROOT；同时隔离日志和原生会话，避免落到在用 storage。
        os.environ.update(LOCAL_STORAGE_ROOT=storage, STORAGE_ROOT=storage,
                          LOG_DIR=str(Path(storage) / "logs"), HARNESS_HOME_ROOT=str(Path(storage) / "harness"))
        import pytest
        arguments = sys.argv[1:]
        # 传入具体测试文件时只执行这些文件；仅传 -k 等选项时仍限定在 backend 目录。
        has_target = any(not value.startswith("-") and Path(value.split("::", 1)[0]).exists()
                         for value in arguments)
        try:
            return pytest.main(["-q", "--tb=short", *([] if has_target else ["tests/backend"]), *arguments])
        finally:
            # Windows 上日志文件仍打开会阻止临时目录清理，先释放本次测试的处理器。
            root_logger = logging.getLogger()
            for handler in list(root_logger.handlers):
                if getattr(handler, "_execution_owned", False):
                    root_logger.removeHandler(handler)
                    handler.close()
            logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
