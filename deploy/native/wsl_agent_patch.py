"""为锁定Dify 1.17.1的Agent补充可配置模型请求预算，不改变默认能力或启动服务。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


PATCH_ID = 'dify-1.17.1-agent-request-limit-v1'
SOURCE_VERSION = '1.17.1'
DEFAULT_REQUEST_LIMIT = 500
ENVIRONMENT_VARIABLE = 'DIFY_AGENT_RUN_REQUEST_LIMIT'
MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FilePatch:
    """一份完整文件的前后摘要与精确替换，不接受仅靠版本号或部分文本推断来源。"""

    path: str
    original_sha256: str
    patched_sha256: str
    replacements: tuple[tuple[str, str], ...]


PATCHES = (
    FilePatch('server/settings.py',
        'ac9cb78cfc32a46eacc2850b447ab97dacdbcdee2fe824498e09eec62039e5d5',
        '0ebbc8dadc61354ff4e8d9e37a21b8845b6685af84408bb8a7abd2452024a5f2', (
            ('    run_timeout_seconds: float = Field(default=DEFAULT_AGENT_RUN_TIMEOUT_SECONDS, gt=0)\n',
             '    run_timeout_seconds: float = Field(default=DEFAULT_AGENT_RUN_TIMEOUT_SECONDS, gt=0)\n'
             '    # 保留原生500次默认，只允许部署按实际预算收紧模型请求次数。\n'
             '    run_request_limit: int = Field(default=500, ge=1)\n'),
        )),
    FilePatch('server/app.py',
        'b988fb893bae8e4265c578315ab2156b6a2fdd64b7cbce7176d1c448941f43eb',
        'e89fa8ddf2923f663e41d5eb0725a591ba68983bb8f87c577598f660e5aea0e4', (
            ('            run_timeout_seconds=resolved_settings.run_timeout_seconds,\n',
             '            run_timeout_seconds=resolved_settings.run_timeout_seconds,\n'
             '            run_request_limit=resolved_settings.run_request_limit,\n'),
        )),
    FilePatch('runtime/run_scheduler.py',
        '74924d789a1cb3dedc309e34de676b4887097e14e91ed365b071dfaa84581db3',
        '2ebebe81d36de4e92da9a7dca912ea1d6da3d9b9ab0366298ac03a6cdc7c0205', (
            ('    run_timeout_seconds: float\n', '    run_timeout_seconds: float\n    run_request_limit: int\n'),
            ('        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,\n',
             '        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,\n'
             '        run_request_limit: int = 500,\n'),
            ('        self.store = store\n',
             '        # 构造器也校验，避免绕过ServerSettings时产生无限或无效预算。\n'
             '        if isinstance(run_request_limit, bool) or not isinstance(run_request_limit, int) or run_request_limit < 1:\n'
             '            raise ValueError("run_request_limit must be a positive integer")\n'
             '        self.store = store\n'),
            ('        self.run_timeout_seconds = run_timeout_seconds\n',
             '        self.run_timeout_seconds = run_timeout_seconds\n        self.run_request_limit = run_request_limit\n'),
            ('            run_timeout_seconds=self.run_timeout_seconds,\n',
             '            run_timeout_seconds=self.run_timeout_seconds,\n'
             '            run_request_limit=self.run_request_limit,\n'),
        )),
    FilePatch('runtime/runner.py',
        '3e0732f5d0c5c224459194a72eca61c8e54f2ece6d6d4119be3c46d622c3c194',
        '1e07115da5de09bb578395e36d75481e09529b048f6b45521337883f874d881f', (
            ('    run_timeout_seconds: float\n', '    run_timeout_seconds: float\n    run_request_limit: int\n'),
            ('        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,\n',
             '        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,\n'
             '        run_request_limit: int = _MAX_AGENT_STEPS_PER_RUN,\n'),
            ('        if stream_text_delta_flush_interval_seconds <= 0:\n',
             '        # 预算约束模型请求次数，不修改工具、能力或原生500次默认。\n'
             '        if isinstance(run_request_limit, bool) or not isinstance(run_request_limit, int) or run_request_limit < 1:\n'
             '            raise ValueError("run_request_limit must be a positive integer")\n'
             '        if stream_text_delta_flush_interval_seconds <= 0:\n'),
            ('        self.run_timeout_seconds = run_timeout_seconds\n',
             '        self.run_timeout_seconds = run_timeout_seconds\n        self.run_request_limit = run_request_limit\n'),
            ('                            usage_limits=UsageLimits(request_limit=_MAX_AGENT_STEPS_PER_RUN),\n',
             '                            usage_limits=UsageLimits(request_limit=self.run_request_limit),\n'),
        )),
)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def prepare_file(content: bytes, patch: FilePatch) -> tuple[bytes, dict]:
    """先校验整文件，已完成时保持原inode；未知版本及结果摘要不符立即拒绝。"""
    observed = sha256(content)
    if observed == patch.patched_sha256:
        output, applied = content, False
    elif observed == patch.original_sha256:
        output = content
        for old, new in patch.replacements:
            if output.count(old.encode()) != 1:
                raise RuntimeError('agent_patch_original_text_mismatch:' + patch.path)
            output = output.replace(old.encode(), new.encode(), 1)
        if sha256(output) != patch.patched_sha256:
            raise RuntimeError('agent_patch_result_digest_mismatch:' + patch.path)
        applied = True
    else:
        raise RuntimeError('agent_patch_source_unrecognized:' + patch.path)
    return output, {'path': patch.path, 'source_before_sha256': patch.original_sha256,
        'source_after_sha256': patch.patched_sha256, 'observed_before_sha256': observed,
        'applied': applied}


def _target(root: Path, patch: FilePatch) -> Path:
    path = root / patch.path
    if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise RuntimeError('agent_patch_source_path_invalid:' + patch.path)
    return path


def prepare_group(package_root: Path) -> tuple[list[tuple[Path, bytes, bytes, os.stat_result]], dict]:
    """四文件在写入前全部预检；中断留下的已知新旧混合版本可显式再次完成。"""
    root = Path(package_root).absolute()
    if not root.is_dir() or root.resolve() != root:
        raise RuntimeError('agent_patch_package_root_invalid')
    prepared, files, diffs = [], [], []
    for patch in PATCHES:
        path = _target(root, patch)
        before = path.stat()
        original = path.read_bytes()
        output, receipt = prepare_file(original, patch)
        prepared.append((path, original, output, before))
        files.append(receipt)
        if receipt['applied']:
            diffs.extend(difflib.unified_diff(original.decode().splitlines(keepends=True),
                output.decode().splitlines(keepends=True), fromfile='a/' + patch.path,
                tofile='b/' + patch.path))
    return prepared, {'patch_id': PATCH_ID, 'source_version': SOURCE_VERSION,
        'package_root': str(root), 'files': files, 'applied': any(item['applied'] for item in files),
        'default_request_limit': DEFAULT_REQUEST_LIMIT, 'environment_variable': ENVIRONMENT_VARIABLE,
        'patch_text': ''.join(diffs), 'shell_capability_changed': False,
        'database_requests': 0, 'model_requests': 0}


def _write_file(path: Path, original: bytes, output: bytes, before: os.stat_result) -> None:
    """文件原子替换且保留属主/权限；提交前复核inode和内容，拒绝覆盖其他作者的新版本。"""
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.agent-budget-', suffix='.pending', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(output)
            handle.flush()
            os.fsync(handle.fileno())
        pending.chmod(stat.S_IMODE(before.st_mode))
        if os.name == 'posix':
            os.chown(pending, before.st_uid, before.st_gid)
        current = path.stat()
        if path.resolve() != path or current.st_ino != before.st_ino or path.read_bytes() != original:
            raise RuntimeError('agent_patch_source_changed:' + path.name)
        pending.replace(path)
        pending = None
        if sha256(path.read_bytes()) != sha256(output):
            raise RuntimeError('agent_patch_written_digest_mismatch:' + path.name)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def apply_patch(package_root: Path, *, verify_only: bool = False) -> dict:
    """只处理明确包路径；独占锁不启动安装/服务，失败保留已有文件以供人工审阅。"""
    root = Path(package_root).absolute()
    if verify_only:
        _, receipt = prepare_group(root)
        return {**receipt, 'verified_only': True, 'applied': False,
            'changes_required': receipt['applied']}
    if not root.is_dir() or root.resolve() != root:
        raise RuntimeError('agent_patch_package_root_invalid')
    lock = root / '.agent-request-limit.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        prepared, receipt = prepare_group(root)
        # 这是逐文件原子操作，不虚称跨四文件事务；执行前须停止该所属Agent进程。
        for path, original, output, before in prepared:
            if output != original:
                _write_file(path, original, output, before)
        return receipt
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(apply_patch(args.package_root, verify_only=args.verify_only), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
