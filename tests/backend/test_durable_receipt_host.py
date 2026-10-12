"""从隔离入口运行真实 Host/Worker 丢包与临时 SQLite 冷重开验证。"""
import os
from pathlib import Path
import subprocess
import sys


def test_real_host_worker_recovers_lost_reply_and_cold_receipt(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
    result = subprocess.run(["node", str(root / "tests/dsh/durable_receipt_probe.mjs"), sys.executable, str(tmp_path / "receipts.sqlite")],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=110)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0
    assert '"proof":"real_worker_host_lost_reply_and_cold_lookup"' in result.stdout


def test_host_receipt_signal_races_and_unknown_states():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["node", "--test", "services/dsh_host/tests/durable-receipt.test.mjs",
        "services/dsh_host/tests/business-tool-receipt.test.mjs", "services/dsh_host/tests/worker.test.mjs"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0
