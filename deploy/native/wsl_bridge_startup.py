"""让已登记工具随本机启动恢复精确代理规则，不连接业务数据或原生模型。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from uuid import uuid4


def reconcile_linux(snapshot: Path) -> dict:
    """在操作锁内校验快照、解析配置并重载所属代理；失败收据阻止自动重试。"""
    import wsl_runtime as runtime

    runtime.check_root()
    data = runtime.load()
    binding_path = runtime.ROOT / "tool-bridge.json"
    if not binding_path.exists():
        return {"configured": False, "reason": "tools_not_registered"}
    if data["boot_id"] != runtime.boot_identity():
        raise RuntimeError("tool_bridge_boot_changed")
    proxy_info = data.get("processes", {}).get("proxy", {})
    proxy = runtime.owned_process(proxy_info, data)
    if not proxy:
        raise RuntimeError("tool_bridge_proxy_not_owned")
    proof = runtime.verify_tool_bridge_snapshot(data, snapshot)
    target = runtime.ROOT / "config/squid.conf"
    expected = runtime.squid_configuration(data, special=False, tool_acl=runtime.tool_bridge_acl(
        data, {"snapshot": str(snapshot), "proof": proof}
    ))
    receipt = runtime.ROOT / "tool-bridge-startup.json"
    previous = None
    if receipt.exists():
        info = receipt.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("tool_bridge_startup_receipt_owner_changed")
        previous = json.loads(receipt.read_text("utf-8"))
        if previous.get("status") != "ready":
            raise RuntimeError("tool_bridge_previous_startup_requires_review")
        if (previous.get("proof") == proof and previous.get("proxy_launch_id") == proxy_info["launch_id"]
                and target.read_text("utf-8") == expected
                and previous.get("config_sha256") == runtime.file_sha(target)):
            return {"configured": True, "reused": True, "agent_proxy_changed": False}

    # 记录一次真实配置尝试；历史保留，重复启动不能掩盖之前失败。
    previous_archive = None
    if previous:
        previous_archive = "tool-bridge-startup-history-" + uuid4().hex + ".json"
        runtime.atomic_json(runtime.ROOT / previous_archive, previous)
    state = {"status": "attempted", "at": runtime.timestamp(), "proof": proof,
             "proxy_launch_id": proxy_info["launch_id"], "business_http": 0, "model_requests": 0,
             "previous_receipt": previous_archive}
    runtime.atomic_json(receipt, state)
    candidate = runtime.ROOT / "config/squid-startup-verify.conf"
    created = False
    try:
        with candidate.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(expected)
        candidate.chmod(0o644)
        parsed = subprocess.run(
            ["/usr/sbin/runuser", "-u", "agentapp", "--", "/usr/sbin/squid", "-k", "parse",
             "-f", str(candidate)],
            env=runtime.safe_environment(data), capture_output=True, timeout=10,
        )
        (runtime.ROOT / "tool-bridge-startup-parse.log").write_bytes(parsed.stdout + parsed.stderr)
        if parsed.returncode:
            raise RuntimeError("tool_bridge_proxy_configuration_invalid")
        # 解析成功后再次核对归属和快照，避免在核验期间换进程或改绑定。
        if not runtime.owned_process(proxy_info, runtime.load()):
            raise RuntimeError("tool_bridge_proxy_changed_before_reload")
        runtime.configure_tool_bridge(snapshot)
        if target.read_text("utf-8") != expected:
            raise RuntimeError("tool_bridge_configuration_changed")
        os.kill(proxy.pid, signal.SIGHUP)
        if not runtime.owned_process(proxy_info, runtime.load()):
            raise RuntimeError("tool_bridge_proxy_exited_after_reload")
        state.update(status="ready", config_sha256=runtime.file_sha(target), finished_at=runtime.timestamp())
        runtime.atomic_json(receipt, state)
        return {"configured": True, "reused": False, "agent_proxy_changed": False}
    except BaseException as error:
        state.update(status="failed", error_type=type(error).__name__)
        runtime.atomic_json(receipt, state)
        raise
    finally:
        if created:
            candidate.unlink(missing_ok=True)


def reconcile_windows(root: Path) -> dict:
    """从Windows登记的所属桥获取快照，不接受任意路径或外部进程。"""
    import windows
    import wsl
    import wsl_http_bridge

    data = windows.load(root)
    if not wsl_http_bridge.owned_bridge(root, data):
        raise RuntimeError("tool_bridge_not_owned")
    info = data["processes"][wsl_http_bridge.COMPONENT]
    if not info.get("ready"):
        raise RuntimeError("tool_bridge_not_ready")
    snapshot = Path(info["config_file"])
    wsl.require_registered_distro()
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/wsl.exe"
    command = [
        str(executable), "-d", wsl.DISTRO, "-u", "root", "--exec", "/usr/bin/timeout",
        "--signal=TERM", "--kill-after=5", "40", wsl.LINUX_ROOT + "/tooling/bin/python",
        wsl.linux_path(Path(__file__).resolve()), "--snapshot", wsl.linux_path(snapshot),
    ]
    result = subprocess.run(command, capture_output=True, env=wsl.clean_environment(), timeout=50)
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise RuntimeError("tool_bridge_startup_response_invalid") from None
    if result.returncode:
        raise RuntimeError("tool_bridge_startup_failed:" + str(value.get("error_type", "unknown")))
    return value


def main() -> int:
    import argparse
    from wsl_setup import operation_lock

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    try:
        with operation_lock():
            result = reconcile_linux(args.snapshot)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
