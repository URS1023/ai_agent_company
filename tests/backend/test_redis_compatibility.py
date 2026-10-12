"""Exercise real Redis client handshakes against a local RESP2-only server."""
from __future__ import annotations

import asyncio
import socketserver
import threading

import pytest

from app.common import events, redis_sync
from app.config import settings
from app.modules.system_tools import monitoring


@pytest.fixture
def legacy_redis(monkeypatch):
    commands = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            while line := self.rfile.readline():
                if not line.startswith(b"*"):
                    return
                args = []
                for _ in range(int(line[1:])):
                    size = int(self.rfile.readline()[1:])
                    args.append(self.rfile.read(size))
                    self.rfile.read(2)
                name = args[0].upper()
                commands.append(name)
                if name in {b"HELLO", b"CLIENT"}:
                    self.wfile.write(b"-ERR unknown command\r\n")
                elif name == b"PING":
                    self.wfile.write(b"+PONG\r\n")
                elif name == b"INFO":
                    data = b"redis_version:5.0.14.1\r\nused_memory:1024\r\nconnected_clients:1\r\ndb0:keys=3,expires=1,avg_ttl=0\r\n"
                    self.wfile.write(b"$" + str(len(data)).encode() + b"\r\n" + data + b"\r\n")
                elif name == b"PUBLISH":
                    self.wfile.write(b":1\r\n")
                else:
                    self.wfile.write(b"+OK\r\n")

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setattr(settings, "REDIS_URL", f"redis://127.0.0.1:{server.server_address[1]}/0")
        monkeypatch.setattr(redis_sync, "_client", None)
        yield commands
        if redis_sync._client:
            redis_sync._client.close()
        server.shutdown()
        worker.join(timeout=2)


def test_sync_and_async_event_clients_work_with_redis_5(legacy_redis):
    sync = redis_sync.redis_client_sync()
    assert sync.ping() is True
    assert sync.publish("test-only", "event") == 1

    async def check_async():
        client = await events.get_redis()
        try:
            assert await client.ping() is True
            assert await client.publish("test-only", "event") == 1
        finally:
            await client.aclose()

    asyncio.run(check_async())
    assert legacy_redis.count(b"PING") == 2
    assert legacy_redis.count(b"PUBLISH") == 2
    assert b"HELLO" not in legacy_redis


def test_health_and_cache_counters_support_legacy_redis(legacy_redis, monkeypatch):
    monkeypatch.setattr(monitoring, "require_permission", lambda *_args: None)
    assert monitoring._redis_check() == {"version": "5.0.14.1", "database": "0"}
    cache = monitoring.cache_summary(object())
    assert cache["status"] == "healthy" and cache["keys"] == 3
    assert cache["expires"] == 1 and cache["usedMemory"] == 1024
    assert b"HELLO" not in legacy_redis
