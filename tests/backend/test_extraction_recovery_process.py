"""经隔离入口启动有界真实进程故障样本，不把内存异常桩冒充进程硬杀。"""
from pathlib import Path
import json
import os
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name != "nt", reason="真实进程死亡证明本轮仅覆盖 Windows")
@pytest.mark.parametrize("fault", ["started", "flushed", "committed", "alive"])
def test_real_worker_hard_interrupt_and_restart(fault):
    """每场景单次单条合成抽取；保留子进程原始日志与最终内核清理证据。"""
    output = ROOT / ".implementation" / ("extraction-batch23-process-" + fault + "-" + uuid.uuid4().hex[:8])
    completed = subprocess.run([sys.executable, str(ROOT / "tests/dsh/extraction_recovery_probe.py"),
        "--fault", fault, "--output", str(output)], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=110,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "GIT_AUTO_PUSH": "0"})
    (output / "probe-stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output / "probe-stderr.log").write_text(completed.stderr, encoding="utf-8")
    print("原始进程证据：", output)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["all_processes_exited"]
    assert all(item["active_after"] == 0 for item in report["cleanup"])
