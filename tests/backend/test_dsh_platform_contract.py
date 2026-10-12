"""验证插件路由闭包、宿主启动边界与代理的首事件流式交付。"""
from __future__ import annotations

import ast
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    """通过文件加载启动辅助代码，避免导入业务应用生命周期。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_catalog_preserves_every_business_route_and_tool():
    catalog = json.loads((ROOT / "configurations/business_apps.json").read_text(encoding="utf-8"))
    plugins = catalog["plugins"]
    ids = {plugin["id"] for plugin in plugins}
    assert len(ids) == len(plugins)
    by_id = {plugin["id"]: plugin for plugin in plugins}

    def dependency_tools(identity, seen=None):
        """Skill 可以组合依赖插件的工具，但不能凭名称引用未安装应用。"""
        seen = set() if seen is None else seen
        assert identity not in seen, "业务插件依赖形成循环"
        plugin = by_id[identity]
        names = set(plugin["tools"])
        for dependency in plugin["dependencies"]:
            names |= dependency_tools(dependency, seen | {identity})
        return names
    modules = [name for plugin in plugins for name in plugin["router_modules"]]
    assert len(modules) == len(set(modules))
    baseline = json.loads((ROOT / "docs/code-audit-20260926/registered-functions.json").read_text(encoding="utf-8"))
    expected = {row["file"].removeprefix("backend/app/").removesuffix(".py").replace("/", ".")
                for row in baseline if row["file"].startswith("backend/app/modules/")}
    assert set(modules) == expected
    for plugin in plugins:
        assert set(plugin["dependencies"]) <= ids
        assert plugin["id"] not in plugin["dependencies"]
        prefixes = plugin["route_prefixes"]
        owned = {"backend/app/" + name.replace(".", "/") + ".py" for name in plugin["router_modules"]}
        for route in baseline:
            if route["file"] in owned:
                assert any(route["path"] == prefix or route["path"].startswith(prefix + "/") for prefix in prefixes), route
        for skill in plugin["skills"]:
            document = (ROOT / skill["path"]).read_text(encoding="utf-8")
            assert f"name: {skill['id']}" in document
            assert set(skill["tools"]) <= dependency_tools(plugin["id"])
    declared_tools = [tool for plugin in plugins for tool in plugin["tools"]]
    assert len(declared_tools) == len(set(declared_tools))
    tree = ast.parse((ROOT / "backend/app/providers/tool/builtin_provider.py").read_text(encoding="utf-8-sig"))
    actual = {node.args[0].value for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_tool"}
    assert set(declared_tools) == actual
    completed = set()
    while completed != ids:
        ready = {plugin["id"] for plugin in plugins if set(plugin["dependencies"]) <= completed}
        assert ready - completed, "业务插件依赖形成循环"
        completed |= ready


def test_launcher_uses_dsh_and_preserves_explicit_binding(monkeypatch):
    launcher = load_module("dsh_launcher", ROOT / "backend/run.py")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "0")
    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    command, env = launcher.host_command()
    assert command[1].replace("\\", "/").endswith("services/dsh_host/launch.mjs")
    assert env["DSH_PYTHON"]
    assert env["HOST"] == "127.0.0.1" and env["PORT"] == "0"
    assert "uvicorn" not in (ROOT / "backend/run.py").read_text(encoding="utf-8")
    monkeypatch.setenv("SSL_CERTFILE", "missing.pem")
    with pytest.raises(RuntimeError, match="同时"):
        launcher.host_command()


def test_launcher_shutdown_uses_control_channel():
    launcher = load_module("dsh_launcher_shutdown", ROOT / "backend/run.py")
    # 使用最小本机进程检验关闭协议，不启动业务 Worker 或数据库。
    process = subprocess.Popen([
        launcher.shutil.which("node"), "--input-type=module", "-e",
        "process.stdin.setEncoding('utf8'); process.stdin.on('data', s => {if(JSON.parse(s).method==='shutdown') process.exit(0)});",
    ], stdin=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        launcher.stop_host(process)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_opencode_proxy_streams_first_event_before_upstream_finishes():
    proxy_module = load_module("dsh_proxy", ROOT / "services/opencode_proxy.py")
    release = threading.Event()
    captured = []

    class Upstream(BaseHTTPRequestHandler):
        """首事件发出后等待测试端放行，证明代理没有等待完整响应。"""
        def do_POST(self):
            captured.append({"headers": dict(self.headers), "body": json.loads(
                self.rfile.read(int(self.headers["Content-Length"])))})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"content":"first"}\n\n')
            self.wfile.flush()
            release.wait(3)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args):
            """测试不记录 HTTP 日志。"""

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy_handler = type("LocalProxy", (proxy_module.OpenCodeProxyHandler,), {
        "_target": f"http://127.0.0.1:{upstream.server_port}/v1", "_timeout": 5})
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), proxy_handler)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (upstream, proxy)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=1.5)
    try:
        client.request("POST", "/v1/chat/completions", json.dumps({"model": "fixture", "thinking": {"type": "enabled"}}),
                       {"Content-Type": "application/json"})
        response = client.getresponse()
        assert response.status == 200
        assert response.readline() == b'data: {"content":"first"}\n'
        assert not release.is_set()
        assert captured[0]["body"]["thinking"] == {"type": "enabled"}
        assert captured[0]["headers"]["x-opencode-session"]
        release.set()
        assert b"[DONE]" in response.read()
    finally:
        release.set()
        client.close()
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)
