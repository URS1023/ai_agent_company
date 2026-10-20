"""源码归档和中断恢复只用临时本地文件，不运行WSL、安装器或数据库。"""

import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]


def module():
    spec = importlib.util.spec_from_file_location('wsl_sources_test', ROOT / 'deploy/native/wsl_sources.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def archive_fixture(tmp_path, monkeypatch):
    value = module()
    source = tmp_path / 'source'
    (source / 'api/empty').mkdir(parents=True)
    (source / 'api/main.py').write_text('print("local")\n', encoding='utf-8')
    for relative in ['api/extensions/storage/backend.py', 'dify-agent/src/dify_agent/storage/redis_run_store.py']:
        path = source / relative
        path.parent.mkdir(parents=True)
        path.write_text('class NativeStorage: pass', encoding='utf-8')
    (source / 'api/storage').mkdir()
    (source / 'api/storage/private-data').write_text('must-not-copy', encoding='utf-8')
    (source / 'api/pyproject.toml').write_text('[project]\nname="native-test"', encoding='utf-8')
    (source / 'api/build').mkdir()
    (source / 'api/build/generated.py').write_text('generated', encoding='utf-8')
    (source / 'api/src/package.egg-info').mkdir(parents=True)
    (source / 'api/src/package.egg-info/PKG-INFO').write_text('platform-specific', encoding='utf-8')
    (source / 'packages/dev-proxy/dist').mkdir(parents=True)
    (source / 'packages/dev-proxy/dist/cli.mjs').write_text('export{}', encoding='utf-8')
    (source / '.env').write_text('SECRET=excluded', encoding='utf-8')
    (source / '.env.example').write_text('SECRET=', encoding='utf-8')
    (source / 'node_modules').mkdir()
    (source / 'node_modules/ignored').write_text('dependency', encoding='utf-8')
    plugin = tmp_path / 'plugin.zip'
    with zipfile.ZipFile(plugin, 'w') as handle:
        handle.writestr('dify-plugin-daemon-0.6.10/cmd/server/main.go', 'package main')
        handle.writestr('dify-plugin-daemon-0.6.10/internal/storage/baidubos/bos.go', 'package baidubos')
        handle.writestr('dify-plugin-daemon-0.6.10/storage/old-data', 'must-not-copy')
    plugin_sha = value.sha(plugin)
    monkeypatch.setattr(value, 'PLUGIN_SHA', plugin_sha)
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps({'dify': {'version': '1.17.1'}, 'source_assets': [
        {'project': 'dify', 'path': 'api/main.py', 'sha256': value.sha(source / 'api/main.py')}
    ]}), encoding='utf-8')
    archive = tmp_path / 'transfer.tar'
    receipt = value.create_archive(source, plugin, lock, archive)
    return value, archive, archive.with_suffix('.tar.json'), receipt


def test_archive_preserves_locked_files_and_excludes_environment_dependencies(tmp_path, monkeypatch):
    value, archive, receipt, result = archive_fixture(tmp_path, monkeypatch)
    with tarfile.open(archive) as handle:
        assert set(handle.getnames()) == {'dify/.env.example', 'dify/api/main.py',
            'dify/api/extensions/storage/backend.py', 'dify/dify-agent/src/dify_agent/storage/redis_run_store.py',
            'dify/api/pyproject.toml', 'dify/packages/dev-proxy/dist/cli.mjs',
            'plugin/internal/storage/baidubos/bos.go', 'plugin/cmd/server/main.go', 'source-manifest.json'}
    assert result['sha256'] == value.sha(archive)
    target = tmp_path / 'sources'
    recovered = value.resume_archive(archive, receipt, target)
    assert recovered['copied_files'] == 8
    assert (target / 'dify/api/empty').is_dir()
    assert (target / 'plugin/cmd/server/main.go').read_text() == 'package main'


