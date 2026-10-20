"""从指定原生版本的部署配置精细组装隔离环境，不读取任何在用 .env。

本工具只生成配置和本地预检；不安装 Docker、不拉镜像、不启动或修改服务。
数据库仅在新 Compose 项目内创建，无宿主机数据库端口，不引用既有 volume。
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parent
SELECTED = {
    "init_permissions", "api", "api_websocket", "worker", "worker_beat", "web",
    "db_postgres", "redis", "sandbox", "local_sandbox", "plugin_daemon",
    "agent_backend", "agent_ssrf_proxy", "ssrf_proxy", "nginx", "weaviate",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root: Path) -> dict:
    """记录源码树摘要；跳过依赖、缓存和非示例环境文件，不读取在用凭据。"""
    excluded = {".git", "node_modules", ".venv", ".venv-runtime", "venv", "__pycache__", ".next", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    entries = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in excluded and not x.startswith(".venv"))
        for name in sorted(files):
            if name.startswith(".env") and not name.endswith(".example"):
                continue
            if name.lower().endswith((".key", ".pem", ".pfx", ".p12", ".pyc")):
                continue
            path = Path(directory) / name
            if path.is_symlink():
                continue
            entries.append((path.relative_to(root).as_posix(), digest(path)))
    packed = "\n".join(f"{name}\0{sha}" for name, sha in sorted(entries))
    return {"sha256": hashlib.sha256(packed.encode()).hexdigest(), "files": len(entries)}


def read_defaults(path: Path) -> dict[str, str]:
    """仅允许版本库示例配置，禁止把在用连接或凭据复制到新环境。"""
    if not path.name.endswith(".example"):
        raise ValueError("只能读取 .example 示例配置")
    result = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                result[key] = value
    return result


def prepare(dify_source: Path, target: Path, dify_port: int, webui_port: int, openwebui_source: Path | None = None) -> dict:
    source = dify_source.resolve()
    target = target.resolve()
    version = json.loads((source / "web/package.json").read_text("utf-8"))["version"]
    if version != "1.17.1":
        raise ValueError(f"Dify 版本不匹配：期望 1.17.1，实际 {version}")
    if target.exists():
        raise ValueError("输出目录已存在；请选择新的空目录，避免覆盖凭据或复用已有数据")
    if target == source or source in target.parents:
        raise ValueError("输出目录不能位于参考项目中")
    if dify_port == webui_port or not all(1024 <= p <= 65535 for p in (dify_port, webui_port)):
        raise ValueError("请为两个服务选择不同的 1024～65535 本机端口")
    docker = source / "docker"
    original = yaml.safe_load((docker / "docker-compose.yaml").read_text("utf-8"))
    lock_path = ROOT / "versions.lock.json"
    if not lock_path.is_file():
        raise ValueError("缺少原生版本锁，不能组装未核验的部署来源")
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text("utf-8"))
        if digest(docker / "docker-compose.yaml") != lock["dify"]["compose_sha256"]:
            raise ValueError("Dify部署源与已核验版本锁不一致，需重新审查后更新版本锁")
        for asset in lock.get("source_assets", []):
            base = source if asset["project"] == "dify" else openwebui_source
            if base is not None and digest(base / asset["path"]) != asset["sha256"]:
                raise ValueError(f"构建资产与锁定来源不一致：{asset['project']}/{asset['path']}")
        for artifact in lock.get("runtime_files", []):
            if digest(docker / artifact["path"]) != artifact["sha256"]:
                raise ValueError(f"部署配置与锁定来源不一致：{artifact['path']}")
        for project_name, base in (("dify", source), ("openwebui", openwebui_source)):
            if base is not None and project_name in lock.get("source_trees", {}):
                if source_fingerprint(base) != lock["source_trees"][project_name]:
                    raise ValueError(f"{project_name}完整源码树摘要已变化，不能将其视为已核验构建")
    services = {name: copy.deepcopy(value) for name, value in original["services"].items() if name in SELECTED}
    if set(services) != SELECTED:
        raise ValueError("原生部署服务清单不匹配，请重新核查版本")
    # 项目名和数据卷前缀每次新建，禁止不知情地接上旧 PostgreSQL / Redis。
    project = "agent-native-" + secrets.token_hex(5)
    compose = {"name": project, "services": services, "networks": copy.deepcopy(original.get("networks", {})), "volumes": {}}
    artifacts: set[Path] = set()
    for name, service in services.items():
        service.pop("container_name", None)
        service.pop("profiles", None)
        service.pop("ports", None)
        if service.get("privileged") or service.get("network_mode") == "host":
            raise ValueError(f"服务 {name} 包含不允许的特权或 host 网络")
        if "env_file" in service:
            service["env_file"] = ["./native.env"]
        if isinstance(service.get("depends_on"), dict):
            service["depends_on"] = {k: v for k, v in service["depends_on"].items() if k in SELECTED}
        elif isinstance(service.get("depends_on"), list):
            service["depends_on"] = [k for k in service["depends_on"] if k in SELECTED]
        volumes = []
        for mount in service.get("volumes", []):
            if not isinstance(mount, str):
                raise ValueError("需要人工核查非字符串格式的数据卷")
            parts = mount.split(":")
            origin = parts[0]
            if origin.startswith("./volumes/") or not origin.startswith("."):
                volume = re.sub(r"[^a-zA-Z0-9_]", "_", origin.removeprefix("./volumes/"))
                compose["volumes"][volume] = {}
                parts[0] = volume
            elif origin.startswith(("./nginx/", "./ssrf_proxy/")):
                artifact = (docker / origin).resolve()
                if docker.resolve() not in artifact.parents:
                    raise ValueError("配置挂载越界")
                if artifact.is_dir():
                    artifacts.update(x for x in artifact.rglob("*") if x.is_file())
                elif artifact.is_file():
                    artifacts.add(artifact)
                elif origin.endswith("/ssl"):
                    pass
                else:
                    raise ValueError(f"缺少原生配置 {origin}")
                # Nginx需要在容器内生成最终配置，因此保留上游模板挂载行为。
            else:
                raise ValueError(f"需要人工核查额外挂载 {origin}")
            volumes.append(":".join(parts))
        service["volumes"] = volumes
    services["nginx"]["ports"] = [f"127.0.0.1:{dify_port}:80"]
    services["nginx"].setdefault("environment", {}).update(NGINX_HTTPS_ENABLED="false", NGINX_PORT="80")
    services["openwebui"] = {
        "image": "ghcr.io/open-webui/open-webui:v0.12.3", "restart": "unless-stopped",
        "ports": [f"127.0.0.1:{webui_port}:8080"], "volumes": ["openwebui_data:/app/backend/data"],
        "environment": {"WEBUI_SECRET_KEY": "${WEBUI_SECRET_KEY:?请运行prepare生成独立密钥}", "WEBUI_AUTH": "true", "ENABLE_SIGNUP": "true", "ENABLE_OLLAMA_API": "false", "ENABLE_OPENAI_API": "false", "HF_HUB_OFFLINE": "1", "RAG_EMBEDDING_MODEL_AUTO_UPDATE": "false"},
        "env_file": ["./native.env"],
    }
    build_sources = {
        "api": (source, "api/Dockerfile"), "api_websocket": (source, "api/Dockerfile"),
        "worker": (source, "api/Dockerfile"), "worker_beat": (source, "api/Dockerfile"),
        "web": (source, "web/Dockerfile"), "agent_backend": (source, "dify-agent/Dockerfile"),
        "local_sandbox": (source / "dify-agent-runtime", "docker/Dockerfile"),
    }
    if openwebui_source is not None:
        webui_source = openwebui_source.resolve()
        webui_version = json.loads((webui_source / "package.json").read_text("utf-8"))["version"]
        if webui_version != "0.12.3":
            raise ValueError("Open WebUI版本必须是已核验的0.12.3")
        build_sources["openwebui"] = (webui_source, "Dockerfile")
    # 另提供显式源码构建override；它不会在生成时执行，避免同版本标签掩盖本地改动。
    source_override = {"services": {}}
    for service_name, (context, dockerfile) in build_sources.items():
        source_override["services"][service_name] = {"image": f"{project}-{service_name}:source-locked", "build": {"context": context.as_posix(), "dockerfile": dockerfile}}
    compose["volumes"]["openwebui_data"] = {}
    used_networks = {key for service in services.values() for key in service.get("networks", [])}
    compose["networks"] = {k: v for k, v in compose["networks"].items() if k in used_networks}
    env = read_defaults(docker / ".env.example")
    # 仅填写新项目内部服务名；不引用宿主机/局域网/远程数据库。
    env.update({
        "DB_TYPE": "postgresql", "DB_HOST": "db_postgres", "DB_PORT": "5432", "DB_DATABASE": "dify",
        "DB_PLUGIN_DATABASE": "dify_plugin", "REDIS_HOST": "redis", "REDIS_PORT": "6379",
        "VECTOR_STORE": "weaviate", "WEAVIATE_ENDPOINT": "http://weaviate:8080", "COMPOSE_PROFILES": "",
        "EXPOSE_NGINX_PORT": str(dify_port), "EXPOSE_NGINX_SSL_PORT": "14443", "NGINX_HTTPS_ENABLED": "false",
        "CONSOLE_API_URL": f"http://127.0.0.1:{dify_port}", "CONSOLE_WEB_URL": f"http://127.0.0.1:{dify_port}",
        "APP_WEB_URL": f"http://127.0.0.1:{dify_port}", "APP_API_URL": f"http://127.0.0.1:{dify_port}",
        "SERVICE_API_URL": f"http://127.0.0.1:{dify_port}", "NEXT_PUBLIC_SOCKET_URL": f"ws://127.0.0.1:{dify_port}",
        "TRIGGER_URL": f"http://127.0.0.1:{dify_port}", "ENDPOINT_URL_TEMPLATE": f"http://127.0.0.1:{dify_port}/e/{{hook_id}}",
        "NEXT_TELEMETRY_DISABLED": "1", "CHECK_UPDATE_URL": "", "SERVER_CONSOLE_API_URL": "http://api:5001",
    })
    for key in ("DB_PASSWORD", "REDIS_PASSWORD", "SECRET_KEY", "SANDBOX_API_KEY", "PLUGIN_DAEMON_KEY", "PLUGIN_DIFY_INNER_API_KEY", "DIFY_AGENT_API_TOKEN", "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN", "WEBUI_SECRET_KEY"):
        env[key] = secrets.token_urlsafe(32)
    env["DIFY_AGENT_SERVER_SECRET_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    # Celery显式替换示例密码，保持与该项目内部 Redis 一致。
    env["CELERY_BROKER_URL"] = f"redis://:{env['REDIS_PASSWORD']}@redis:6379/1"
    env["DIFY_AGENT_REDIS_URL"] = f"redis://:{env['REDIS_PASSWORD']}@redis:6379/2"
    target.mkdir(parents=True)
    manifest = {"project": project, "dify_version": version, "openwebui_version": "0.12.3", "source_compose_sha256": digest(docker / "docker-compose.yaml"), "ports": {"dify": dify_port, "openwebui": webui_port}, "files": [], "images": {k: v["image"] for k, v in services.items()}, "database": {"postgres": "新建Compose服务db_postgres:5432/dify", "openwebui": "新建项目独立volume内SQLite"}, "started": False}
    for artifact in sorted(artifacts):
        destination = target / artifact.relative_to(docker)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact, destination)
        manifest["files"].append({"path": artifact.relative_to(docker).as_posix(), "sha256": digest(artifact)})
    (target / "nginx/ssl").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "LICENSE", target / "Dify-LICENSE")
    (target / "compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False, allow_unicode=True), "utf-8")
    (target / "compose.source.yaml").write_text(yaml.safe_dump(source_override, sort_keys=False, allow_unicode=True), "utf-8")
    (target / "native.env").write_text("\n".join(f"{key}={value}" for key, value in env.items()) + "\n", "utf-8")
    manifest["generated_sha256"] = {name: digest(target / name) for name in ("compose.yaml", "compose.source.yaml", "native.env")}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest


def runtime_tools() -> list[dict]:
    """只执行本机版本命令，不自动切换或安装解释器。"""
    versions = [{"tool": "current_python", "path": sys.executable, "version": sys.version.split()[0]}]
    for tool in ("node", "pnpm", "uv", "docker", "podman"):
        executable = shutil.which(tool)
        if not executable:
            versions.append({"tool": tool, "available": False})
            continue
        try:
            found = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5)
            versions.append({"tool": tool, "path": executable, "available": found.returncode == 0, "version": found.stdout.strip()[:200]})
        except (OSError, subprocess.TimeoutExpired):
            versions.append({"tool": tool, "path": executable, "available": False})
    return versions


def preflight(target: Path) -> dict:
    """只查本地配置、端口占用与Docker可用性，不查询任何数据库。"""
    manifest = json.loads((target / "manifest.json").read_text("utf-8"))
    compose = yaml.safe_load((target / "compose.yaml").read_text("utf-8"))
    failures, warnings = [], []
    for service_name, service in compose["services"].items():
        for port in service.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                failures.append(f"{service_name} 暴露了非回环端口")
        if service_name in {"db_postgres", "redis", "weaviate"} and service.get("ports"):
            failures.append(f"{service_name} 不应暴露宿主机数据库端口")
        for mount in service.get("volumes", []):
            if str(mount).startswith(("/", "../")):
                failures.append(f"{service_name} 包含未核验的外部挂载")
        image = service.get("image", "")
        if image.endswith(":latest") or ":" not in image.split("/")[-1]:
            warnings.append(f"{service_name} 沿用原生浮动镜像，生产须锁定经验证的digest")
        if image.endswith("-local"):
            warnings.append(f"{service_name} 需要对应原生本地构建镜像；不得替换成不匹配版本")
    for port in manifest["ports"].values():
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", int(port)))
            except OSError:
                failures.append(f"本机端口 {port} 已占用")
    docker = shutil.which("docker")
    if not docker:
        failures.append("未安装Docker，配置尚未运行")
    else:
        completed = subprocess.run([docker, "compose", "version"], capture_output=True, text=True, timeout=10)
        if completed.returncode:
            failures.append("Docker Compose不可用")
        else:
            completed = subprocess.run([docker, "compose", "--env-file", str(target / "native.env"), "-f", str(target / "compose.yaml"), "config", "--quiet"], capture_output=True, text=True, timeout=15)
            if completed.returncode:
                # 不输出compose完整配置，避免随机生成的密钥流入日志。
                failures.append("Compose配置校验失败，请在本机核对错误，不发布密钥")
    versions = runtime_tools()
    return {"project": manifest["project"], "ready": not failures, "failures": failures, "warnings": warnings, "host_tools": versions, "source_requirements": {"dify": {"python": "~=3.13.0", "node": "^24.20.0", "pnpm": "12.10"}, "openwebui": {"python": ">=3.11,<3.13", "node": ">=20.16,<=22"}}, "services_started": False, "database_queries": 0}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="组装和预检独立原生环境，不启动服务")
    parser.add_argument("--dify-source", type=Path)
    parser.add_argument("--openwebui-source", type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--dify-port", type=int, default=12080)
    parser.add_argument("--webui-port", type=int, default=13000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        result = preflight(args.target.resolve())
    else:
        if not args.dify_source:
            parser.error("生成配置需要显式指定 --dify-source")
        result = prepare(args.dify_source, args.target, args.dify_port, args.webui_port, args.openwebui_source)
        result = {"project": result["project"], "target": str(args.target.resolve()), "services": len(result["images"]), "started": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.check and not result.get("ready", False):
        raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit('R25已允许D盘Docker安装。历史全栈准备入口仍停用：需先落实D盘新项目、精确镜像和分组资源预算；安装入口为deploy/docker_desktop.ps1。')
