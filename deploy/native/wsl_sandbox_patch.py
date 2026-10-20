"""锁定官方0.2.15源码的最小兼容补丁；只接受已知前后摘要，不运行或构建沙箱。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

SOURCE_COMMIT = '5631afef06ec88f80c28129aec7fd22a30006b14'
SOURCE_FILE = Path('internal/core/runner/nodejs/nodejs.go')
# 以下旧补丁常量只用于识别和撤回历史错误，禁止重新施加。
PATCH_ID = 'dify-sandbox-0.2.15-js-script-path-v1'
ORIGINAL_SHA256 = '1c492ae51b55dbcc70911eab71a6e344987452d659f6aa6be68a1ba98976dcae'
PATCHED_SHA256 = '4d7fb1bd40abbd21ff1a0f5e9fcb3b780e6431d5b9f425d9f320c7620ec83508'
OLD_LINE = b'\tscript_path := path.Join(root_path, LIB_PATH, PROJECT_NAME, "node_temp/node_temp/test.js")\n'
NEW_LINE = b'\tscript_path := path.Join(root_path, LIB_PATH, PROJECT_NAME, "node_temp/test.js")\n'
PATCH_TEXT = (
    '--- a/internal/core/runner/nodejs/nodejs.go\n'
    '+++ b/internal/core/runner/nodejs/nodejs.go\n'
    '@@ -153 +153 @@\n'
    '-' + OLD_LINE.decode() + '+' + NEW_LINE.decode()
)
RESTORE_PATCH_ID = 'dify-sandbox-0.2.15-js-script-path-restore-upstream-v1'
RESTORE_PATCH_TEXT = (
    '--- a/internal/core/runner/nodejs/nodejs.go\n'
    '+++ b/internal/core/runner/nodejs/nodejs.go\n'
    '@@ -153 +153 @@\n'
    '-' + NEW_LINE.decode() + '+' + OLD_LINE.decode()
)
PYTHON_SOURCE_FILE = Path('internal/static/python_syscall/syscalls_amd64.go')
PYTHON_PATCH_ID = 'dify-sandbox-0.2.15-python-stat-v1'
PYTHON_ORIGINAL_SHA256 = '7b6d631dd181747a263dfe5206717822813e7a4c698c0b5f22cb2f9d94296e47'
PYTHON_PATCHED_SHA256 = '51293b4aea72600f18f849073f8bbd41e7dd478604dd6eff976cf24a55ac68e6'
PYTHON_OLD_LINE = b'\tsyscall.SYS_NEWFSTATAT, syscall.SYS_FSTAT, syscall.SYS_FCNTL, syscall.SYS_IOCTL, syscall.SYS_LSEEK, syscall.SYS_GETDENTS64,\n'
PYTHON_NEW_LINE = b'\tsyscall.SYS_STAT, syscall.SYS_NEWFSTATAT, syscall.SYS_FSTAT, syscall.SYS_FCNTL, syscall.SYS_IOCTL, syscall.SYS_LSEEK, syscall.SYS_GETDENTS64,\n'
PYTHON_PATCH_TEXT = (
    '--- a/internal/static/python_syscall/syscalls_amd64.go\n'
    '+++ b/internal/static/python_syscall/syscalls_amd64.go\n'
    '@@ -15 +15 @@\n'
    '-' + PYTHON_OLD_LINE.decode() + '+' + PYTHON_NEW_LINE.decode()
)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def prepare_patch(content: bytes) -> tuple[bytes, dict]:
    """拒绝旧调用方重新施加错误JS路径补丁，避免过期安装脚本静默回退。"""
    raise RuntimeError('sandbox_node_script_path_patch_retired_use_restore')


def prepare_node_script_path_restore(content: bytes) -> tuple[bytes, dict]:
    """撤回已知单层路径错误；上游临时目录先创建目的目录再复制，双层路径是实际布局。"""
    return prepare_known_patch(content, SOURCE_FILE, RESTORE_PATCH_ID, PATCHED_SHA256, ORIGINAL_SHA256,
        NEW_LINE, OLD_LINE, RESTORE_PATCH_TEXT, {
            'reverses_patch_id': PATCH_ID, 'historical_patch_sha256': sha256(PATCH_TEXT.encode()),
            'source_current_sha256': ORIGINAL_SHA256,
            'current_script_path': 'node_temp/node_temp/test.js',
            'reason': 'temp_dir_runner_copies_required_directory_into_precreated_destination',
            'seccomp_preserved': True,
        })


def prepare_python_stat_patch(content: bytes) -> tuple[bytes, dict]:
    """只允许audit已经证实缺失的stat；不替换默认表，也不触碰网络条件和拒绝动作。"""
    return prepare_known_patch(content, PYTHON_SOURCE_FILE, PYTHON_PATCH_ID,
        PYTHON_ORIGINAL_SHA256, PYTHON_PATCHED_SHA256, PYTHON_OLD_LINE, PYTHON_NEW_LINE,
        PYTHON_PATCH_TEXT, {'seccomp_enforced': True, 'seccomp_preserved': False,
            'seccomp_default_action_preserved': True, 'network_policy_preserved': True,
            'allowed_syscalls_added': [{'architecture': 'linux-amd64', 'number': 4, 'name': 'stat'}]})


def prepare_known_patch(content: bytes, source_file: Path, patch_id: str, original_sha: str,
        patched_sha: str, old_line: bytes, new_line: bytes, patch_text: str, boundaries: dict) -> tuple[bytes, dict]:
    """固定单行修正共用摘要校验，未知来源一律拒绝，不模糊匹配。"""
    observed = sha256(content)
    if observed == patched_sha:
        patched, applied = content, False
    elif observed == original_sha:
        if content.count(old_line) != 1:
            raise RuntimeError('sandbox_patch_original_line_mismatch')
        patched, applied = content.replace(old_line, new_line, 1), True
        if sha256(patched) != patched_sha:
            raise RuntimeError('sandbox_patch_result_mismatch')
    else:
        raise RuntimeError('sandbox_patch_source_unrecognized')
    return patched, {
        'patch_id': patch_id, 'source_commit': SOURCE_COMMIT, 'source_file': source_file.as_posix(),
        'source_before_sha256': original_sha, 'source_after_sha256': patched_sha,
        'observed_before_sha256': observed, 'patch_sha256': sha256(patch_text.encode()),
        'patch_text': patch_text, 'applied': applied, **boundaries,
    }


def apply_node_script_path_patch(source_root: Path) -> dict:
    """保留旧入口的明确失败，避免其他安装调用再次引入已撤回的错误。"""
    raise RuntimeError('sandbox_node_script_path_patch_retired_use_restore')


def restore_node_script_path(source_root: Path) -> dict:
    """仅将已知错误版本恢复官方文件；返回独立纠正收据，不读取或覆盖历史安装记录。"""
    return apply_known_patch(source_root, SOURCE_FILE, prepare_node_script_path_restore, ORIGINAL_SHA256)


def apply_python_stat_patch(source_root: Path) -> dict:
    """原子补充Python默认元数据查询白名单；其他规则文件不在写入范围内。"""
    return apply_known_patch(source_root, PYTHON_SOURCE_FILE, prepare_python_stat_patch, PYTHON_PATCHED_SHA256)


def apply_known_patch(source_root: Path, source_file: Path, prepare, expected_sha: str) -> dict:
    """保持相同的路径、并发和原子写入边界，所有修正均拒绝未知源和并发版本漂移。"""
    root = Path(source_root).absolute()
    target = root / source_file
    if root.resolve() != root or target.resolve() != target or not target.is_file():
        raise RuntimeError('sandbox_patch_source_path_invalid')
    original_stat = target.stat()
    if original_stat.st_size > 1024 * 1024:
        raise RuntimeError('sandbox_patch_source_size_invalid')
    original = target.read_bytes()
    patched, receipt = prepare(original)
    if not receipt['applied']:
        return receipt
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.nodejs-path-', suffix='.pending', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(patched)
            handle.flush()
            os.fsync(handle.fileno())
        pending.chmod(stat.S_IMODE(original_stat.st_mode))
        if os.name == 'posix':
            os.chown(pending, original_stat.st_uid, original_stat.st_gid)
        current = target.stat()
        if (target.resolve() != target or current.st_ino != original_stat.st_ino
                or target.read_bytes() != original):
            raise RuntimeError('sandbox_patch_source_changed')
        pending.replace(target)
        pending = None
        if sha256(target.read_bytes()) != expected_sha:
            raise RuntimeError('sandbox_patch_written_digest_mismatch')
        return receipt
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--patch', choices=['node-script-path', 'python-stat'], default='python-stat')
    operation.add_argument('--restore-node-script-path', action='store_true')
    args = parser.parse_args()
    if args.restore_node_script_path:
        apply = restore_node_script_path
    else:
        apply = apply_python_stat_patch if args.patch == 'python-stat' else apply_node_script_path_patch
    print(json.dumps(apply(args.source_root), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
