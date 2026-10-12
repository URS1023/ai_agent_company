"""真实私有 Worker 的受控管道夹具：临时 SQLite、零模型与零网络。"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "backend")]
from run_isolated_backend import configure_isolation

configure_isolation()
target = Path(sys.argv[1]).resolve()
assert target.is_absolute() and target.suffix == ".sqlite"
os.environ.update(DATABASE_URL="sqlite:///" + target.as_posix(), PYTHONUTF8="1", CHAT_ENGINE="dsh",
                  LOCAL_STORAGE_ROOT=str(target.parent / "storage"), LOG_DIR=str(target.parent / "logs"),
                  HARNESS_HOME_ROOT=str(target.parent / "harness"))
# 合成配置不能隐式加载仓库部署配置；存在性检查在配置模块导入后恢复。
original_exists = Path.exists
Path.exists = lambda self: False if self.name in {".env", ".env.local"} else original_exists(self)
from app.config import settings
Path.exists = original_exists
import socket
import threading
import runpy
os.environ["DSH_TEST_ISOLATION"] = "1"
# 复用已验收的 Windows 内部 socketpair 守卫，再收紧到只允许它的动态作用域。
runpy.run_path(str(ROOT / "tests/dsh/python_guard/sitecustomize.py"))
pair_state = threading.local()
original_pair, isolated_connect = socket.socketpair, socket.socket.connect


def local_pair(*args, **kwargs):
    """Windows asyncio 自唤醒需标准库本机 socketpair，例外只存在于该函数当前线程。"""
    pair_state.active = True
    try:
        return original_pair(*args, **kwargs)
    finally:
        pair_state.active = False


def no_application_connect(sock, address):
    if getattr(pair_state, "active", False):
        return isolated_connect(sock, address)
    raise AssertionError("回执夹具禁止应用网络连接")


socket.socketpair = local_pair
socket.socket.connect = no_application_connect
socket.socket.connect_ex = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("回执夹具禁止应用网络连接"))

source, output = sys.stdin, sys.stdout
source.reconfigure(encoding="utf-8")
output.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.stdout = sys.stderr

from app.business_worker.protocol import JsonRpc
from app.business_worker.server import Worker
from app.business_worker import application, chat
from app.business_worker.context import session
from app.models import Message, Run, RunEvent, ToolCall
from app.providers import model
from sqlalchemy import select

model._profile_config = lambda *a, **kw: {"model": "fixture", "base_url": "http://127.0.0.1:9/v1", "api_key": "fixture",
    "capabilities": {"runtime_options": {"thinking": "disabled"}}}
fault = {"mode": "none", "dropped": 0, "execute": 0}


class Sink:
    """丢包点位于真实提交与真实 JSON-RPC 编码之后，只改变输出，不替换业务结果。"""
    def write(self, raw):
        packet = json.loads(raw)
        if "result" in packet and isinstance(packet["result"], dict) and packet["result"].get("receipt_protocol") == 1:
            value = packet["result"]
            if value.get("state") == "committed" and fault["mode"] in {"reply", "both"} and fault["dropped"] == 0:
                with session() as db:
                    row = db.get(RunEvent, value["receipt"]["id"])
                    assert row.payload["data"]["result"] == value["result"]
                fault["dropped"] += 1
                return len(raw)
        if packet.get("method") == "tool.receipt.committed" and fault["mode"] in {"notice", "both"}:
            return len(raw)
        return output.write(raw)

    def flush(self):
        output.flush()


rpc = JsonRpc(source, Sink())
worker = Worker(rpc)
application.application()


def request(path, body, headers=None):
    return {"method": "POST", "path": path, "query_string": "", "headers": list({"content-type": "application/json", **(headers or {})}.items()),
            "body_base64": base64.b64encode(json.dumps(body).encode()).decode()}


def response(path, body, headers=None):
    value = application.dispatch(request(path, body, headers))
    assert value["status"] in {200, 201}, value["status"]
    return json.loads(base64.b64decode(value["body_base64"]))


def handle(method, params):
    if method == "fixture.prepare":
        worker.initialize()
        token = response("/api/auth/login", {"username": "workspace-admin", "password": settings.BOOTSTRAP_ADMIN_PASSWORD})
        headers = {"authorization": "Bearer " + token["token"], "x-workspace-id": token["workspaces"][0]["id"]}
        conv = response("/api/chat/conversations", {"title": "回执丢包隔离测试"}, headers)
        prepared = chat.prepare(request("/api/chat/conversations/" + conv["id"] + "/send", {"content": "查询当前时间"}, headers))
        fault.update(mode=params.get("mode", "reply"), dropped=0)
        return prepared
    if method == "fixture.inspect":
        with session() as db:
            run = db.get(Run, params["run_id"])
            calls = db.scalars(select(ToolCall).where(ToolCall.run_id == run.id)).all()
            events = db.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
            return {"fault": fault, "calls": [{"status": row.status, "result": row.result_json} for row in calls],
                "events": [{"id": row.id, "payload": row.payload} for row in events],
                "parts": db.get(Message, run.context_snapshot["assistant_message_id"]).parts_json}
    if method == "tool.execute":
        fault["execute"] += 1
    return worker.handle(method, params)


try:
    rpc.serve(handle)
finally:
    worker.shutdown()
