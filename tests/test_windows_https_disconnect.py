"""Real TLS resets against a disposable backend, never the running LAN service.

中文说明：用一次性后端做真实 TLS 断连验证，绝不间断或改动正在运行的 LAN 服务。
隔离方式：临时目录 + 一次性 SQLite + 子进程启动的测试后端，仅在本机回环上通信。
"""
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "win32" or sys.version_info[:2] != (3, 12), reason="Windows Python 3.12")
def test_https_stays_healthy_after_repeated_peer_resets(tmp_path):
    cert_dir = tmp_path / "certs"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/create_lan_certificate.py"),
        "--ip", "192.168.6.16", "--directory", str(cert_dir),
    ], check=True, capture_output=True)
    context = ssl.create_default_context(cafile=str(cert_dir / "root-ca.crt"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"https://127.0.0.1:{port}"
    # Explicit proxy bypass applies only to this local fixture's HTTP client.
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    env = {
        **os.environ, "HOST": "127.0.0.1", "PORT": str(port), "APP_ENV": "test",
        "DATABASE_URL": "sqlite:///" + (tmp_path / "test.sqlite").as_posix(),
        "LOCAL_STORAGE_ROOT": str(tmp_path / "storage"),
        "BOOTSTRAP_ADMIN_PASSWORD": "IsolatedResetCheck12345",
        "BOOTSTRAP_LYNX_REPORTS": "0", "SCHEDULER_ENABLED": "0", "AUTO_MIGRATE": "1",
        "SSL_CERTFILE": str(cert_dir / "server.crt"), "SSL_KEYFILE": str(cert_dir / "server.key"),
    }
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-c",
             "import logging, runpy; "
             "logger = logging.getLogger('app.core.windows_asyncio'); "
             "logger.setLevel(logging.DEBUG); logger.addHandler(logging.StreamHandler()); "
             "runpy.run_path('run.py', run_name='__main__')"],
            cwd=ROOT / "backend", env=env, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                assert server.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
                try:
                    with client.open(base + "/api/health", timeout=2) as response:
                        assert json.load(response)["app"] == "test"
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(0.25)

            for _ in range(40):
                with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                    with context.wrap_socket(raw, server_hostname="127.0.0.1") as connection:
                        connection.sendall(
                            b"GET /api/health HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
                        )
                        assert b"200 OK" in connection.recv(4096)
                        # Windows linger uses two unsigned shorts. Closing with
                        # zero linger sends RST, with no TLS close_notify.
                        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))

            for path in ("/ai", "/api/health", "/api/ready"):
                with client.open(base + path, timeout=10) as response:
                    assert response.status == 200
            time.sleep(0.5)
            output = log_path.read_text(encoding="utf-8", errors="replace")
            assert "Exception in callback" not in output, output
            assert "Traceback (most recent call last)" not in output, output
            print(f"40 TLS resets; all post-reset checks passed; log: {log_path}")
            print("Handled shutdown resets:", output.count("Peer reset during socket shutdown"))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
