"""用专属cgroup v2跟踪原生进程树；不设置资源限额、不接管其他服务。"""

from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import time

import psutil

BASE = Path('/sys/fs/cgroup')


def names(runtime_id: str, launch_id: str, role: str) -> tuple[Path, Path]:
    if not re.fullmatch('[a-f0-9]{32}', runtime_id) or not re.fullmatch('[a-f0-9]{32}', launch_id):
        raise RuntimeError('cgroup_identity_invalid')
    if not re.fullmatch('[a-z_]{2,24}', role):
        raise RuntimeError('cgroup_role_invalid')
    parent = BASE / ('aic-' + runtime_id)
    return parent, parent / (launch_id + '-' + role)


def secure_directory(path: Path, *, inode=None) -> None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RuntimeError('cgroup_path_redirected')
    stat = path.stat()
    if not path.is_dir() or stat.st_uid != 0 or stat.st_mode & 0o022:
        raise RuntimeError('cgroup_directory_not_root_owned')
    if inode is not None and stat.st_ino != inode:
        raise RuntimeError('cgroup_directory_replaced')


def prepare_root(runtime_id: str, expected_inode=None) -> int:
    parent, _ = names(runtime_id, '0' * 32, 'runtime')
    if not (BASE / 'cgroup.controllers').is_file():
        raise RuntimeError('cgroup_v2_required')
    if expected_inode is None:
        parent.mkdir(exist_ok=False)
        parent.chmod(0o700)
    secure_directory(parent, inode=expected_inode)
    return parent.stat().st_ino


def create(runtime_id: str, launch_id: str, role: str, parent_inode: int) -> dict:
    parent, path = names(runtime_id, launch_id, role)
    secure_directory(parent, inode=parent_inode)
    path.mkdir(exist_ok=False)
    path.chmod(0o700)
    if not (path / 'cgroup.kill').is_file():
        raise RuntimeError('cgroup_kill_not_available')
    return {'runtime_id': runtime_id, 'launch_id': launch_id, 'role': role, 'path': str(path),
        'inode': path.stat().st_ino, 'parent_inode': parent_inode}


def verify(info: dict) -> Path:
    parent, path = names(info['runtime_id'], info['launch_id'], info['role'])
    if info.get('path') != str(path):
        raise RuntimeError('cgroup_manifest_path_changed')
    secure_directory(parent, inode=info['parent_inode'])
    secure_directory(path, inode=info['inode'])
    if not (path / 'cgroup.kill').is_file():
        raise RuntimeError('cgroup_kill_not_available')
    return path


def join(info: dict) -> None:
    """包装器在派生任何业务子进程前进入本次cgroup，子孙自动继承归属。"""
    path = verify(info)
    (path / 'cgroup.procs').write_text(str(os.getpid()))


def contains(info: dict, pid: int) -> bool:
    path = verify(info)
    relative = '/' + path.relative_to(BASE).as_posix()
    try:
        entries = Path('/proc', str(pid), 'cgroup').read_text().splitlines()
        return any(line.startswith('0::') and (line[3:] == relative or line[3:].startswith(relative + '/')) for line in entries)
    except FileNotFoundError:
        return False


def members(info: dict) -> list[int]:
    path = verify(info)
    values = set()
    groups = [path, *[item for item in path.rglob('*') if item.is_dir()]]
    if len(groups) > 256:
        raise RuntimeError('cgroup_descendant_budget_exceeded')
    for group in groups:
        secure_directory(group)
        values.update(int(value) for value in (group / 'cgroup.procs').read_text().split())
    return sorted(values)


def live_members(info: dict) -> list[int]:
    alive = []
    for pid in members(info):
        try:
            if contains(info, pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                alive.append(pid)
        except psutil.NoSuchProcess:
            continue
    return alive


def stop(info: dict, *, seconds=10) -> dict:
    """先正常TERM，再仅kill经inode核验的本次cgroup；组长先退出也不漏孤儿。"""
    path = verify(info)
    deadline = time.monotonic() + seconds
    for pid in live_members(info):
        if contains(info, pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    while time.monotonic() < deadline - 1:
        if not live_members(info):
            return {'verified': True, 'stopped': True, 'alive_pids': [], 'cgroup_preserved': True}
        time.sleep(0.1)
    # 使用cgroup.kill避免PID复用竞态；不会对更换inode或不属于本runtime的组发信号。
    if live_members(info):
        verify(info)
        (path / 'cgroup.kill').write_text('1')
    while time.monotonic() < deadline:
        if not live_members(info):
            return {'verified': True, 'stopped': True, 'alive_pids': [], 'cgroup_preserved': True}
        time.sleep(0.05)
    alive = live_members(info)
    return {'verified': True, 'stopped': not alive, 'alive_pids': alive, 'cgroup_preserved': True}
