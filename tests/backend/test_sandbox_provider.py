"""Cross-platform Python sandbox output and repair contracts."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import settings
from app.errors import SandboxError
from app.providers.tool import sandbox_provider
from app.providers.tool.base import ToolSpec


def _spec() -> ToolSpec:
    return ToolSpec(
        name="python_execute",
        description="isolated test",
        provider="sandbox",
        timeout_seconds=30,
    )


def test_linux_tmp_output_is_mapped_and_collected_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    result = sandbox_provider.invoke(
        _spec(),
        {
            "code": (
                "from pathlib import Path\n"
                "Path('/tmp/nested/financial_dashboard.png').write_bytes(b'chart')\n"
                "result = {'saved': True}\n"
            )
        },
        {"run_id": "portable-output", "allow_network": False},
    )

    assert result.status == "success"
    assert result.data["result"] == {"saved": True}
    assert result.data["files"] == [str(Path("nested") / "financial_dashboard.png")]
    artifact = result.artifacts[0]
    assert Path(artifact["path"]).read_bytes() == b"chart"
    assert Path(artifact["path"]).resolve().is_relative_to(tmp_path.resolve())


def test_invalid_matplotlib_rectangle_radius_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    result = sandbox_provider.invoke(
        _spec(),
        {
            "code": (
                "from matplotlib.patches import Rectangle\n"
                "patch = Rectangle((0, 0), 1, 1, radius=0.1)\n"
                "result = {'width': patch.get_width()}\n"
            )
        },
        {"run_id": "rectangle-repair", "allow_network": False},
    )
    assert result.status == "success"
    assert result.data["result"] == {"width": 1}


def test_sandbox_error_excludes_raw_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))

    with pytest.raises(SandboxError) as caught:
        sandbox_provider.invoke(
            _spec(),
            {"code": "raise ValueError('invalid chart input')"},
            {"run_id": "short-error", "allow_network": False},
        )

    assert str(caught.value) == "ValueError: invalid chart input"
    assert "Traceback" not in str(caught.value)


def test_workflow_keeps_complete_nested_output(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    output = sandbox_provider.invoke(
        _spec(),
        {"code": "def main(rows):\n    return {'data': {'rows': rows}}", "inputs": {"rows": [{"number": n, "tags": ["中文"]} for n in range(250)]}},
        {"run_id": "complete-workflow-output", "allow_network": False},
    )
    rows = output.data["result"]["data"]["rows"]
    assert len(rows) == 250
    assert rows[-1] == {"number": 249, "tags": ["中文"]}


@pytest.mark.parametrize("code, message", [
    ("print('no return')", "没有输出"),
    ("result = float('nan')", "JSON"),
    ("result = '字' * 400000", "超过 1 MB"),
])
def test_workflow_invalid_output_fails_explicitly(tmp_path, monkeypatch, code, message):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(SandboxError, match=message):
        sandbox_provider.invoke(
            _spec(), {"code": code, "inputs": {"input": {}}},
            {"run_id": "invalid-workflow-output", "allow_network": False},
        )


@pytest.mark.parametrize("code,inputs", [
    ("raise SystemExit(7)", {}),
    ("import os; os._exit(9)", {}),
    ("import os; os._exit(0)", {}),
    ("raise SystemExit(0)", {"inputs": {"input": {}}}),
])
def test_early_worker_exit_cannot_report_success(tmp_path, monkeypatch, code, inputs):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(SandboxError):
        sandbox_provider.invoke(_spec(), {"code": code, **inputs},
                                {"run_id": "early-exit", "allow_network": False})


@pytest.mark.parametrize("escape_code", [
    "import os; os.spawnv(os.P_WAIT, 'definitely-not-a-command', ['definitely-not-a-command'])",
    "import os; os.system('echo sandbox-escape')",
    "import ctypes; ctypes.CDLL(None)",
])
def test_sandbox_blocks_process_and_native_library_escape(tmp_path, monkeypatch, escape_code):
    """进程变体和 ctypes 动态库入口必须统一失败，不能绕过沙箱。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(SandboxError, match="(subprocess|禁止|ImportError|PermissionError)"):
        sandbox_provider.invoke(
            _spec(), {"code": escape_code},
            {"run_id": "escape-boundary", "allow_network": False},
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows 的 nt/startfile 入口仅在 Windows 存在")
@pytest.mark.parametrize("escape_code", [
    "import os; os.startfile('sandbox-escape-target.txt')",
    "import nt; nt.system('echo sandbox-escape')",
])
def test_sandbox_blocks_windows_process_aliases(tmp_path, monkeypatch, escape_code):
    """Windows 特有的 startfile 与 nt.system 也不能绕过进程边界。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(SandboxError, match="(subprocess|禁止|PermissionError)"):
        sandbox_provider.invoke(
            _spec(), {"code": escape_code},
            {"run_id": "windows-escape-boundary", "allow_network": False},
        )


def test_sandbox_allows_numpy_internal_ctypes_initialization(tmp_path, monkeypatch):
    """numpy 等受信任依赖仍可在初始化时使用 ctypes，不影响图表工具。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    result = sandbox_provider.invoke(
        _spec(), {"code": "import numpy; result = {'loaded': True}"},
        {"run_id": "trusted-native-import", "allow_network": False},
    )
    assert result.data["result"] == {"loaded": True}
