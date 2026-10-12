"""仅验证隔离浏览器宿主的端口选择与调度接线，不启动应用、浏览器或数据库。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import business_browser_acceptance as runner


class BrowserPortAllocationTests(unittest.TestCase):
    def test_candidates_stay_in_high_port_range_and_probes_close(self):
        probes = []

        def socket_fixture(*args):
            probe = MagicMock()
            probe.__enter__.return_value = probe
            probes.append(probe)
            return probe

        size = runner.BROWSER_PORT_MAX - runner.BROWSER_PORT_MIN + 1
        with (patch.object(runner.secrets, "randbelow", side_effect=[0, size - 1]),
              patch.object(runner.socket, "socket", side_effect=socket_fixture)):
            ports = [runner.browser_host_port(), runner.browser_host_port()]
        self.assertEqual(ports, [49152, 65535])
        for port, probe in zip(ports, probes):
            probe.bind.assert_called_once_with(("127.0.0.1", port))
            probe.__exit__.assert_called_once()
            self.assertNotIn(port, {0, 21, 6000, 6665, 6666, 6667, 6697, 8011, 8012, 10080})

    def test_occupied_local_port_is_skipped_without_connecting(self):
        """真实回环 bind 占用一端口，仅探测两个候选，不发送 HTTP 或业务请求。"""
        occupied_port = runner.browser_host_port()
        with runner.socket.socket(runner.socket.AF_INET, runner.socket.SOCK_STREAM) as occupied:
            if hasattr(runner.socket, "SO_EXCLUSIVEADDRUSE"):
                occupied.setsockopt(runner.socket.SOL_SOCKET, runner.socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("127.0.0.1", occupied_port))
            free_port = runner.browser_host_port()
            self.assertNotEqual(free_port, occupied_port)
            with patch.object(runner.secrets, "randbelow", side_effect=[
                occupied_port - runner.BROWSER_PORT_MIN, free_port - runner.BROWSER_PORT_MIN,
            ]) as choose:
                self.assertEqual(runner.browser_host_port(), free_port)
            self.assertEqual(choose.call_count, 2)
        # 返回前探测 socket 已释放，调用方可以实际绑定它。
        with runner.socket.socket(runner.socket.AF_INET, runner.socket.SOCK_STREAM) as owner:
            owner.bind(("127.0.0.1", free_port))

    def test_all_occupied_candidates_stop_at_fixed_probe_budget(self):
        probes = []

        def occupied_fixture(*args):
            probe = MagicMock()
            probe.__enter__.return_value = probe
            probe.bind.side_effect = OSError("本机端口已占用")
            probes.append(probe)
            return probe

        with (patch.object(runner.secrets, "randbelow", return_value=0),
              patch.object(runner.socket, "socket", side_effect=occupied_fixture)):
            with self.assertRaisesRegex(RuntimeError, "32 次探测"):
                runner.browser_host_port()
        self.assertEqual(len(probes), 32)
        for probe in probes:
            probe.__exit__.assert_called_once()

    def test_scheduler_passes_explicit_high_port_to_its_host(self):
        """回归实际故障入口：不能把 PORT=0 交给系统后直接导航随机端口。"""
        observed = []

        class FixtureHost:
            def __init__(self, directory, model_port, *, env_overrides=None):
                port = int((env_overrides or {}).get("PORT", "0"))
                observed.append(port)
                assert 49152 <= port <= 65535, f"浏览器宿主仍使用未筛选端口：{port}"
                self.base = f"http://127.0.0.1:{port}"

            def ready(self):
                """假宿主立即就绪，不产生子进程。"""

        class FixtureProcess:
            def wait(self, *, timeout):
                return 0

        class FixtureJob:
            def assign(self, process):
                """假进程不创建 Windows Job。"""

        def finish(check, **kwargs):
            check["seconds"] = 0

        with tempfile.TemporaryDirectory(prefix="test5-port-wiring-") as temporary:
            with (patch.object(runner, "ROOT", Path(temporary)),
                  patch.object(runner, "Host", FixtureHost),
                  patch.object(runner, "WindowsProcessJob", FixtureJob),
                  patch.object(runner, "finish_case", finish),
                  patch.object(runner.shutil, "which", return_value="fixture-node"),
                  patch.object(runner.subprocess, "check_output", return_value="fixture\n"),
                  patch.object(runner.subprocess, "Popen", return_value=FixtureProcess()),
                  patch.object(runner.sys, "argv", ["browser-test", "--suites", "ai_documents"]),
                  redirect_stdout(io.StringIO())):
                result = runner.main()
            manifest_path = next(Path(temporary).rglob("manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(result, 0, manifest["checks"])
            self.assertEqual(len(observed), 1)
            self.assertEqual(manifest["checks"][0]["base_url"], f"http://127.0.0.1:{observed[0]}")


if __name__ == "__main__":
    unittest.main()
