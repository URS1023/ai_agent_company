"""真实启动项目内 DSH，使用回环模型桩验证工具、持久化与跨进程恢复，不接业务库。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import queue
import subprocess
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
requests_seen = []


class FixtureModel(BaseHTTPRequestHandler):
    """固定返回一轮工具调用和后续文字，验证传输行为而非模型智力。"""

    def log_message(self, *args):
        """不打印 HTTP 桩的例行访问日志。"""

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests_seen.append(payload)
        number = len(requests_seen)
        if number > 4:
            self.send_error(429, "offline request budget exceeded")
            return
        if number == 1:
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "fixture-call", "type": "function",
                     "function": {"name": "lookup_fixture", "arguments": '{"value":7}'}}]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "本地验证完成，结果为7。"}
            finish = "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for body, reason in [(delta, None), ({}, finish)]:
            chunk = {"id": "fixture-completion", "object": "chat.completion.chunk", "created": 1,
                     "model": "fixture-model", "choices": [{"index": 0, "delta": body, "finish_reason": reason}]}
            if reason:
                chunk["usage"] = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class NativeProcess:
    """测试驱动仅代理模拟工具和模型放行，不导入平台应用。"""

    def __init__(self, home, port, resume):
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                   "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update({"DSH_HOME": str(home), "DSH_UPSTREAM_ROOT": str(ROOT / ".implementation/harness_migration/upstream"),
                    "DSH_TELEMETRY_DISABLED": "1", "DSH_PRIMARY_RUNTIME": "", "DSH_AGENTS_HOME": str(home / "disabled")})
        self.events = queue.Queue()
        self.diagnostics = []
        self.frames = []
        self.tools = 0
        self.started = time.monotonic()
        self.process = subprocess.Popen(["node", "--import", (OUT / "local-network-only.mjs").as_uri(),
            str(ROOT / "services/deepseek_harness/launch.mjs")], env=env, cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self.read_output, daemon=True).start()
        threading.Thread(target=self.read_errors, daemon=True).start()
        self.send({"id": "initialize", "method": "initialize", "params": {
            "sessionId": "offline-review-session", "cwd": str(home / "workspace"), "resumeExpected": resume,
            "model": {"protocol": "openai-completions", "baseURL": f"http://127.0.0.1:{port}/v1",
                      "model": "fixture-model", "apiKey": "offline-fixture", "contextWindow": 32768, "maxTokens": 4096},
            "systemPrompt": "这是离线传输验证，只处理模拟输入。", "tools": [{"name": "lookup_fixture",
             "description": "返回模拟数字", "parameters": {"type": "object", "properties": {"value": {"type": "number"}}, "required": ["value"]}}]}})

    def read_output(self):
        """将管道消息送入带超时的验证循环。"""
        for line in self.process.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                self.events.put({"invalid_protocol": line[:300]})
        self.events.put({"process_closed": True})

    def read_errors(self):
        """只保存有限诊断供失败时定位，不读取任何现有业务日志。"""
        for line in self.process.stderr:
            if len(self.diagnostics) < 30:
                self.diagnostics.append(line[:300])

    def send(self, packet):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **packet}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def until(self, predicate, timeout=30):
        """所有阶段有固定总预算，失败立即退出，不自动重试。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            if item.get("process_closed") or item.get("invalid_protocol") or "error" in item:
                raise RuntimeError(json.dumps({"packet": item, "diagnostics": self.diagnostics}, ensure_ascii=False))
            method = item.get("method")
            if method == "model.authorize":
                self.send({"id": item["id"], "result": {"allowed": True}})
            elif method == "tool.invoke":
                self.tools += 1
                self.send({"id": item["id"], "result": {"status": "success", "data": {"total": 7}, "artifacts": []}})
            elif method == "session.frame":
                self.frames.append(item["params"]["frame"])
            elif method == "runtime.error":
                raise RuntimeError(str(item))
            if predicate(item):
                return item
        raise TimeoutError("本地验证阶段超时")

    def prompt(self, request_id, text):
        self.send({"id": request_id, "method": "session.prompt", "params": {
            "sessionId": "offline-review-session", "requestId": request_id,
            "content": [{"type": "text", "text": text}], "mode": "queue"}})
        return self.until(lambda x: x.get("method") == "run.settled" and x["params"]["requestId"] == request_id)["params"]

    def stop(self):
        """退出桩进程；即使验证失败也不留下后台服务。"""
        if self.process.poll() is None:
            try:
                self.send({"id": "shutdown", "method": "shutdown", "params": {}})
                self.process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            try:
                stream.close()
            except OSError:
                # 子进程提前退出时，Windows 已关闭的管道可能报告无效句柄。
                pass


def main():
    """验证一次工具闭环、冷恢复和重复请求收据。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureModel)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    result = {"scope": "real DSH subprocess + local fixture model; no database or external model", "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="dsh-functional-review-") as temporary:
            home = Path(temporary)
            first = NativeProcess(home, server.server_port, False)
            try:
                initialized = first.until(lambda x: x.get("id") == "initialize")
                result["cold_start_seconds"] = round(time.monotonic() - first.started, 3)
                outcome = first.prompt("request-1", "查询模拟数据并回答。标记 FIRST_TURN_MARKER")
                assert outcome["status"] == "completed", outcome
                assert first.tools == 1, first.tools
                assert len(requests_seen) == 2, len(requests_seen)
                assert any(m.get("role") == "tool" for m in requests_seen[1]["messages"])
                result["checks"].append("真实工具回调、回传模型、流式提交与终态完成")
                result["first_turn_event_types"] = sorted({f["event"]["type"] for f in first.frames if f.get("type") == "event"})
            finally:
                first.stop()
            second = NativeProcess(home, server.server_port, True)
            try:
                second.until(lambda x: x.get("id") == "initialize")
                result["resume_start_seconds"] = round(time.monotonic() - second.started, 3)
                outcome = second.prompt("request-2", "继续上一轮模拟任务。")
                assert outcome["status"] == "completed", outcome
                assert "FIRST_TURN_MARKER" in json.dumps(requests_seen[-1]["messages"])
                result["checks"].append("关闭进程后重启恢复原生会话，历史进入下一轮模型请求")
                before = len(requests_seen)
                duplicate = second.prompt("request-1", "相同请求编号")
                assert duplicate["status"] == "completed" and len(requests_seen) == before
                result["checks"].append("冷恢复后重复请求编号只返回已完成收据，不重复模型或工具调用")
            finally:
                second.stop()
        result["model_requests"] = len(requests_seen)
        result["database_connections"] = 0
        result["external_model_requests"] = 0
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise
    finally:
        server.shutdown()
        server.server_close()
        (OUT / "native-local-smoke-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
