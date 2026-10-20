"""在tmp_path片段上验证补丁边界，不读取WSL、不执行Go/沙箱或连接任何服务。"""

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def tool():
    spec = importlib.util.spec_from_file_location('wsl_sandbox_patch', ROOT / 'deploy/native/wsl_sandbox_patch.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample(module, tmp_path, monkeypatch):
    # 行为用极小Go片段替身；生产模块固定的真实整文件SHA另行保持原值。
    original = b'package nodejs\n\nfunc setup() {\n' + module.OLD_LINE + b'}\n'
    patched = original.replace(module.OLD_LINE, module.NEW_LINE)
    monkeypatch.setattr(module, 'ORIGINAL_SHA256', hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(module, 'PATCHED_SHA256', hashlib.sha256(patched).hexdigest())
    target = tmp_path / module.SOURCE_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(patched)
    return target, original, patched


def test_production_fingerprints_and_single_line_contract_are_pinned():
    module = tool()
    assert module.SOURCE_COMMIT == '5631afef06ec88f80c28129aec7fd22a30006b14'
    assert module.ORIGINAL_SHA256 == '1c492ae51b55dbcc70911eab71a6e344987452d659f6aa6be68a1ba98976dcae'
    assert module.PATCHED_SHA256 == '4d7fb1bd40abbd21ff1a0f5e9fcb3b780e6431d5b9f425d9f320c7620ec83508'
    assert module.PATCH_TEXT.count('\n-\t') == module.PATCH_TEXT.count('\n+\t') == 1
    assert 'node_temp/node_temp/test.js' in module.PATCH_TEXT and 'node_temp/test.js' in module.PATCH_TEXT
    assert module.RESTORE_PATCH_ID != module.PATCH_ID
    assert module.RESTORE_PATCH_TEXT.endswith('-' + module.NEW_LINE.decode() + '+' + module.OLD_LINE.decode())


def test_python_stat_patch_pins_audited_source_and_only_adds_one_metadata_syscall():
    module = tool()
    assert module.PYTHON_ORIGINAL_SHA256 == '7b6d631dd181747a263dfe5206717822813e7a4c698c0b5f22cb2f9d94296e47'
    assert module.PYTHON_PATCHED_SHA256 == '51293b4aea72600f18f849073f8bbd41e7dd478604dd6eff976cf24a55ac68e6'
    assert module.PYTHON_NEW_LINE.replace(b'syscall.SYS_STAT, ', b'', 1) == module.PYTHON_OLD_LINE
    assert module.PYTHON_PATCH_TEXT.count('\n-\t') == module.PYTHON_PATCH_TEXT.count('\n+\t') == 1
    assert 'ALLOW_NETWORK_SYSCALLS' not in module.PYTHON_PATCH_TEXT
    assert 'ALLOWED_SYSCALLS' not in module.PYTHON_PATCH_TEXT


def test_python_stat_patch_is_atomic_idempotent_and_reports_changed_whitelist_truthfully(tmp_path, monkeypatch):
    module = tool()
    original = b'package python_syscall\nvar ALLOW_SYSCALLS = []int{\n' + module.PYTHON_OLD_LINE + b'}\n'
    patched = original.replace(module.PYTHON_OLD_LINE, module.PYTHON_NEW_LINE)
    monkeypatch.setattr(module, 'PYTHON_ORIGINAL_SHA256', hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(module, 'PYTHON_PATCHED_SHA256', hashlib.sha256(patched).hexdigest())
    target = tmp_path / module.PYTHON_SOURCE_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(original)
    receipt = module.apply_python_stat_patch(tmp_path)
    assert target.read_bytes() == patched and receipt['applied']
    assert receipt['seccomp_enforced'] and receipt['seccomp_preserved'] is False
    assert receipt['seccomp_default_action_preserved'] and receipt['network_policy_preserved']
    assert receipt['allowed_syscalls_added'] == [{'architecture': 'linux-amd64', 'number': 4, 'name': 'stat'}]
    assert module.apply_python_stat_patch(tmp_path)['applied'] is False
    target.write_bytes(patched + b'unknown edit\n')
    with pytest.raises(RuntimeError, match='source_unrecognized'):
        module.apply_python_stat_patch(tmp_path)


def setup_tool():
    spec = importlib.util.spec_from_file_location('wsl_setup', ROOT / 'deploy/native/wsl_setup.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sandbox_toolchain_uses_locked_cached_archive_and_leaves_plugin_go_untouched(tmp_path, monkeypatch):
    module = setup_tool()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'tooling/go/bin').mkdir(parents=True)
    (tmp_path / 'tooling/go/bin/go').write_bytes(b'plugin-go-1.26.10')
    archive = tmp_path / 'go1.24.13.linux-amd64.tar.gz'
    archive.write_bytes(b'fake-archive')
    observed = []
    def download(url, filename, expected_sha):
        assert url == 'https://go.dev/dl/go1.24.13.linux-amd64.tar.gz'
        assert filename == archive.name
        assert expected_sha == '1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730'
        observed.append('cached-download')
        return archive
    def extract(path, target):
        assert path == archive
        (target / 'go/bin').mkdir(parents=True)
        (target / 'go/VERSION').write_text('go1.24.13\ntime 2026-01-01T00:00:00Z\n')
        (target / 'go/bin/go').write_bytes(b'official-sandbox-go')
    monkeypatch.setattr(module, 'download', download)
    monkeypatch.setattr(module, 'extract_tar', extract)
    monkeypatch.setattr(module, 'command', lambda *a, **kw: pytest.fail('安装工具不应执行构建或业务进程'))
    receipt = module.setup_sandbox_toolchain()
    assert receipt['version'] == '1.24.13'
    assert module.GO == '1.26.10'
    assert (tmp_path / 'tooling/go/bin/go').read_bytes() == b'plugin-go-1.26.10'
    assert module.sandbox_go_binary() == str(tmp_path / 'tooling/go-sandbox-1.24.13/bin/go')
    assert module.setup_sandbox_toolchain()['reused']
    assert observed == ['cached-download']
    binary = tmp_path / 'tooling/go-sandbox-1.24.13/bin/go'
    binary.write_bytes(b'unknown-new-binary')
    with pytest.raises(RuntimeError, match='sandbox_go_binary_changed'):
        module.setup_sandbox_toolchain()


def test_sandbox_toolchain_refuses_unregistered_existing_install(tmp_path, monkeypatch):
    module = setup_tool()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'tooling/go-sandbox-1.24.13').mkdir(parents=True)
    monkeypatch.setattr(module, 'download', lambda *a: pytest.fail('不得覆盖未登记目录后重下'))
    with pytest.raises(FileNotFoundError):
        module.setup_sandbox_toolchain()


def test_known_wrong_path_is_restored_once_and_second_call_is_idempotent(tmp_path, monkeypatch):
    module = tool()
    target, original, patched = sample(module, tmp_path, monkeypatch)
    before_mode = target.stat().st_mode
    historical_receipt = tmp_path / 'sandbox-receipt.json'
    historical_bytes = b'{"js_patch":{"patch_id":"dify-sandbox-0.2.15-js-script-path-v1","applied":true}}'
    historical_receipt.write_bytes(historical_bytes)
    first = module.restore_node_script_path(tmp_path)
    assert target.read_bytes() == original and original != patched
    assert first['applied'] is True and first['seccomp_preserved'] is True
    assert first['source_before_sha256'] == first['observed_before_sha256'] == hashlib.sha256(patched).hexdigest()
    assert first['source_after_sha256'] == first['source_current_sha256'] == hashlib.sha256(original).hexdigest()
    assert first['patch_id'] == module.RESTORE_PATCH_ID
    assert first['reverses_patch_id'] == module.PATCH_ID
    assert first['historical_patch_sha256'] == hashlib.sha256(module.PATCH_TEXT.encode()).hexdigest()
    assert first['current_script_path'] == 'node_temp/node_temp/test.js'
    assert first['patch_sha256'] == hashlib.sha256(first['patch_text'].encode()).hexdigest()
    assert target.stat().st_mode == before_mode
    inode = target.stat().st_ino
    second = module.restore_node_script_path(tmp_path)
    assert second['applied'] is False and target.stat().st_ino == inode
    assert second['observed_before_sha256'] == hashlib.sha256(original).hexdigest()
    assert target.read_bytes() == original
    assert historical_receipt.read_bytes() == historical_bytes
    assert list(target.parent.glob('.nodejs-path-*.pending')) == []


def test_fresh_official_source_is_preserved_and_retired_entrypoints_never_reapply(tmp_path, monkeypatch):
    module = tool()
    target, original, _ = sample(module, tmp_path, monkeypatch)
    target.write_bytes(original)
    before = target.stat()
    receipt = module.restore_node_script_path(tmp_path)
    assert receipt['applied'] is False
    assert receipt['source_current_sha256'] == hashlib.sha256(original).hexdigest()
    assert target.stat().st_ino == before.st_ino and target.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(RuntimeError, match='patch_retired_use_restore'):
        module.apply_node_script_path_patch(tmp_path)
    with pytest.raises(RuntimeError, match='patch_retired_use_restore'):
        module.prepare_patch(original)
    assert target.read_bytes() == original


def test_cli_defaults_to_python_stat_and_exposes_separate_explicit_restore(tmp_path, monkeypatch, capsys):
    module = tool()
    seen = []
    def python_stat(root):
        seen.append(('python-stat', root))
        return {'patch_id': module.PYTHON_PATCH_ID}
    def restore(root):
        seen.append(('restore', root))
        return {'patch_id': module.RESTORE_PATCH_ID}
    monkeypatch.setattr(module, 'apply_python_stat_patch', python_stat)
    monkeypatch.setattr(module, 'restore_node_script_path', restore)
    monkeypatch.setattr(sys, 'argv', ['wsl_sandbox_patch.py', '--source-root', str(tmp_path)])
    assert module.main() == 0
    assert module.PYTHON_PATCH_ID in capsys.readouterr().out
    monkeypatch.setattr(sys, 'argv', [*sys.argv, '--restore-node-script-path'])
    assert module.main() == 0
    assert module.RESTORE_PATCH_ID in capsys.readouterr().out
    assert seen == [('python-stat', tmp_path), ('restore', tmp_path)]
    monkeypatch.setattr(sys, 'argv', ['wsl_sandbox_patch.py', '--source-root', str(tmp_path), '--patch', 'node-script-path'])
    with pytest.raises(RuntimeError, match='patch_retired_use_restore'):
        module.main()
    assert len(seen) == 2


def test_unknown_file_is_rejected_without_changes(tmp_path):
    module = tool()
    target = tmp_path / module.SOURCE_FILE
    target.parent.mkdir(parents=True)
    content = b'unknown source, even if it contains node_temp/node_temp/test.js\n'
    target.write_bytes(content)
    with pytest.raises(RuntimeError, match='source_unrecognized'):
        module.restore_node_script_path(tmp_path)
    assert target.read_bytes() == content
    assert list(target.parent.glob('.nodejs-path-*.pending')) == []


def test_guard_rejects_changed_postimage_instead_of_silent_partial_fix(tmp_path, monkeypatch):
    module = tool()
    target, _, patched = sample(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'ORIGINAL_SHA256', '0' * 64)
    with pytest.raises(RuntimeError, match='result_mismatch'):
        module.restore_node_script_path(tmp_path)
    assert target.read_bytes() == patched


def test_source_drift_before_atomic_replace_preserves_other_change(tmp_path, monkeypatch):
    module = tool()
    target, _, _ = sample(module, tmp_path, monkeypatch)
    actual_chmod = Path.chmod
    drift = b'concurrent explicit source change\n'
    def changed(path, mode, **kwargs):
        actual_chmod(path, mode, **kwargs)
        if path.name.endswith('.pending'):
            target.write_bytes(drift)
    monkeypatch.setattr(Path, 'chmod', changed)
    with pytest.raises(RuntimeError, match='source_changed'):
        module.restore_node_script_path(tmp_path)
    assert target.read_bytes() == drift
    assert list(target.parent.glob('.nodejs-path-*.pending')) == []


def test_symlink_target_is_rejected_without_touching_destination(tmp_path):
    module = tool()
    original = tmp_path / 'original.go'
    original.write_bytes(b'unchanged')
    source = tmp_path / 'source'
    target = source / module.SOURCE_FILE
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(original)
    except OSError:
        pytest.skip('当前Windows账户未开放创建软链；Linux路径检查由同一分支执行')
    with pytest.raises(RuntimeError, match='path_invalid'):
        module.restore_node_script_path(source)
    assert original.read_bytes() == b'unchanged'
