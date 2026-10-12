"""在最新原生宿主与临时平台库上验收全站路由和桌面、移动端外壳。"""
from contextlib import ExitStack
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from real_platform_smoke import Host, ROOT, PASSWORD


def main():
    """后端禁止外部数据库，测试脚本只能通过本轮宿主读取临时平台。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["all", "chat_shell", "full_browser_route_audit"], default="all")
    args = parser.parse_args()
    folder = {"all": "ui", "chat_shell": "ui-shell-fixed", "full_browser_route_audit": "ui-route-fixed"}[args.suite]
    output = ROOT / ".implementation/full-functional-20260929" / folder
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "最新Node宿主、临时SQLite、禁止业务库与外部网络", "checks": []}
    with tempfile.TemporaryDirectory(prefix="test5-functional-ui-") as temp, ExitStack() as cleanup:
        host = Host(Path(temp), 9)
        cleanup.callback(host.close)
        host.ready()
        env = {**os.environ, "TEST5_E2E_BASE_URL": host.base, "TEST5_E2E_PASSWORD": PASSWORD,
               "TEST5_E2E_ALLOW_NATIVE": "1", "TEST5_E2E_MODULES_ONLY": "1"}
        for name in (("full_browser_route_audit", "chat_shell") if args.suite == "all" else (args.suite,)):
            started = time.perf_counter()
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                try:
                    result = subprocess.run(["node", str(ROOT / "tests/ui" / (name + ".js"))], cwd=ROOT,
                                            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            report["checks"].append({"name": name, "exit_code": code, "seconds": round(time.perf_counter() - started, 3)})
            (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)
    return int(any(row["exit_code"] for row in report["checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
