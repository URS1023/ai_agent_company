"""真实 Host/Worker 冷恢复：一次性结算故障、本机模型桩和独立 SQLite。"""
from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import threading


ROOT = Path(__file__).resolve().parents[2]


def _smoke_module():
    """复用已有隔离 Host 管理，不启动任何在用服务。"""
    spec = importlib.util.spec_from_file_location("completion_smoke_support", ROOT / "tests/dsh/real_platform_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_handler(calls):
    """只接受本用例两个标记，调用上限能识别意外重发。"""
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            """访问计数单独保存，不打印请求头。"""

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            users = [message for message in payload["messages"] if message["role"] == "user"]
            # 原生模型适配层会在用户消息后补充技能目录，按明确夹具标记选择真实请求。
            texts = [message["content"] if isinstance(message["content"], str) else
                "".join(item.get("text", "") for item in message["content"] if item["type"] == "text") for message in users]
            text = next((item for item in reversed(texts) if item in {"completion-window-A", "completion-window-B"}), "")
            calls.append(text)
            answer = {"completion-window-A": "FIXTURE_A_FINAL", "completion-window-B": "FIXTURE_B_FINAL"}.get(text)
            if answer is None or len(calls) > 4:
                self.send_error(500, "fixture request not allowed")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for delta, reason in [({"role": "assistant", "content": answer}, None), ({}, "stop")]:
                    value = {"id": "completion-fixture", "object": "chat.completion.chunk", "created": 1,
                        "model": "fixture-model", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Model


def test_real_host_recovers_only_its_own_request_after_lost_settlement(tmp_path):
    """真实生成A/B原生日志，仅A第一次结算失败；重启不得复用B正文或请求新模型。"""
    support = _smoke_module()
    calls, hosts = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _model_handler(calls))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fault_log = tmp_path / "settle-fault.jsonl"
    record = {"status": "running", "fixture_directory": str(tmp_path), "model_requests": calls}
    evidence_dir = ROOT / ".implementation/dsh-completion-batch14"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = evidence_dir / ("host-" + tmp_path.parent.name + "-" + tmp_path.name + ".json")
    try:
        options = "--import=" + (ROOT / "tests/dsh/local_network_only.mjs").as_uri()
        options += " --import=" + (ROOT / "tests/dsh/completion_settle_fault.mjs").as_uri()
        host = support.Host(tmp_path, server.server_port, env_overrides={"NODE_OPTIONS": options,
            "DSH_COMPLETION_FAULT": "1", "DSH_COMPLETION_FAULT_LOG": str(fault_log)})
        hosts.append(host)
        host.ready()
        host.login()
        host.json("POST", "/api/models/profiles", status=201, json={"profile_name": "fixture", "provider": "openai-compatible",
            "model": "fixture-model", "endpoint": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "fixture-only-key",
            "capabilities": ["chat", "tools"], "runtime_options": {"protocol": "openai-completions", "thinking": "disabled",
                "context_window": 32768, "max_output_tokens": 512}})
        conversation = host.json("POST", "/api/chat/conversations", status=201, json={"title": "隔离恢复窗口"})["id"]
        first = host.send(conversation, "completion-window-A", "completion-client-A")
        first_done, first_events = host.settled(first["run_id"])
        assert first_done["status"] == "failed", first_events
        assert any(value.get("error", {}).get("code") == "DSH_SETTLE_FAILED" for _, value in first_events)
        faults = [json.loads(line) for line in fault_log.read_text(encoding="utf-8").splitlines()]
        assert faults == [{"run_id": first["run_id"], "method": "chat.settle", "forwarded": False}]
        second = host.send(conversation, "completion-window-B", "completion-client-B")
        assert host.settled(second["run_id"])[0]["status"] == "completed"
        with sqlite3.connect(tmp_path / "platform.sqlite") as database:
            before = dict(database.execute("SELECT id,status FROM runs WHERE id IN (?,?)", (first["run_id"], second["run_id"])))
            assert before == {first["run_id"]: "running", second["run_id"]: "completed"}
            assert database.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
        record.update(run_a=first["run_id"], run_b=second["run_id"], before=before, fault=faults)
        host.close()
        logs = list((tmp_path / "dsh").rglob("*.jsonl"))
        native = [json.loads(line) for path in logs for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        native_events = [item for item in native if item.get("type") in {"user/message", "assistant/message", "turn/end"}]
        assert sum(item["type"] == "turn/end" for item in native_events) >= 2
        assert all(any(item.get("data", {}).get("source", {}).get("rpcId") == identity for item in native_events)
                   for identity in (first["run_id"], second["run_id"]))
        record["native_events"] = native_events
        assert calls == ["completion-window-A", "completion-window-B"]
        restored = support.Host(tmp_path, server.server_port)
        hosts.append(restored)
        restored.ready()
        restored.login()
        history = restored.json("GET", f"/api/chat/conversations/{conversation}")
        messages = {item["run_id"]: item for item in history["messages"] if item["role"] == "assistant"}
        record["after"] = {identity: {key: messages[identity][key] for key in ("content", "status")}
                           for identity in (first["run_id"], second["run_id"])}
        assert messages[first["run_id"]]["content"] == "FIXTURE_A_FINAL"
        assert messages[second["run_id"]]["content"] == "FIXTURE_B_FINAL"
        assert all(messages[identity]["status"] == "completed" for identity in (first["run_id"], second["run_id"]))
        assert calls == ["completion-window-A", "completion-window-B"], "恢复不能再调用模型"
        with sqlite3.connect(tmp_path / "platform.sqlite") as database:
            assert database.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
        record["status"] = "passed"
    except BaseException as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        failures = []
        for host in reversed(hosts):
            try:
                if host.process.poll() is None:
                    host.close()
            except Exception as exc:
                failures.append(str(exc))
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        record["host_exit_codes"] = [host.process.poll() for host in hosts]
        record["diagnostics"] = [list(host.diagnostics) for host in hosts]
        record["cleanup_failures"] = failures
        evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not failures and all(host.process.poll() is not None for host in hosts), "本轮宿主必须全部回收"
