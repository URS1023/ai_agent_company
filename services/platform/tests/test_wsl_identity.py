"""只读新临时proc快照验证raw ticks身份；不访问本机Linux或进程。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('pure_wsl_identity', ROOT / 'deploy/native/wsl_identity.py')
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)
KERNEL = '12345678-1234-1234-1234-123456789abc'


def stat(pid, ticks, name='owned (worker) name'):
    return f'{pid} ({name}) ' + ' '.join(['S'] + ['0'] * 18 + [str(ticks)] + ['0'] * 3)


@pytest.fixture
def proc(tmp_path):
    (tmp_path / 'sys/kernel/random').mkdir(parents=True)
    (tmp_path / 'sys/kernel/random/boot_id').write_text(KERNEL + '\n')
    (tmp_path / '1/ns').mkdir(parents=True)
    (tmp_path / 'self/ns').mkdir(parents=True)
    (tmp_path / '1/stat').write_text(stat(1, '42'))
    (tmp_path / 'self/stat').write_text(stat(321, '12345'))
    (tmp_path / '1/ns/pid').touch()
    os.link(tmp_path / '1/ns/pid', tmp_path / 'self/ns/pid')
    return tmp_path


def test_boot_hash_uses_only_kernel_raw_ticks_and_namespace(proc, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, 'Process', lambda *a: pytest.fail('稳定身份不得读取壁钟出生时间'))
    expected = {'version': 2, 'kernel': KERNEL, 'init_start_ticks': '42',
        'pid_namespace': (proc / '1/ns/pid').stat().st_ino}
    assert identity.boot_components(proc_root=proc) == expected
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    assert identity.boot_identity_v2(proc_root=proc) == digest
    assert identity.boot_identity_v2(proc_root=proc, namespace_pid='self') == digest
    assert identity.read_start_ticks('self', proc_root=proc) == '12345'


@pytest.mark.parametrize('changed', ['kernel', 'init', 'namespace'])
def test_real_boot_component_changes_cannot_match_the_previous_identity(proc, changed):
    before = identity.boot_identity_v2(proc_root=proc)
    if changed == 'kernel':
        (proc / 'sys/kernel/random/boot_id').write_text('abcdefab-1234-1234-1234-123456789abc')
    elif changed == 'init':
        (proc / '1/stat').write_text(stat(1, '43'))
    else:
        # self仍持有旧inode，替换根namespace文件能精确证明新namespace不是旧启动。
        (proc / '1/ns/pid').unlink()
        (proc / '1/ns/pid').touch()
    assert identity.boot_identity_v2(proc_root=proc) != before


@pytest.mark.parametrize('raw', ['', stat(2, 42), stat(1, 'NaN'), '1 (truncated) S 0'])
def test_malformed_proc_stat_never_becomes_a_birth_identity(proc, raw):
    (proc / '1/stat').write_text(raw)
    with pytest.raises(RuntimeError, match='stat_invalid'):
        identity.read_start_ticks(1, proc_root=proc)


@pytest.mark.parametrize('record', [{}, {'identity_version': 2}, {'identity_version': True, 'start_ticks': '42'},
    {'identity_version': 1, 'start_ticks': '42'}, {'identity_version': 2, 'start_ticks': 42},
    {'identity_version': 2, 'start_ticks': '-1'}])
def test_legacy_or_untyped_record_requires_explicit_migration(record):
    with pytest.raises(RuntimeError, match='requires_identity_migration'):
        identity.record_start_ticks(record)
    assert identity.record_start_ticks({'identity_version': 2, 'start_ticks': '0'}) == '0'
