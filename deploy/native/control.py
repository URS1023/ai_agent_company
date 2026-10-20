"""新建原生环境的运行计划、归属预检和有限健康验收；默认不执行操作。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import httpx

ROOT = Path(__file__).resolve().parent


def run(command: list[str], *, timeout: int = 15) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Docker命令错误可能包含完整配置，只报告命令类型，不输出潜在凭据。
        raise RuntimeError(f"本机容器命令失败：{command[1] if len(command) > 1 else 'docker'}")
    return result.stdout.strip()


def read_manifest(target: Path) -> dict:
    target = target.resolve()
    manifest = json.loads((target / "manifest.json").read_text("utf-8"))
    if not re.fullmatch(r"agent-native-[0-9a-f]{10}", manifest.get("project", "")):
        raise ValueError("不是本工具生成的独立项目")
    for name, expected in manifest.get("generated_sha256", {}).items():
        if Path(name).name != name or hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
            raise ValueError("运行配置或密钥文件已变化，请人工核对后重新生成独立环境")
    if set(manifest.get("generated_sha256", {})) != {"compose.yaml", "compose.source.yaml", "native.env"}:
        raise ValueError("运行配置缺少完整摘要，请使用当前prepare工具生成新的独立目录")
    return manifest


def docker_command() -> list[str]:
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError("未安装Docker，本工具不会安装系统组件")
    # 强制显式本机context，不接受tcp/ssh端点或环境覆盖，防止测试落到外部daemon。
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise RuntimeError("请先清除Docker端点环境覆盖，并核验本机context")
    context = run([executable, "context", "show"])
    host = json.loads(run([executable, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}", context]))
    if not str(host).startswith(("npipe:////./pipe/", "unix:///")):
        raise RuntimeError("容器daemon不是可核验的本机管道，禁止连接测试")
    command = [executable, "--context", context]
    run(command + ["info", "--format", "{{.ServerVersion}}"])
    return command


def compose_command(docker: list[str], target: Path) -> list[str]:
    return docker + ["compose", "--env-file", str(target / "native.env"), "-f", str(target / "compose.yaml")]


def project_containers(docker: list[str], project: str) -> list[str]:
    return run(docker + ["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).split()


def local_bind_matches(source: str, target: Path) -> bool:
    """识别DockerDesktop明确的Windows盘符映射；其余未知路径保持拒绝。"""
    normalized = source.replace("\\", "/").rstrip("/")
    expected = target.resolve().as_posix().rstrip("/")
    if "/../" in normalized or normalized.endswith("/.."):
        return False
    candidates = [expected]
    if re.match(r"^[A-Za-z]:/", expected):
        drive_path = expected[0].lower() + expected[2:]
        candidates.extend(["/run/desktop/mnt/host/" + drive_path, "/host_mnt/" + drive_path])
    return any(normalized.lower() == candidate.lower() or normalized.lower().startswith(candidate.lower() + "/") for candidate in candidates)


def verify_ownership(docker: list[str], manifest: dict, target: Path) -> list[str]:
    project = manifest["project"]
    containers = project_containers(docker, project)
    if not containers:
        raise RuntimeError("该独立项目尚未启动，不向未知服务发送健康请求")
    services = set()
    for container in containers:
        labels = json.loads(run(docker + ["inspect", "--format", "{{json .Config.Labels}}", container]))
        if labels.get("com.docker.compose.project") != project:
            raise RuntimeError("容器归属不匹配")
        service_name = labels.get("com.docker.compose.service")
        services.add(service_name)
        bindings = json.loads(run(docker + ["inspect", "--format", "{{json .HostConfig.PortBindings}}", container])) or {}
        if service_name in {"nginx", "openwebui"}:
            container_port, manifest_key = ("80/tcp", "dify") if service_name == "nginx" else ("8080/tcp", "openwebui")
            if bindings.get(container_port) != [{"HostIp": "127.0.0.1", "HostPort": str(manifest["ports"][manifest_key])}]:
                raise RuntimeError("容器端口与本机归属清单不一致，停止健康验收")
        elif any(bindings.values()):
            raise RuntimeError("内部服务意外发布宿主端口，停止健康验收")
        for mount in json.loads(run(docker + ["inspect", "--format", "{{json .Mounts}}", container])):
            if mount.get("Type") == "volume" and not mount.get("Name", "").startswith(project + "_"):
                raise RuntimeError("发现非本项目数据卷，停止健康验收")
            if mount.get("Type") == "bind":
                if not local_bind_matches(mount["Source"], target):
                    raise RuntimeError("无法核验配置挂载属于本目录，停止健康验收")
    required = {"api", "web", "nginx", "db_postgres", "redis", "openwebui"}
    if not required <= services:
        raise RuntimeError("独立项目核心服务不完整")
    return sorted(services)


def execute_start(target: Path, manifest: dict) -> dict:
    docker = docker_command()
    project = manifest["project"]
    if project_containers(docker, project):
        raise RuntimeError("该项目已有容器；本工具拒绝将首次启动混同旧环境恢复")
    if run(docker + ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]):
        raise RuntimeError("该项目已有数据卷；请核对保留需求，不自动复用或删除")
    # 启动器不拉取和构建镜像；先确认每个指定镜像已在本机提供。
    missing = []
    for image in sorted(set(manifest["images"].values())):
        try:
            run(docker + ["image", "inspect", "--format", "{{.Id}}", image])
        except RuntimeError:
            missing.append(image)
    if missing:
        return {"started": False, "missing_images": missing, "action": "先按版本锁拉取或构建明确的镜像，不能替换成任意latest"}
    from prepare import preflight
    result = preflight(target)
    if not result["ready"]:
        return {"started": False, "preflight": result}
    run(compose_command(docker, target) + ["up", "-d", "--no-build", "--pull", "never"], timeout=60)
    return {"started": True, "project": project, "database": manifest["database"], "models_called": 0}


def smoke(target: Path, manifest: dict) -> dict:
    docker = docker_command()
    services = verify_ownership(docker, manifest, target)
    checks = []
    urls = [
        ("dify_console_ping", f"http://127.0.0.1:{manifest['ports']['dify']}/console/api/ping"),
        ("openwebui_health", f"http://127.0.0.1:{manifest['ports']['openwebui']}/health"),
        ("dify_frontend", f"http://127.0.0.1:{manifest['ports']['dify']}/"),
        ("openwebui_frontend", f"http://127.0.0.1:{manifest['ports']['openwebui']}/"),
    ]
    # 每条请求只发一次，5秒超时；不用/ready或/health/db触发隐式数据库查询。
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        for name, url in urls:
            response = client.get(url)
            checks.append({"check": name, "status": response.status_code, "passed": response.status_code == 200})
            if response.status_code != 200:
                break
    return {"project": manifest["project"], "services": services, "passed": len(checks) == 4 and all(x["passed"] for x in checks), "scope": "独立容器归属与无数据库查询的HTTP健康/静态页面", "checks": checks, "login_tested": False, "workflow_tested": False, "models_called": 0, "database_queries": 0}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="独立原生环境控制；默认只生成计划")
    parser.add_argument("action", choices=["plan", "start", "smoke"], nargs="?", default="plan")
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    target = args.runtime.resolve()
    manifest = read_manifest(target)
    if not args.execute or args.action == "plan":
        result = {"dry_run": True, "project": manifest["project"], "runtime": str(target), "requested_action": args.action, "new_volume_namespace": manifest["project"] + "_", "ports": manifest["ports"], "database_queries": 0, "services_started": False}
    elif args.action == "start":
        result = execute_start(target, manifest)
    else:
        result = smoke(target, manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.execute and args.action != "plan" and not result.get("started" if args.action == "start" else "passed", False):
        raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit('历史容器控制入口已停用。请使用 deploy/native/windows.py start/stop/doctor；当前方案不使用Docker。')
