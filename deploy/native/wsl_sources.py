"""源码单包传输与中断恢复；只补经过摘要校验的缺失文件，不覆盖已有差异。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
import time
import zipfile

KIND = 'ai-agent-company-source-archive-v1'
PLUGIN_SHA = 'a3ae9b9e85e6592bd2cdd7f49ee3c5172b7bedb94758f245b7aca6378cc4bf58'
SKIP = {'.git', 'node_modules', '.venv', '__pycache__', '.next', '.turbo', '.cache', '.pytest_cache'}
DATA_PREFIXES = ('dify/storage', 'dify/api/storage', 'dify/docker/volumes', 'dify/web/dist', 'dify/dist',
    'plugin/storage', 'plugin/dist')
MAX_FILES = 100000
MAX_BYTES = 1024 ** 3


def sha(path: Path, check=lambda: None) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            check()
            result.update(chunk)
    return result.hexdigest()


def excluded(name: str) -> bool:
    return name in SKIP or name.endswith('.egg-info') or name == '.env' or name.startswith('.env.') and not name.endswith('.example')


def excluded_path(name: str) -> bool:
    """只按已知位置排除运行数据；internal/storage等同名业务源码必须保留。"""
    return any(excluded(part) for part in name.split('/')) or any(name == prefix or name.startswith(prefix + '/') for prefix in DATA_PREFIXES)


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or any(part in {'', '.', '..'} for part in value.split('/')):
        raise RuntimeError('unsafe_source_member')
    if path.parts[0] not in {'dify', 'plugin'}:
        raise RuntimeError('unexpected_source_project')
    return path


def bounded_manifest(manifest: dict) -> None:
    if manifest.get('kind') != KIND or manifest.get('filter_revision') != 2 or manifest.get('version') != '1.17.1' or manifest.get('plugin_source_sha256') != PLUGIN_SHA:
        raise RuntimeError('source_manifest_identity_mismatch')
    files = manifest.get('files')
    directories = manifest.get('directories')
    if not isinstance(files, dict) or not isinstance(directories, list) or len(files) + len(directories) > MAX_FILES:
        raise RuntimeError('source_manifest_count_exceeded')
    if len(set(directories)) != len(directories) or set(files) & set(directories):
        raise RuntimeError('source_manifest_duplicate_path')
    size = 0
    for name, value in files.items():
        path = safe_relative(name)
        if excluded_path(name):
            raise RuntimeError('source_manifest_excluded_file')
        if not isinstance(value.get('size'), int) or value['size'] < 0 or value.get('mode') not in {0o644, 0o755}:
            raise RuntimeError('source_manifest_file_invalid')
        checksum = value.get('sha256', '')
        if len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise RuntimeError('source_manifest_digest_invalid')
        size += value['size']
        for parent in path.parents:
            if str(parent) != '.' and str(parent) not in directories:
                raise RuntimeError('source_manifest_parent_missing')
    for name in directories:
        safe_relative(name)
    if size > MAX_BYTES:
        raise RuntimeError('source_manifest_size_exceeded')


class HashReader:
    """摘要取自实际写入归档的字节，避免文件在两次读取间变化。"""

    def __init__(self, handle):
        self.handle = handle
        self.value = hashlib.sha256()

    def read(self, size=-1):
        data = self.handle.read(size)
        self.value.update(data)
        return data


def create_archive(source: Path, plugin_archive: Path, lock_path: Path, output: Path) -> dict:
    """在Windows本地顺序打包；输出必须全新，凭据与旧构建目录不会进入归档。"""
    if source.is_symlink() or source.resolve() != source.absolute():
        raise RuntimeError('source_root_redirected')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if lock['dify']['version'] != '1.17.1' or sha(plugin_archive) != PLUGIN_SHA:
        raise RuntimeError('source_release_lock_mismatch')
    receipt_path = output.with_suffix(output.suffix + '.json')
    pending = output.with_suffix(output.suffix + '.pending')
    if output.exists() or pending.exists() or receipt_path.exists():
        raise RuntimeError('source_archive_destination_exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'kind': KIND, 'filter_revision': 2, 'version': '1.17.1', 'plugin_source_sha256': PLUGIN_SHA, 'directories': [], 'files': {}}

    def add(handle, name, size, mode, data):
        safe_relative(name)
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = size, mode, 0
        reader = HashReader(data)
        handle.addfile(info, reader)
        manifest['files'][name] = {'size': size, 'mode': mode, 'sha256': reader.value.hexdigest()}

    with tarfile.open(pending, 'w', format=tarfile.PAX_FORMAT) as archive:
        for current, folders, names in os.walk(source, followlinks=False):
            folder = Path(current)
            folders[:] = sorted(name for name in folders if not excluded_path('dify/' + (folder / name).relative_to(source).as_posix()))
            # setuptools产物只在有Python项目声明的目录排除；其他模块的同名build/dist不猜用途。
            if 'pyproject.toml' in names or 'setup.py' in names:
                folders[:] = [name for name in folders if name != 'build']
            relative = folder.relative_to(source).as_posix()
            manifest['directories'].append('dify' + ('/' + relative if relative != '.' else ''))
            if any((folder / name).is_symlink() for name in folders + names if not excluded(name)):
                raise RuntimeError('source_symlink_not_copied')
            for name in sorted(names):
                if excluded_path('dify/' + (folder / name).relative_to(source).as_posix()):
                    continue
                path = folder / name
                if not stat.S_ISREG(path.stat().st_mode):
                    raise RuntimeError('source_special_file_not_copied')
                key = 'dify/' + path.relative_to(source).as_posix()
                mode = 0o755 if path.suffix == '.sh' else 0o644
                with path.open('rb') as data:
                    add(archive, key, path.stat().st_size, mode, data)
        with zipfile.ZipFile(plugin_archive) as plugin:
            for item in plugin.infolist():
                name = item.filename
                if '\\' in name or not name.startswith('dify-plugin-daemon-0.6.10/') or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError('plugin_source_member_invalid')
                relative = name.removeprefix('dify-plugin-daemon-0.6.10/').rstrip('/')
                if not relative:
                    continue
                key = 'plugin/' + relative
                safe_relative(key)
                if excluded_path(key):
                    continue
                if item.is_dir():
                    manifest['directories'].append(key)
                else:
                    if key in manifest['files']:
                        raise RuntimeError('duplicate_plugin_source_file')
                    with plugin.open(item) as data:
                        add(archive, key, item.file_size, 0o755 if relative.endswith('.sh') else 0o644, data)
        for name in list(manifest['files']) + list(manifest['directories']):
            manifest['directories'].extend(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
        manifest['directories'] = sorted(set(manifest['directories']), key=lambda name: (name.count('/'), name))
        for item in lock['source_assets']:
            if item['project'] == 'dify' and manifest['files'].get('dify/' + item['path'], {}).get('sha256') != item['sha256']:
                raise RuntimeError('dify_source_lock_mismatch')
        manifest['verified_assets'] = [item for item in lock['source_assets'] if item['project'] == 'dify']
        bounded_manifest(manifest)
        body = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode()
        info = tarfile.TarInfo('source-manifest.json')
        info.size, info.mode = len(body), 0o644
        archive.addfile(info, io.BytesIO(body))
    pending.replace(output)
    result = {'kind': KIND, 'archive': output.name, 'sha256': sha(output), 'size': output.stat().st_size,
        'manifest_sha256': hashlib.sha256(body).hexdigest(), 'filter_revision': 2, 'file_count': len(manifest['files']),
        'expanded_bytes': sum(item['size'] for item in manifest['files'].values())}
    receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def child_path(root: Path, name: str) -> Path:
    path = root.joinpath(*safe_relative(name).parts)
    for current in [path, *path.parents]:
        if current == root:
            break
        if current.is_symlink():
            raise RuntimeError('partial_source_symlink')
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('partial_source_escape')
    return path


def resume_archive(archive_path: Path, receipt_path: Path, target: Path, *, seconds=180, apply=True,
        reviewed_extras: Path | None = None, reviewed_extras_sha256: str | None = None) -> dict:
    """先完整核对归档和已有文件，再原子补齐缺失；不重写中断历史与已有内容。"""
    deadline = time.monotonic() + seconds

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('source_resume_budget_exceeded')

    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('kind') != KIND or archive_path.stat().st_size != receipt.get('size') or sha(archive_path, check) != receipt.get('sha256'):
        raise RuntimeError('source_archive_receipt_mismatch')
    if target.is_symlink() or target.resolve() != target.absolute():
        raise RuntimeError('source_destination_redirected')
    reviewed = {}
    if reviewed_extras:
        if not reviewed_extras_sha256 or sha(reviewed_extras, check) != reviewed_extras_sha256 or reviewed_extras.stat().st_size > 4 * 1024 ** 2:
            raise RuntimeError('reviewed_extras_digest_mismatch')
        audit = json.loads(reviewed_extras.read_text())
        if audit.get('kind') != 'native12-source-partial-audit-v1' or audit.get('source_root') != str(target):
            raise RuntimeError('reviewed_extras_target_mismatch')
        for entry in audit['entries']:
            safe_relative(entry['path'])
            if entry['path'] in reviewed:
                raise RuntimeError('duplicate_reviewed_extra')
            reviewed[entry['path']] = entry
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES + 1 or len({member.name for member in members}) != len(members):
            raise RuntimeError('source_archive_member_budget_or_duplicate')
        meta = archive.getmember('source-manifest.json')
        if not meta.isfile() or meta.size > 24 * 1024 ** 2:
            raise RuntimeError('source_manifest_invalid')
        body = archive.extractfile(meta).read()
        if hashlib.sha256(body).hexdigest() != receipt.get('manifest_sha256'):
            raise RuntimeError('source_manifest_digest_mismatch')
        manifest = json.loads(body)
        bounded_manifest(manifest)
        files = manifest['files']
        if set(member.name for member in members) != set(files) | {'source-manifest.json'}:
            raise RuntimeError('source_archive_manifest_members_mismatch')
        # 所有归档内容及现有文件在写入前校验；已存在的非源码额外文件也禁止悄悄忽略。
        if target.exists():
            observed_extras = set()
            for current, folders, names in os.walk(target, followlinks=False):
                for name in folders + names:
                    path = Path(current) / name
                    relative = path.relative_to(target).as_posix()
                    if path.is_symlink():
                        raise RuntimeError('unregistered_partial_source_path')
                    if relative not in files and relative not in manifest['directories']:
                        check()
                        entry = reviewed.get(relative)
                        if not entry:
                            raise RuntimeError('unregistered_partial_source_path:' + relative)
                        expected_kind = 'directory' if path.is_dir() else 'file'
                        actual = path.stat()
                        if entry['kind'] != expected_kind or entry['uid'] != actual.st_uid or entry['mode'] != oct(actual.st_mode & 0o777):
                            raise RuntimeError('reviewed_extra_identity_changed')
                        if expected_kind == 'file' and (actual.st_size != entry['size'] or sha(path, check) != entry['sha256']):
                            raise RuntimeError('reviewed_extra_digest_changed')
                        observed_extras.add(relative)
            if observed_extras != set(reviewed):
                raise RuntimeError('reviewed_extra_set_changed')
        missing = []
        existing = 0
        for name, expected in files.items():
            check()
            member = archive.getmember(name)
            if not member.isfile() or member.size != expected['size']:
                raise RuntimeError('source_archive_member_invalid')
            value = hashlib.sha256()
            with archive.extractfile(member) as handle:
                while chunk := handle.read(1024 * 1024):
                    check()
                    value.update(chunk)
            if value.hexdigest() != expected['sha256']:
                raise RuntimeError('source_archive_file_digest_mismatch')
            path = child_path(target, name)
            if path.exists():
                if not path.is_file() or path.stat().st_size != expected['size'] or sha(path, check) != expected['sha256']:
                    raise RuntimeError('partial_source_content_mismatch:' + name)
                existing += 1
            else:
                missing.append(name)
        if not apply:
            return {'verified': True, 'existing_files': existing, 'missing_files': len(missing), 'applied': False}
        target.mkdir(exist_ok=True)
        for name in manifest['directories']:
            check()
            child_path(target, name).mkdir(exist_ok=True)
        for name in missing:
            check()
            path = child_path(target, name)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.source-', delete=False) as output, archive.extractfile(name) as handle:
                    temporary = Path(output.name)
                    while chunk := handle.read(1024 * 1024):
                        check()
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.chmod(files[name]['mode'])
                # hardlink提供不可覆盖的原子发布；并发出现的文件也不能被replace覆写。
                os.link(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return {'verified': True, 'existing_files': existing, 'copied_files': len(missing), 'applied': True,
            'version': '1.17.1', 'filter_revision': manifest['filter_revision'], 'verified_assets': manifest['verified_assets'], 'plugin_source_sha256': PLUGIN_SHA,
            'archive_sha256': receipt['sha256'], 'original_sources_attempt_preserved': True}


def audit_archive(archive_path: Path, receipt_path: Path, target: Path, output: Path) -> dict:
    """只读核对全部差异和额外文件；生成供人工审阅的精确快照，不补源、不运行服务。"""
    if os.name != 'posix' or os.environ.get('WSL_DISTRO_NAME') != 'ai-agent-company' or target != Path('/opt/ai-agent-company/sources'):
        raise RuntimeError('source_audit_requires_owned_wsl_target')
    if target.resolve() != target or output.exists():
        raise RuntimeError('source_audit_path_invalid')
    receipt = json.loads(receipt_path.read_text())
    deadline = time.monotonic() + 180
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('source_audit_deadline')
    if sha(archive_path, check) != receipt['sha256']:
        raise RuntimeError('source_audit_archive_changed')
    with tarfile.open(archive_path) as archive:
        body = archive.extractfile('source-manifest.json').read()
    if hashlib.sha256(body).hexdigest() != receipt['manifest_sha256']:
        raise RuntimeError('source_audit_manifest_changed')
    manifest = json.loads(body)
    bounded_manifest(manifest)
    expected = set(manifest['files']) | set(manifest['directories'])
    extras, mismatches, missing = [], [], []
    for current, folders, files in os.walk(target, followlinks=False):
        check()
        for name in folders + files:
            path = Path(current) / name
            relative = path.relative_to(target).as_posix()
            if path.is_symlink():
                raise RuntimeError('source_audit_symlink')
            if relative in expected:
                continue
            current_stat = path.stat()
            entry = {'path': relative, 'kind': 'directory' if path.is_dir() else 'file',
                'uid': current_stat.st_uid, 'mode': oct(current_stat.st_mode & 0o777)}
            if path.is_file(): entry.update(size=current_stat.st_size, sha256=sha(path, check))
            extras.append(entry)
    for name, expected_file in manifest['files'].items():
        check()
        path = child_path(target, name)
        if not path.exists():
            missing.append(name)
        elif not path.is_file() or sha(path, check) != expected_file['sha256']:
            mismatches.append(name)
    result = {'kind': 'native12-source-partial-audit-v1', 'source_root': str(target),
        'archive_filter_revision': manifest['filter_revision'], 'archive_sha256': receipt['sha256'],
        'entries': extras, 'extra_count': len(extras), 'mismatches': mismatches, 'missing': missing,
        'database_requests': 0, 'service_starts': 0}
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return {'extra_count': len(extras), 'mismatch_count': len(mismatches), 'missing_count': len(missing),
        'report_sha256': sha(output, check), 'output': str(output), 'database_requests': 0, 'service_starts': 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='生成锁定源码单包，不启动WSL或安装依赖')
    parser.add_argument('operation', choices=['sources-archive', 'audit-resume'])
    for name in ['source', 'plugin-archive', 'lock', 'archive', 'archive-receipt', 'target']:
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == 'sources-archive':
        if not all([args.source, args.plugin_archive, args.lock]): parser.error('需要source、plugin-archive、lock')
        result = create_archive(args.source, args.plugin_archive, args.lock, args.output)
    else:
        if not all([args.archive, args.archive_receipt, args.target]): parser.error('需要archive、archive-receipt、target')
        result = audit_archive(args.archive, args.archive_receipt, args.target, args.output)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
