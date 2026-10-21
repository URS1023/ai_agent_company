"""为专属发行版保持一个可核归属的前台任务；不修改WSL全局空闲或内存配置。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from uuid import uuid4

import psutil
from wsl_identity import read_start_ticks, record_start_ticks

COMPONENT = 'dify-wsl-lifetime'


def linux_context():
    import wsl_runtime as runtime
    runtime.check_root()
    return runtime, runtime.load(), runtime.ROOT / 'lifetime.json'


def linux_process(record: dict, runtime_id: str, boot_id: str):
    """随机实例、raw ticks和命令共同认领；created_at只作审计展示。"""
    if record.get('runtime_id') != runtime_id or record.get('boot_id') != boot_id:
        return None
    expected_ticks = record_start_ticks(record)
    try:
        candidate = psutil.Process(record['pid'])
        if read_start_ticks(candidate.pid) != expected_ticks:
            return None
        if candidate.exe() != record['executable'] or candidate.cmdline() != record['command']:
            raise RuntimeError('lifetime_process_identity_changed')
        if candidate.uids().real != 0:
            raise RuntimeError('lifetime_process_owner_changed')
        return candidate if candidate.status() != psutil.STATUS_ZOMBIE and read_start_ticks(candidate.pid) == expected_ticks else None
    except (psutil.NoSuchProcess, FileNotFoundError):
        return None


def linux_status() -> dict:
    runtime, data, path = linux_context()
    record = json.loads(path.read_text()) if path.exists() else {}
    current_boot = runtime.boot_identity()
    if record:
        try:
            record_start_ticks(record)
        except RuntimeError:
            return {'runtime_id': data['id'], 'boot_id': current_boot, 'running': None,
                'keeper_id': record.get('keeper_id'), 'pid': record.get('pid'),
                'requires_identity_migration': True, 'requires_boot_reconciliation': data['boot_id'] != current_boot}
    if data['boot_id'] != current_boot:
        # v2只因真实启动组成变化而不同；仍由reconcile核旧cgroup/PID/netns全消失，不能直接清记录。
        return {'runtime_id': data['id'], 'boot_id': current_boot, 'running': False,
            'keeper_id': record.get('keeper_id'), 'pid': record.get('pid'),
            'requires_identity_migration': False, 'requires_boot_reconciliation': True,
            'identity_state': 'different_boot'}
    process = linux_process(record, data['id'], current_boot)
    return {
        'runtime_id': data['id'], 'boot_id': current_boot,
        'running': process is not None, 'keeper_id': record.get('keeper_id'),
        'pid': process.pid if process else None,
        'requires_boot_reconciliation': data['boot_id'] != current_boot,
    }


def run_linux(keeper_id: str) -> None:
    runtime, data, path = linux_context()
    if not re.fullmatch(r'[a-f0-9]{32}', keeper_id):
        raise RuntimeError('lifetime_identifier_invalid')
    current_boot = runtime.boot_identity()
    if data['boot_id'] != current_boot:
        raise RuntimeError('wsl_boot_changed_requires_reconcile')
    candidate = psutil.Process()
    with runtime.lock():
        previous = json.loads(path.read_text()) if path.exists() else {}
        if linux_process(previous, data['id'], current_boot):
            raise RuntimeError('lifetime_already_running')
        if previous:
            runtime.atomic_json(runtime.ROOT / ('lifetime-history-' + uuid4().hex + '.json'), previous)
        record = {
            'runtime_id': data['id'], 'boot_id': current_boot, 'keeper_id': keeper_id,
            'pid': candidate.pid, 'created_at': candidate.create_time(),
            'identity_version': 2, 'start_ticks': read_start_ticks(candidate.pid),
            'executable': candidate.exe(), 'command': candidate.cmdline(),
        }
        runtime.atomic_json(path, record)
    running = True

    def stop_requested(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    try:
        while running:
            signal.pause()
    finally:
        with runtime.lock():
            current = json.loads(path.read_text())
            if current.get('keeper_id') == keeper_id and current.get('pid') == candidate.pid:
                runtime.atomic_json(path, {**current, 'stopped_at': runtime.timestamp()})


def stop_linux(keeper_id: str) -> dict:
    runtime, data, path = linux_context()
    if not path.exists():
        return {'stopped': True, 'already_stopped': True}
    record = json.loads(path.read_text())
    if record.get('keeper_id') != keeper_id:
        raise RuntimeError('lifetime_instance_changed')
    record_start_ticks(record)
    process = linux_process(record, data['id'], runtime.boot_identity())
    if process is None:
        return {'stopped': True, 'already_stopped': True}
    process.terminate()
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        remaining = linux_process(record, data['id'], runtime.boot_identity())
        if remaining:
            remaining.kill()
            remaining.wait(timeout=5)
    return {'stopped': linux_process(record, data['id'], runtime.boot_identity()) is None}


def windows_command(action: str, keeper_id: str | None = None) -> list[str]:
    import wsl
    wsl.require_registered_distro()
    command = [
        str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'),
        '--distribution', wsl.DISTRO, '--user', 'root', '--exec',
        wsl.LINUX_ROOT + '/tooling/bin/python',
        wsl.linux_path(Path(__file__).resolve()), action,
    ]
    return command + (['--keeper-id', keeper_id] if keeper_id else [])


def linux_call(action: str, keeper_id: str | None = None) -> dict:
    import wsl
    result = subprocess.run(
        windows_command(action, keeper_id), env=wsl.clean_environment(),
        capture_output=True, timeout=25,
    )
    if result.returncode:
        raise RuntimeError('wsl_lifetime_' + action + '_failed')
    return json.loads(result.stdout)


def ensure_windows_lifetime() -> dict:
    """Windows持有前台WSL客户端，Linux持有精确记录；两侧一致时复用。"""
    import windows
    import wsl
    state = windows.load(windows.DEFAULT_ROOT)
    previous = state.get('processes', {}).get(COMPONENT, {})
    process = windows.owned_process(windows.DEFAULT_ROOT, COMPONENT, state)
    inspected = linux_call('status')
    if inspected.get('requires_identity_migration'):
        raise RuntimeError('requires_identity_migration')
    if inspected.get('running') is None:
        raise RuntimeError('lifetime_identity_unknown_requires_review')
    if process or inspected['running']:
        if process and inspected['running'] and inspected['keeper_id'] == previous.get('keeper_id'):
            return {'running': True, 'reused': True, 'runtime_id': inspected['runtime_id']}
        raise RuntimeError('wsl_lifetime_sides_do_not_match')
    if inspected['requires_boot_reconciliation']:
        # 原控制器只在旧PID/cgroup/netns均不存在时归档启动历史，不能跳过归属检查。
        if wsl.main(['reconcile-boot']) != 0:
            raise RuntimeError('wsl_boot_reconciliation_failed')
    keeper_id = uuid4().hex
    launched = windows.spawn(
        windows.DEFAULT_ROOT, COMPONENT, windows_command('run', keeper_id),
        cwd=windows.REPO, env=wsl.clean_environment(),
        metadata={'keeper_id': keeper_id, 'runtime_id': inspected['runtime_id']},
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            confirmed = linux_call('status')
            if confirmed['running']:
                if confirmed['keeper_id'] != keeper_id:
                    raise RuntimeError('wsl_lifetime_instance_changed')
                return {'running': True, 'runtime_id': confirmed['runtime_id']}
            if not windows.owned_process(windows.DEFAULT_ROOT, COMPONENT, windows.load(windows.DEFAULT_ROOT)):
                raise RuntimeError('wsl_lifetime_process_exited')
            time.sleep(.2)
        raise TimeoutError('wsl_lifetime_not_ready')
    except Exception:
        if windows.same_launch(windows.load(windows.DEFAULT_ROOT)['processes'].get(COMPONENT), launched):
            # 先处理Linux精确实例，不能把Windows客户端退出当成Linux命令已经消失。
            stopped = linux_call('stop', keeper_id)
            if stopped.get('stopped'):
                windows.stop(windows.DEFAULT_ROOT, COMPONENT)
        raise


def stop_windows_lifetime() -> dict:
    import windows
    state = windows.load(windows.DEFAULT_ROOT)
    info = state.get('processes', {}).get(COMPONENT)
    if not info:
        return {'stopped': True, 'already_stopped': True}
    stopped = linux_call('stop', info['keeper_id'])
    if not stopped.get('stopped'):
        raise RuntimeError('linux_lifetime_cleanup_incomplete')
    return windows.stop(windows.DEFAULT_ROOT, COMPONENT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'status', 'stop'])
    parser.add_argument('--keeper-id')
    args = parser.parse_args()
    if args.action == 'run':
        run_linux(args.keeper_id or '')
    elif args.action == 'stop':
        print(json.dumps(stop_linux(args.keeper_id or '')))
    else:
        print(json.dumps(linux_status()))
