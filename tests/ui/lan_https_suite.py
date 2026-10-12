"""LAN deployment read checks and isolated HTTPS browser business acceptance.

Never stop or reconfigure the running LAN service. Business write suites use
separate temporary databases/storage and localhost HTTPS listeners. Model and
device fixtures in those suites do not prove production provider availability.

中文说明（局域网部署验收：只读检查 + 隔离 HTTPS 浏览器业务验收）：
用途：对已在运行的 LAN 服务做只读检查，并在独立的临时库/存储与 localhost HTTPS 监听上跑浏览器业务用例。
运行方式：用项目 venv 执行本文件（可通过命令行参数选择要跑的子集），需要本机浏览器与该 LAN 服务已在运行。
硬性约束：绝不停止或重新配置正在运行的 LAN 服务；写操作只发生在临时数据库/存储与 localhost 监听上，
因此用例里的模型与设备夹具不能证明生产提供方可用。
"""
import argparse
import json
import os
import random
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITES = ["analytics", "content", "canvas", "connections", "interactions", "device", "graph", "capability", "ai_documents", "system"]
SUITE_SCRIPTS = {"canvas": "lan_workflow_canvas.js", "connections": "workflow_connection_gestures.js", "interactions": "workflow_canvas_interactions.js", "device": "device_workflow_inline_resources.js"}


def request(url, ca):
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))),
    )
    with client.open(url, timeout=10) as response:
        return response.status, response.read()


def browser_run(script, env, output):
    command = ["node", "--require", str(ROOT / "tests/ui/https_chrome_setup.cjs"), str(ROOT / "tests/ui" / script)]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    with output.open("wb") as log:
        for line in iter(process.stdout.readline, b""):
            log.write(line)
            log.flush()
            print(line.decode("utf-8", errors="replace").rstrip(), flush=True)
    return process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["live", "isolated", "runtime", "chat"])
    parser.add_argument("--url", default="https://192.168.6.16:8443")
    parser.add_argument("--suite", choices=SUITES)
    args = parser.parse_args()
    output = ROOT / ".implementation/ui-results" / ("lan-https-" + args.mode + "-" + str(time.time_ns()) + "-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    certs = ROOT / ".implementation/lan-https"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TEST5_EDGE_PATH": "C:/Program Files/Google/Chrome/Application/chrome.exe",
           "TEST5_E2E_TLS_CERTFILE": str(certs / "server.crt")}
    results = []
    if args.mode in {"live", "runtime", "chat"}:
        for route in ("/ai", "/api/health", "/api/ready"):
            status, _ = request(args.url + route, certs / "root-ca.crt")
            assert status == 200
            print("PASS verified TLS " + route, flush=True)
        sys.path.insert(0, str(ROOT / "backend"))
        from app.config import settings
        env.update(TEST5_E2E_BASE_URL=args.url, TEST5_E2E_PASSWORD=settings.BOOTSTRAP_ADMIN_PASSWORD,
                   TEST5_E2E_ALLOW_LAN_READONLY="1")
        script = {"live": "full_browser_route_audit.js", "runtime": "lan_runtime_audit.js", "chat": "lan_live_chat.js"}[args.mode]
        code = browser_run(script, env, output / "browser.log")
        results.append({"suite": args.mode, "exit": code})
    else:
        for suite in ([args.suite] if args.suite else SUITES):
            folder = output / suite
            folder.mkdir()
            # Windows may allocate low ephemeral ports blocked by Chrome.
            for _ in range(100):
                port = random.randrange(20000, 60000)
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", port))
                        break
                    except OSError:
                        continue
            else:
                raise RuntimeError("No available browser-safe test port")
            base = f"https://127.0.0.1:{port}"
            isolated_env = {**env, "HOST": "127.0.0.1", "PORT": str(port), "APP_ENV": "test",
                "DATABASE_URL": "sqlite:///" + (folder / "test.sqlite").as_posix(),
                "LOCAL_STORAGE_ROOT": str(folder / "storage"), "BOOTSTRAP_ADMIN_PASSWORD": "IsolatedLanHttps12345",
                "BOOTSTRAP_LYNX_REPORTS": "0", "SCHEDULER_ENABLED": "0", "AUTO_MIGRATE": "1",
                "SSL_CERTFILE": str(certs / "server.crt"), "SSL_KEYFILE": str(certs / "server.key"),
                "ES_URL": "http://127.0.0.1:1", "REDIS_URL": "redis://127.0.0.1:1/0",
                "TEST5_E2E_BASE_URL": base, "TEST5_E2E_PASSWORD": "IsolatedLanHttps12345", "TEST5_E2E_ALLOW_WRITE": "isolated",
                "TEST5_E2E_OUTPUT": str(folder / "evidence")}
            print("START " + suite, flush=True)
            with (folder / "server.log").open("w", encoding="utf-8") as log:
                server = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT / "backend", env=isolated_env,
                    stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    deadline = time.monotonic() + 90
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError("Isolated server stopped; inspect " + str(folder / "server.log"))
                        try:
                            status, body = request(base + "/api/health", certs / "root-ca.crt")
                            assert status == 200 and json.loads(body)["app"] == "test"
                            break
                        except OSError:
                            if time.monotonic() > deadline:
                                raise
                            time.sleep(0.5)
                    script = SUITE_SCRIPTS.get(suite, "full_browser_" + suite + "_acceptance.js")
                    code = browser_run(script, isolated_env, folder / "browser.log")
                    results.append({"suite": suite, "exit": code})
                finally:
                    server.terminate()
                    server.wait(timeout=20)
            (output / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (output / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("REPORT " + str(output), flush=True)
    return int(any(item["exit"] != 0 for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
