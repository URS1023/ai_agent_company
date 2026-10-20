"""工具代理重启配置回归；只使用临时文件与替身，不启动WSL或发请求。"""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def subject(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "deploy/native/wsl_bridge_startup.py"
    spec = importlib.util.spec_from_file_location("wsl_bridge_startup_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "config").mkdir()
    (tmp_path / "tool-bridge.json").write_text("{}")
    target = tmp_path / "config/squid.conf"
    target.write_text("old")
    proof = {"snapshot_sha256": "new"}
    state = {"boot_id": "boot", "processes": {"proxy": {"launch_id": "proxy"}}}
    calls = []

    def configure(snapshot):
        calls.append("configure")
        target.write_text("new")

    runtime = SimpleNamespace(
        ROOT=tmp_path, check_root=lambda: None, load=lambda: state, boot_identity=lambda: "boot",
        owned_process=lambda *_: SimpleNamespace(pid=7), verify_tool_bridge_snapshot=lambda *_: proof,
        squid_configuration=lambda *_a, **_k: "new", tool_bridge_acl=lambda *_: "acl",
        atomic_json=lambda path, value: path.write_text(json.dumps(value)), timestamp=lambda: "now",
        safe_environment=lambda _: {}, file_sha=lambda _: "sha", configure_tool_bridge=configure,
    )
    monkeypatch.setitem(sys.modules, "wsl_runtime", runtime)
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k:
                        SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    monkeypatch.setattr(module.os, "kill", lambda *_: calls.append("reload"))
    monkeypatch.setattr(module.signal, "SIGHUP", 1, raising=False)
    # Windows没有Linux属主，纯测试仅模拟本项目根用户收据。
    original_stat = Path.stat
    receipt = tmp_path / "tool-bridge-startup.json"

    def stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == receipt:
            return SimpleNamespace(st_uid=0, st_mode=0o600)
        return result

    monkeypatch.setattr(Path, "stat", stat)
    return module, runtime, calls, receipt


def test_first_configuration_and_same_launch_reuse(subject, tmp_path):
    module, _, calls, receipt = subject
    assert module.reconcile_linux(tmp_path / "snapshot.json")["reused"] is False
    assert calls == ["configure", "reload"]
    assert json.loads(receipt.read_text())["status"] == "ready"
    assert module.reconcile_linux(tmp_path / "snapshot.json")["reused"] is True
    assert calls == ["configure", "reload"]


def test_changed_snapshot_reconfigures_and_preserves_receipt(subject, tmp_path):
    module, runtime, calls, receipt = subject
    module.reconcile_linux(tmp_path / "snapshot.json")
    runtime.verify_tool_bridge_snapshot = lambda *_: {"snapshot_sha256": "next"}
    module.reconcile_linux(tmp_path / "next.json")
    saved = json.loads(receipt.read_text())
    assert (tmp_path / saved["previous_receipt"]).is_file()
    assert calls == ["configure", "reload", "configure", "reload"]


def test_parse_failure_does_not_reload_or_retry(subject, tmp_path, monkeypatch):
    module, _, calls, receipt = subject
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k:
                        SimpleNamespace(returncode=1, stdout=b"", stderr=b"invalid"))
    with pytest.raises(RuntimeError, match="configuration_invalid"):
        module.reconcile_linux(tmp_path / "snapshot.json")
    assert calls == []
    assert json.loads(receipt.read_text())["status"] == "failed"
    with pytest.raises(RuntimeError, match="requires_review"):
        module.reconcile_linux(tmp_path / "snapshot.json")
    assert (tmp_path / "config/squid.conf").read_text() == "old"


def test_foreign_proxy_is_never_signalled(subject, tmp_path):
    module, runtime, calls, _ = subject
    runtime.owned_process = lambda *_: None
    with pytest.raises(RuntimeError, match="not_owned"):
        module.reconcile_linux(tmp_path / "snapshot.json")
    assert calls == []


def test_existing_candidate_is_preserved(subject, tmp_path):
    module, _, calls, _ = subject
    candidate = tmp_path / "config/squid-startup-verify.conf"
    candidate.write_text("preserve")
    with pytest.raises(FileExistsError):
        module.reconcile_linux(tmp_path / "snapshot.json")
    assert candidate.read_text() == "preserve"
    assert calls == []


def test_unregistered_tools_skip_without_configuration(subject, tmp_path):
    module, _, calls, _ = subject
    (tmp_path / "tool-bridge.json").unlink()
    assert module.reconcile_linux(tmp_path / "snapshot.json")["configured"] is False
    assert calls == []
