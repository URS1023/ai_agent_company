"""Linux启动和进程身份只使用内核原始字段，不以可调整的壁钟推断PID出生。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

IDENTITY_VERSION = 2


def read_start_ticks(pid: int | str, *, proc_root: Path = Path('/proc')) -> str:
    """读取stat第22字段；comm可包含空格和括号，因此从最后一个右括号后解析。"""
    if pid != 'self' and (type(pid) is not int or pid < 1):
        raise RuntimeError('linux_process_identifier_invalid')
    raw = (proc_root / str(pid) / 'stat').read_text()
    prefix, separator, suffix = raw.rpartition(')')
    fields = suffix.split()
    header_pid = prefix.partition(' ')[0]
    if (not separator or not header_pid.isascii() or not header_pid.isdecimal()
            or (pid != 'self' and int(header_pid) != pid) or len(fields) < 20
            or not fields[19].isascii() or not fields[19].isdecimal()):
        raise RuntimeError('linux_process_stat_invalid')
    return fields[19]


def boot_components(*, proc_root: Path = Path('/proc'), namespace_pid: int | str = 1) -> dict:
    """PID1原始ticks区分发行版重建；worker读取自身namespace避免跨用户ptrace。"""
    if namespace_pid not in (1, 'self') or type(namespace_pid) is bool:
        raise RuntimeError('linux_boot_namespace_invalid')
    kernel = (proc_root / 'sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', kernel):
        raise RuntimeError('linux_kernel_boot_invalid')
    inode = (proc_root / str(namespace_pid) / 'ns/pid').stat().st_ino
    if type(inode) is not int or inode <= 0:
        raise RuntimeError('linux_pid_namespace_invalid')
    return {'version': IDENTITY_VERSION, 'kernel': kernel,
        'init_start_ticks': read_start_ticks(1, proc_root=proc_root), 'pid_namespace': inode}


def boot_identity_v2(*, proc_root: Path = Path('/proc'), namespace_pid: int | str = 1) -> str:
    """固定字段及排序生成v2摘要；不读私有manifest、不修改任何Linux状态。"""
    value = boot_components(proc_root=proc_root, namespace_pid=namespace_pid)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def record_start_ticks(record: dict) -> str:
    """旧记录必须经人工证明迁移；缺字段不能冒充进程不存在或自动接管当前PID。"""
    ticks = record.get('start_ticks')
    if (type(record.get('identity_version')) is not int or record['identity_version'] != IDENTITY_VERSION
            or not isinstance(ticks, str) or not ticks.isascii() or not ticks.isdecimal()):
        raise RuntimeError('requires_identity_migration')
    return ticks
