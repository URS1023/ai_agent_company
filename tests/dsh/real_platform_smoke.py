"""真实 Node/DSH/Python Worker 验收：临时 SQLite、本机模型桩、有限工作量。"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

import requests

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PASSWORD = "IsolatedBootstrap12345"
FINISHED = {"completed", "partial", "failed", "cancelled", "interrupted"}
PARTIAL_TEXT = "已经流出但尚未完成的回答。"


class ModelState:
    """固定模型响应，不把桩验证解释为真实模型业务质量。"""
    def __init__(self):
        self.requests = []
        self.hold_started = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.image_file_id = None


def model_handler(state):
    class FixtureModel(BaseHTTPRequestHandler):
        """有限次流式补全，按本轮标记返回工具或文本。"""
        def log_message(self, *args):
            """省略本机 HTTP 访问日志。"""

        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with state.lock:
                    state.requests.append(payload)
                    number = len(state.requests)
                if number > 30:
                    self.send_error(429, "fixture request budget exceeded")
                    return
                messages = payload.get("messages", [])
                # DSH 会在用户消息之后注入技能目录；按本轮场景标记定位实际用户消息。
                latest = max((index for index, message in enumerate(messages) if message.get("role") == "user"
                              and "SCENARIO_" in json.dumps(message.get("content", ""))), default=-1)
                content = json.dumps(messages[latest].get("content", ""), ensure_ascii=False) if latest >= 0 else ""
                has_result = any(message.get("role") == "tool" for message in messages[latest + 1:])
                tool = None
                if "SCENARIO_REFERENCE_PRESENT" in content:
                    receipts = [message for message in messages[latest + 1:] if message.get("role") == "tool"]
                    if not receipts:
                        tool = ("save_json_artifact", {"data": {"value": 9}, "name": "referenced-delivery"})
                    elif len(receipts) == 1:
                        raw = receipts[0]["content"]
                        text = raw if isinstance(raw, str) else "".join(part.get("text", "") for part in raw)
                        receipt = json.loads(text)
                        assert receipt["metadata"]["delivery"]["already_presented"] is True
                        tool = ("present", {"files": [{"path": receipt["artifacts"][0]["download_url"]}]})
                elif "SCENARIO_NATIVE_PRESENT" in content:
                    count = sum(message.get("role") == "tool" for message in messages[latest + 1:])
                    tool = [("write", {"file_path": "native-delivery.txt", "content": "原生 DSH 文件交付，数值 7。"}),
                            ("present", {"files": [{"path": "native-delivery.txt", "description": "原生文件回执"}]})][count] if count < 2 else None
                elif "SCENARIO_ARTIFACT" in content and not has_result:
                    tool = ("save_json_artifact", {"data": {"value": 7, "scope": "fixture"}, "name": "fixture-delivery"})
                elif "SCENARIO_TOOL_ERROR" in content and not has_result:
                    tool = ("read_file", {"file_id": "missing-fixture-file"})
                elif "SCENARIO_IMAGE_TOOL" in content and not has_result:
                    tool = ("read_file", {"file_id": state.image_file_id, "include_images": True, "max_images": 1})
                partial_hold = "SCENARIO_HOLD_PARTIAL" in content
                if "SCENARIO_HOLD" in content and not partial_hold:
                    state.hold_started.set()
                    if not state.release.wait(10):
                        self.send_error(504, "fixture hold budget exceeded")
                        return
                if tool:
                    name, arguments = tool
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"fixture-tool-{number}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}
                    finish = "tool_calls"
                else:
                    delta = {"role": "assistant", "content": "本机验收已响应。" if "SCENARIO_MISSING" not in content else "Word 文档已经生成。"}
                    finish = "stop"
                self.send_response(200)
                if not payload.get("stream"):
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"id": f"fixture-{number}", "model": "fixture-model",
                        "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88}}).encode())
                    return
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                if partial_hold:
                    partial = {"id": f"fixture-{number}", "object": "chat.completion.chunk", "created": 1,
                        "model": "fixture-model", "choices": [{"index": 0,
                        "delta": {"role": "assistant", "content": PARTIAL_TEXT}, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(partial, ensure_ascii=False) + "\n\n").encode())
                    self.wfile.flush()
                    state.hold_started.set()
                    if not state.release.wait(10):
                        return
                for chunk, reason in [(delta, None), ({}, finish)]:
                    value = {"id": f"fixture-{number}", "object": "chat.completion.chunk", "created": 1,
                             "model": "fixture-model", "choices": [{"index": 0, "delta": chunk, "finish_reason": reason}]}
                    if reason:
                        value["usage"] = {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88}
                    self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    return FixtureModel


class Host:
    """拥有本轮宿主进程和 HTTP 会话，失败也会回收全部子进程。"""
    def __init__(self, directory, model_port, *, env_overrides=None):
        self.directory = directory
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                   "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        endpoint = f"http://127.0.0.1:{model_port}/v1"
        env.update({"HOST": "127.0.0.1", "PORT": "0", "DSH_PROJECT_ROOT": str(ROOT), "DSH_PYTHON": sys.executable,
                    "DSH_UPSTREAM_ROOT": str(ROOT / ".implementation/harness_migration/upstream"),
                    "DSH_HOME": str(directory / "dsh"), "DSH_TELEMETRY_DISABLED": "1", "DSH_TEST_ISOLATION": "1",
                    "DSH_PRIMARY_RUNTIME": "", "DSH_AGENTS_HOME": str(directory / "disabled"),
                    "PYTHONPATH": str(HERE / "python_guard"), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NODE_OPTIONS": "--import=" + (HERE / "local_network_only.mjs").as_uri(),
                    "DATABASE_URL": "sqlite:///" + (directory / "platform.sqlite").as_posix(),
                    # 健康检查与知识索引也不能沿用部署中的本机缓存/检索服务；9 端口由隔离守卫拒绝。
                    "REDIS_URL": "redis://127.0.0.1:9/0", "ES_URL": "http://127.0.0.1:9",
                    "APP_ENV": "test", "AUTO_MIGRATE": "1", "BOOTSTRAP_ADMIN_PASSWORD": PASSWORD,
                    "BOOTSTRAP_LYNX_REPORTS": "0", "SCHEDULER_ENABLED": "0", "OPCODE_PROXY_ENABLED": "0",
                    "LOCAL_STORAGE_ROOT": str(directory / "storage"), "STORAGE_ROOT": str(directory / "storage"),
                    "LOG_DIR": str(directory / "logs"), "HARNESS_HOME_ROOT": str(directory / "sessions"),
                    "AGNES_BASE_URL": endpoint, "AGNES_API_KEY": "fixture-only-key", "AGNES_CHAT_MODEL": "fixture-model",
                    "SILICONFLOW_BASE_URL": endpoint, "SILICONFLOW_API_KEY": "fixture-only-key",
                    "SILICONFLOW_VISION_API_KEY": "fixture-only-key", "VISION_ENABLED": "1",
                    "SECRET_MASTER_KEY": "test-fixture-master-key-0000000000"})
        # 真实业务验收可替换受控模型代理和本机数据库保护，其余隔离目录仍由本实例拥有。
        env.update(env_overrides or {})
        self.env = env
        self.events = queue.Queue()
        self.diagnostics = deque(maxlen=100)
        self.process = subprocess.Popen(["node", str(ROOT / "services/dsh_host/launch.mjs")], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.readers = [threading.Thread(target=self.read, args=(self.process.stdout, True), daemon=True),
                        threading.Thread(target=self.read, args=(self.process.stderr, False), daemon=True)]
        for thread in self.readers:
            thread.start()
        self.http = requests.Session()
        self.http.trust_env = False
        self.base = None

    def read(self, stream, output):
        """保存有界诊断并提取就绪事件，避免管道堵塞。"""
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                self.diagnostics.append(line.strip()[:1000])
                continue
            if output:
                self.events.put(value)
            else:
                self.diagnostics.append(str(value)[:1000])
        if output:
            self.events.put({"closed": True})

    def ready(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            if event.get("closed"):
                raise RuntimeError("宿主未就绪即退出：\n" + "\n".join(self.diagnostics))
            if event.get("event") == "dsh.ready":
                self.base = f"http://127.0.0.1:{event['port']}"
                return
        raise TimeoutError("宿主就绪超时")

    def request(self, method, path, *, status=200, **kwargs):
        response = self.http.request(method, self.base + path, timeout=20, **kwargs)
        assert response.status_code == status, (method, path, response.status_code, response.text[:2000])
        return response

    def json(self, method, path, *, status=200, **kwargs):
        return self.request(method, path, status=status, **kwargs).json()

    def login(self):
        value = self.json("POST", "/api/auth/login", json={"username": "workspace-admin", "password": PASSWORD})
        self.http.headers.update({"Authorization": "Bearer " + value["token"], "X-Workspace-Id": value["workspaces"][0]["id"]})

    def send(self, conversation, marker, request_id, **extra):
        return self.json("POST", f"/api/chat/conversations/{conversation}/send", status=202,
            headers={"Accept": "application/json"}, json={"content": marker, "request_id": request_id,
                "model_profile": "fixture", "mode": "standard", "delivery_mode": "queue", **extra})

    def settled(self, run_id):
        response = self.request("GET", f"/api/chat/runs/{run_id}/events", stream=True)
        events = []
        event = None
        try:
            for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    value = json.loads(line[5:].strip())
                    events.append((event, value))
                    if event == "done":
                        assert value.get("status") in FINISHED, value
                        return value, events
        finally:
            response.close()
        raise AssertionError(f"运行事件提前断开：{events[-4:]}")

    def close(self):
        self.http.close()
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"method":"shutdown"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False)
                else:
                    self.process.kill()
                self.process.wait(timeout=5)
        for thread in self.readers:
            thread.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass


def verify_platform(host, state, model_port, checks):
    """所有请求都针对本轮创建的隔离宿主。"""
    host.ready()
    assert host.json("GET", "/api/health")["engine"] == "dsh"
    assert b"<html" in host.request("GET", "/ai").content.lower()
    host.login()
    capability = host.json("GET", "/api/chat/capabilities")
    assert capability["features"]["queue"] and len(capability["plugins"]) == 14
    checks.append("真实 DSH 宿主启动、静态页面、登录与业务插件目录")
    payload = "原始附件内容：数值7。\n第二行保持不变。".encode("utf-8")
    uploaded = host.json("POST", "/api/files", status=201,
                         files={"file": ("fixture-input.txt", payload, "text/plain")})
    file_id = uploaded.get("id") or uploaded.get("file_id")
    assert file_id, uploaded
    assert host.request("GET", f"/api/files/{file_id}/download").content == payload
    checks.append("原上传/下载接口在私有Worker转发后字节一致")
    host.json("POST", "/api/models/profiles", status=201, json={"profile_name": "fixture", "provider": "openai-compatible",
        "model": "fixture-model", "endpoint": f"http://127.0.0.1:{model_port}/v1", "api_key": "fixture-only-key",
        "capabilities": ["chat", "tools"], "runtime_options": {"protocol": "openai-completions", "thinking": "disabled",
            "context_window": 32768, "max_output_tokens": 4096, "compat": {"thinkingFormat": "deepseek"}}})
    host.json("POST", "/api/models/profiles", status=201, json={"profile_name": "vision", "provider": "openai-compatible",
        "model": "fixture-vision", "endpoint": f"http://127.0.0.1:{model_port}/v1", "api_key": "fixture-only-key",
        "capabilities": ["vision", "ocr"], "runtime_options": {"protocol": "openai-completions", "thinking": "disabled",
            "context_window": 32768, "max_output_tokens": 1400}})
    vision = host.json("POST", "/api/models/probe/vision")
    assert vision["status"] == "passed" and vision["details"]["probe_type"] == "vision", vision
    image_blocks = [block for row in state.requests if row.get("model") == "fixture-vision"
                    for message in row.get("messages", []) if isinstance(message.get("content"), list)
                    for block in message["content"] if block.get("type") == "image_url"]
    assert image_blocks, image_blocks
    image_url = image_blocks[0]["image_url"]["url"]
    assert image_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")), image_url[:80]
    image_data = base64.b64decode(image_url.split(",", 1)[1], validate=True)
    assert len(image_data) > 1000
    checks.append("实际视觉探测经Worker反向RPC和DSH附件解析，图片内容到达模型请求")
    image_mime = image_url.split(";", 1)[0][5:]
    uploaded_image = host.json("POST", "/api/files", status=201,
        files={"file": ("fixture-image.png" if image_mime == "image/png" else "fixture-image.jpg", image_data, image_mime)})
    state.image_file_id = uploaded_image.get("id") or uploaded_image.get("file_id")
    image_conversation = host.json("POST", "/api/chat/conversations", status=201, json={"title": "图片隔离验收"})["id"]
    image_tool = host.send(image_conversation, "SCENARIO_IMAGE_TOOL 读取图片中的内容。", "fixture-image-tool-1",
                          file_ids=[state.image_file_id], skill_ids=["file-work"])
    result, image_events = host.settled(image_tool["run_id"])
    assert result["status"] == "completed", result
    read_results = [value.get("result", {}) for event, value in image_events
                    if event == "tool.completed" and value.get("tool_name") == "read_file"]
    assert read_results and read_results[0]["data"]["visual_image_count"] == 1, read_results
    assert read_results[0]["data"]["vision_warnings"] == [], read_results
    host.json("POST", "/api/models/profiles", status=201, json={"profile_name": "multimodal", "provider": "openai-compatible",
        "model": "fixture-multimodal", "endpoint": f"http://127.0.0.1:{model_port}/v1", "api_key": "fixture-only-key",
        "capabilities": ["chat", "tools", "vision"], "runtime_options": {"protocol": "openai-completions", "thinking": "disabled",
            "context_window": 32768, "max_output_tokens": 4096, "input_modalities": ["text", "image"]}})
    image_direct = host.send(image_conversation, "SCENARIO_IMAGE_DIRECT 直接查看上传图片。", "fixture-image-direct-1",
                             file_ids=[state.image_file_id], model_profile="multimodal")
    assert host.settled(image_direct["run_id"])[0]["status"] == "completed"
    assert any(block.get("type") == "image_url" for row in state.requests if row.get("model") == "fixture-multimodal"
               for message in row.get("messages", []) if isinstance(message.get("content"), list) for block in message["content"])
    checks.append("主对话附图：纯文本模型通过read_file识图，多模态模型通过原生图片内容直接接收")
    skill = host.json("POST", "/api/skills", status=201,
        json={"name": "隔离辅助模型验证", "spec": {"instructions": "只回答本机验收已响应。"}})
    tested = host.json("POST", f"/api/skills/{skill['skill_id']}/test-run", json={"input": "SCENARIO_AUXILIARY"})
    assert tested["status"] == "passed" and "本机验收" in tested["output"]["text"], tested
    plugin = host.json("POST", "/api/plugins", status=201,
        json={"name": "隔离插件原生执行", "spec": {"instructions": "只回答本机验收已响应。"}})
    tested = host.json("POST", f"/api/plugins/{plugin['plugin_id']}/test-run", json={"input": "SCENARIO_PLUGIN"})
    assert tested["status"] == "passed" and "本机验收" in tested["reply"], tested
    checks.append("Skill辅助模型与Plugin试运行均反向调用同一DSH宿主")
    conversation = host.json("POST", "/api/chat/conversations", status=201, json={"title": "DSH 隔离验收"})["id"]
    accepted = host.send(conversation, "SCENARIO_ARTIFACT 保存一个 JSON 文件。", "fixture-artifact-1", skill_ids=["file-work"],
                         required_deliveries=[{"type": "json", "count": 1}])
    result, events = host.settled(accepted["run_id"])
    assert result["status"] == "completed", (result, events[-6:])
    detail = host.json("GET", f"/api/chat/conversations/{conversation}")
    deliveries = detail["projections"]["deliveries"]
    ready = [item for item in deliveries if item["status"] == "ready"]
    assert ready and ready[0]["source_run_id"] == accepted["run_id"], deliveries
    artifact_id = ready[0].get("artifact_id") or ready[0]["id"]
    download = host.request("GET", f"/api/artifacts/{artifact_id}/download")
    assert json.loads(download.content)["value"] == 7
    before = len(state.requests)
    duplicate = host.send(conversation, "SCENARIO_ARTIFACT 保存一个 JSON 文件。", "fixture-artifact-1", skill_ids=["file-work"],
                          required_deliveries=[{"type": "json", "count": 1}])
    assert duplicate["duplicate"] and duplicate["run_id"] == accepted["run_id"]
    assert len(state.requests) == before
    assert duplicate["request_id"] == "fixture-artifact-1"
    checks.append("真实模型流→企业工具→JSON工件→下载；重复请求不重执行")
    native_file = host.send(conversation, "SCENARIO_NATIVE_PRESENT 生成一个文本文件并交付。", "fixture-native-present-1")
    native_result, native_events = host.settled(native_file["run_id"])
    assert native_result["status"] == "completed", native_events[-8:]
    native_history = host.json("GET", f"/api/chat/conversations/{conversation}")
    native_artifacts = [item for item in native_history["projections"]["deliveries"]
                        if item.get("source_run_id") == native_file["run_id"] and item.get("status") == "ready"]
    assert len(native_artifacts) == 1, native_history["projections"]["deliveries"]
    assert host.request("GET", native_artifacts[0]["download_url"]).content.decode("utf-8") == "原生 DSH 文件交付，数值 7。"
    checks.append("原生write→present→平台工件卡片→认证下载，源文件与下载字节一致")
    referenced = host.send(conversation, "SCENARIO_REFERENCE_PRESENT 生成并交付 JSON 工件。", "fixture-reference-present-1",
                           required_deliveries=[{"type": "json", "count": 1}])
    reference_result, reference_events = host.settled(referenced["run_id"])
    assert reference_result["status"] == "completed", reference_events[-8:]
    reference_history = host.json("GET", f"/api/chat/conversations/{conversation}")
    reused = [item for item in reference_history["projections"]["deliveries"]
              if item.get("source_run_id") == referenced["run_id"] and item.get("status") == "ready"]
    assert len(reused) == 1, reused
    assert json.loads(host.request("GET", reused[0]["download_url"]).content)["value"] == 9
    assert not any(event == "tool.failed" for event, data in reference_events), reference_events
    checks.append("业务工件URL误传原生present时确定性复用已授权附件，不搜索文件或创建重复工件")
    missing = host.send(conversation, "SCENARIO_MISSING 生成 Word 文档。", "fixture-missing-1")
    result, _ = host.settled(missing["run_id"])
    assert result["status"] == "partial", result
    checks.append("缺少真实交付物时业务状态为 partial，不能因正文宣称完成而成功")
    error = host.send(conversation, "SCENARIO_TOOL_ERROR 读取文件。", "fixture-toolerror-1", skill_ids=["file-work"])
    host.settled(error["run_id"])
    recovery = host.send(conversation, "SCENARIO_RECOVERY 继续回答一个普通问题。", "fixture-recovery-1")
    result, _ = host.settled(recovery["run_id"])
    assert result["status"] == "completed", result
    checks.append("工具失败后同一会话仍可正常继续")
    first = host.send(conversation, "SCENARIO_HOLD 队列首条。", "fixture-hold-1")
    assert state.hold_started.wait(3), "模型桩未收到首条请求"
    queued = host.send(conversation, "SCENARIO_QUEUE 生成 PPT 演示文稿。", "fixture-queued-1")
    removed = host.send(conversation, "SCENARIO_REMOVED 不应执行。", "fixture-removed-1")
    host.json("POST", f"/api/chat/conversations/{conversation}/queue/{queued['run_id']}", json={"action": "edit", "content": "SCENARIO_EDITED 生成 Word 文档。"})
    host.json("POST", f"/api/chat/conversations/{conversation}/queue/{removed['run_id']}", json={"action": "remove"})
    state.release.set()
    assert host.settled(first["run_id"])[0]["status"] == "completed"
    assert host.settled(queued["run_id"])[0]["status"] == "partial"
    assert host.settled(removed["run_id"])[0]["status"] == "cancelled"
    assert any("SCENARIO_EDITED" in json.dumps(row.get("messages", []), ensure_ascii=False) for row in state.requests)
    edited_requests = [row for row in state.requests if any(message.get("role") == "user"
        and "SCENARIO_EDITED" in json.dumps(message.get("content", ""), ensure_ascii=False) for message in row.get("messages", []))]
    assert edited_requests
    system_text = "\n".join(message["content"] if isinstance(message.get("content"), str) else
        "\n".join(block.get("text", "") for block in message.get("content", []))
        for message in edited_requests[-1]["messages"] if message.get("role") == "system")
    task_line = next(line.split("当前业务任务与交付约束：", 1)[1] for line in system_text.splitlines()
                     if "当前业务任务与交付约束：" in line)
    assert json.loads(task_line)["required_deliveries"] == [{"type": "document", "count": 1}], task_line
    checks.append("队列受理、PPT改Word后模型任务约束同步、移除和串行执行闭环")
    state.hold_started.clear()
    state.release.clear()
    active = host.send(conversation, "SCENARIO_HOLD_PARTIAL 等待取消。", "fixture-cancel-1")
    assert state.hold_started.wait(3)
    deadline = time.monotonic() + 3
    while True:
        history = host.json("GET", f"/api/chat/conversations/{conversation}")
        if any(message.get("content") == PARTIAL_TEXT for message in history["messages"]):
            break
        assert time.monotonic() < deadline, "正文增量未在预算内持久化"
        time.sleep(.05)
    host.json("POST", f"/api/chat/runs/{active['run_id']}/cancel")
    state.release.set()
    result, cancel_events = host.settled(active["run_id"])
    assert result["status"] == "cancelled"
    assert next(value["content"] for event, value in cancel_events if event == "run.cancelled") == PARTIAL_TEXT
    history = host.json("GET", f"/api/chat/conversations/{conversation}")
    assert any(message.get("content") == PARTIAL_TEXT and message.get("status") == "cancelled" for message in history["messages"])
    next_run = host.send(conversation, "SCENARIO_AFTER_CANCEL 正常回答。", "fixture-aftercancel-1")
    assert host.settled(next_run["run_id"])[0]["status"] == "completed"
    checks.append("正文流出后取消：实时终态与刷新历史一致，取消后能继续对话")
    return conversation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/implementation/dsh-integration-result.json")
    args = parser.parse_args()
    started = time.monotonic()
    state = ModelState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), model_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"scope": "真实 Node DSH + Python Worker；临时 SQLite；本机模型桩；无业务数据库连接", "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="test5-dsh-platform-") as temporary:
            directory = Path(temporary)
            host = Host(directory, server.server_port)
            try:
                conversation = verify_platform(host, state, server.server_port, report["checks"])
            except BaseException:
                report["diagnostics"] = list(host.diagnostics)
                report["fixture_inputs"] = [{"messages": [row for row in request.get("messages", []) if row.get("role") != "system"],
                    "tools": [row.get("function", {}).get("name") for row in request.get("tools", [])]}
                    for request in state.requests[-3:]]
                raise
            finally:
                state.release.set()
                host.close()
            restored = Host(directory, server.server_port)
            try:
                restored.ready()
                restored.login()
                history = restored.json("GET", f"/api/chat/conversations/{conversation}")
                cancelled = next(message for message in history["messages"]
                                 if message.get("request_id") == "fixture-cancel-1")
                replay = restored.request("GET", f"/api/chat/runs/{cancelled['run_id']}/events?after={history['cursor']}")
                terminal_data = next(json.loads(line[5:].strip()) for line in replay.text.splitlines() if line.startswith("data:"))
                assert terminal_data["status"] == "cancelled", terminal_data
                report["checks"].append("重启后以末尾游标恢复旧运行，只返回该运行真实终态，不混入最新任务状态")
                accepted = restored.send(conversation, "SCENARIO_RESTART 请结合之前的对话回答。", "fixture-restart-1")
                assert restored.settled(accepted["run_id"])[0]["status"] == "completed"
                assert "SCENARIO_ARTIFACT" in json.dumps(state.requests[-1].get("messages", []), ensure_ascii=False)
                detail = restored.json("GET", f"/api/chat/conversations/{conversation}")
                assert detail["projections"]["deliveries"]
                report["checks"].append("宿主重启后恢复原生上下文、业务任务与交付记录")
            finally:
                restored.close()
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        report.update(model_requests=len(state.requests), elapsed_seconds=round(time.monotonic() - started, 3))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