def test_resume_preserves_existing_exact_files_and_reports_missing(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'sources'
    (target / 'dify/api').mkdir(parents=True)
    existing = target / 'dify/api/main.py'
    existing.write_text('print("local")\n', encoding='utf-8')
    before = existing.stat().st_mtime_ns
    verified = value.resume_archive(archive, receipt, target, apply=False)
    assert verified == {'verified': True, 'existing_files': 1, 'missing_files': 7, 'applied': False}
    assert not (target / 'plugin').exists()
    result = value.resume_archive(archive, receipt, target)
    assert result['existing_files'] == 1 and result['copied_files'] == 7
    assert existing.stat().st_mtime_ns == before


def test_existing_difference_rejected_before_any_missing_file_is_created(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'sources'
    (target / 'plugin/cmd/server').mkdir(parents=True)
    path = target / 'plugin/cmd/server/main.go'
    path.write_text('unexpected change', encoding='utf-8')
    with pytest.raises(RuntimeError, match='partial_source_content_mismatch'):
        value.resume_archive(archive, receipt, target)
    assert not (target / 'dify').exists()
    assert path.read_text() == 'unexpected change'


def test_unknown_partial_file_is_not_silently_adopted(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'sources'
    target.mkdir()
    (target / 'unregistered').write_text('keep', encoding='utf-8')
    with pytest.raises(RuntimeError, match='unregistered_partial_source_path'):
        value.resume_archive(archive, receipt, target)


def test_receipt_digest_failure_never_creates_target(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    with archive.open('ab') as handle:
        handle.write(b'changed')
    with pytest.raises(RuntimeError, match='source_archive_receipt_mismatch'):
        value.resume_archive(archive, receipt, tmp_path / 'sources')
    assert not (tmp_path / 'sources').exists()


@pytest.mark.parametrize('name', ['../dify/secret', '/dify/secret', 'dify/../secret', 'dify/a\\b', 'other/file', 'dify//file'])
def test_archive_member_path_rejects_escape_or_wrong_project(name):
    with pytest.raises(RuntimeError):
        module().safe_relative(name)


def test_total_deadline_rejects_before_mutation(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    clock = iter([0, 181])
    monkeypatch.setattr(value.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError, match='source_resume_budget_exceeded'):
        value.resume_archive(archive, receipt, tmp_path / 'sources')
    assert not (tmp_path / 'sources').exists()


def test_exclusive_publication_does_not_replace_concurrent_file(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'sources'
    actual_link = value.os.link

    def race(source, destination):
        destination.write_text('concurrent file', encoding='utf-8')
        return actual_link(source, destination)

    monkeypatch.setattr(value.os, 'link', race)
    with pytest.raises(FileExistsError):
        value.resume_archive(archive, receipt, target)
    assert (target / 'dify/.env.example').read_text() == 'concurrent file'
    assert not list(target.rglob('.source-*'))


def test_locked_asset_change_does_not_publish_archive(tmp_path, monkeypatch):
    value, archive, _, _ = archive_fixture(tmp_path, monkeypatch)
    (tmp_path / 'source/api/main.py').write_text('changed', encoding='utf-8')
    with pytest.raises(RuntimeError, match='dify_source_lock_mismatch'):
        value.create_archive(tmp_path / 'source', tmp_path / 'plugin.zip', tmp_path / 'lock.json', tmp_path / 'other.tar')
    assert not (tmp_path / 'other.tar').exists()


def reviewed_fixture(tmp_path, monkeypatch):
    value, archive, receipt, _ = archive_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'sources'
    extra = target / 'dify/build/generated.py'
    extra.parent.mkdir(parents=True)
    extra.write_text('generated package output')
    entries = []
    for path in [extra.parent, extra]:
        current = path.stat()
        entry = {'path': path.relative_to(target).as_posix(), 'kind': 'directory' if path.is_dir() else 'file',
            'uid': current.st_uid, 'mode': oct(current.st_mode & 0o777)}
        if path.is_file(): entry.update(size=current.st_size, sha256=value.sha(path))
        entries.append(entry)
    audit = tmp_path / 'review.json'
    audit.write_text(json.dumps({'kind': 'native12-source-partial-audit-v1', 'source_root': str(target), 'entries': entries}))
    return value, archive, receipt, target, extra, audit


def test_reviewed_generated_files_are_kept_only_when_exact_snapshot_matches(tmp_path, monkeypatch):
    value, archive, receipt, target, extra, audit = reviewed_fixture(tmp_path, monkeypatch)
    result = value.resume_archive(archive, receipt, target, reviewed_extras=audit, reviewed_extras_sha256=value.sha(audit))
    assert result['copied_files'] == 8
    assert extra.read_text() == 'generated package output'
    assert (target / 'plugin/internal/storage/baidubos/bos.go').read_text() == 'package baidubos'


def test_reviewed_extra_modified_after_audit_is_rejected_before_writes(tmp_path, monkeypatch):
    value, archive, receipt, target, extra, audit = reviewed_fixture(tmp_path, monkeypatch)
    extra.write_text('changed package output')
    with pytest.raises(RuntimeError, match='reviewed_extra_digest_changed'):
        value.resume_archive(archive, receipt, target, reviewed_extras=audit, reviewed_extras_sha256=value.sha(audit))
    assert not (target / 'plugin').exists()


def test_extra_not_in_reviewed_snapshot_still_refuses_recovery(tmp_path, monkeypatch):
    value, archive, receipt, target, extra, audit = reviewed_fixture(tmp_path, monkeypatch)
    extra.with_name('unreviewed.py').write_text('unreviewed')
    with pytest.raises(RuntimeError, match='unregistered_partial_source_path'):
        value.resume_archive(archive, receipt, target, reviewed_extras=audit, reviewed_extras_sha256=value.sha(audit))
    assert not (target / 'plugin').exists()
