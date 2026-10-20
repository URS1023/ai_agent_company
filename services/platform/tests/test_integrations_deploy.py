"""部署准备的边界测试：不执行Docker、网络、现有数据库或系统安装。"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy/native" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_hash_ignores_in_use_env_and_dependencies(tmp_path):
    prepare = load_tool("prepare")
    (tmp_path / "app.py").write_text("print(1)", "utf-8")
    (tmp_path / ".env").write_text("DB_HOST=do-not-read.invalid", "utf-8")
    (tmp_path / ".env.example").write_text("DB_HOST=db_postgres", "utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/x.js").write_text("dependency", "utf-8")
    original = prepare.source_fingerprint(tmp_path)
    assert original["files"] == 2
    (tmp_path / ".env").write_text("DB_HOST=changed-do-not-read.invalid", "utf-8")
    assert prepare.source_fingerprint(tmp_path) == original
    (tmp_path / "app.py").write_text("print(2)", "utf-8")
    assert prepare.source_fingerprint(tmp_path) != original


def test_prepare_rejects_existing_runtime_before_reading_old_env(tmp_path):
    prepare = load_tool("prepare")
    source = tmp_path / "source"
    (source / "web").mkdir(parents=True)
    (source / "web/package.json").write_text('{"version":"1.17.1"}', "utf-8")
    output = tmp_path / "runtime"
    output.mkdir()
    (output / "native.env").write_text("never overwrite", "utf-8")
    with pytest.raises(ValueError, match="已存在"):
        prepare.prepare(source, output, 12080, 13000)
    assert (output / "native.env").read_text("utf-8") == "never overwrite"


def test_manifest_tamper_and_old_manifest_are_rejected(tmp_path):
    control = load_tool("control")
    files = {"compose.yaml": "services: {}", "compose.source.yaml": "services: {}", "native.env": "DB_HOST=db_postgres"}
    hashes = {}
    for name, content in files.items():
        (tmp_path / name).write_text(content, "utf-8")
        hashes[name] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    manifest = {"project": "agent-native-0123456789", "generated_sha256": hashes}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    assert control.read_manifest(tmp_path)["project"] == manifest["project"]
    (tmp_path / "native.env").write_text("DB_HOST=unapproved.invalid", "utf-8")
    with pytest.raises(ValueError, match="变化"):
        control.read_manifest(tmp_path)
    manifest["generated_sha256"] = {}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="摘要"):
        control.read_manifest(tmp_path)


def test_docker_context_cannot_point_to_remote_or_environment_override(monkeypatch):
    control = load_tool("control")
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1234")
    monkeypatch.setattr(control.shutil, "which", lambda value: "fake-docker")
    with pytest.raises(RuntimeError, match="环境覆盖"):
        control.docker_command()
    monkeypatch.delenv("DOCKER_HOST")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(control, "run", lambda command, **kwargs: '"ssh://external.invalid"' if "inspect" in command else "external")
    with pytest.raises(RuntimeError, match="本机管道"):
        control.docker_command()


@pytest.mark.parametrize("existing", ["container", "volume"])
def test_start_never_reuses_existing_project_data(tmp_path, monkeypatch, existing):
    control = load_tool("control")
    monkeypatch.setattr(control, "docker_command", lambda: ["fake-docker"])
    monkeypatch.setattr(control, "project_containers", lambda docker, project: ["existing"] if existing == "container" else [])
    monkeypatch.setattr(control, "run", lambda command, **kwargs: "existing-volume")
    with pytest.raises(RuntimeError, match="已有"):
        control.execute_start(tmp_path, {"project": "agent-native-0123456789"})


def test_missing_image_does_not_pull_build_or_start(tmp_path, monkeypatch):
    control = load_tool("control")
    commands = []
    monkeypatch.setattr(control, "docker_command", lambda: ["fake-docker"])
    monkeypatch.setattr(control, "project_containers", lambda docker, project: [])
    def run(command, **kwargs):
        commands.append(command)
        if "image" in command:
            raise RuntimeError("not installed")
        return ""
    monkeypatch.setattr(control, "run", run)
    result = control.execute_start(tmp_path, {"project": "agent-native-0123456789", "images": {"plugin_daemon": "langgenius/dify-plugin-daemon:0.6.10-local"}})
    assert not result["started"]
    assert result["missing_images"] == ["langgenius/dify-plugin-daemon:0.6.10-local"]
    assert all("pull" not in command and "up" not in command and "build" not in command for command in commands)


def test_health_does_not_call_http_until_container_ownership_is_verified(tmp_path, monkeypatch):
    control = load_tool("control")
    monkeypatch.setattr(control, "docker_command", lambda: ["fake-docker"])
    def refuse(*args, **kwargs):
        raise RuntimeError("ownership unknown")
    monkeypatch.setattr(control, "verify_ownership", refuse)
    monkeypatch.setattr(control.httpx, "Client", lambda **kwargs: pytest.fail("不应发送网络请求"))
    with pytest.raises(RuntimeError, match="ownership"):
        control.smoke(tmp_path, {"project": "agent-native-0123456789"})


def test_bind_mount_boundary_rejects_parent_paths(tmp_path):
    control = load_tool("control")
    assert control.local_bind_matches(str(tmp_path / "nginx/nginx.conf"), tmp_path)
    assert not control.local_bind_matches(str(tmp_path.parent / "old-database"), tmp_path)
    assert not control.local_bind_matches(str(tmp_path) + "/../old-database", tmp_path)
