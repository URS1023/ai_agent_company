"""仅校验临时目录中的沙箱Node来源记录，不下载、启动或请求任何服务。"""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / 'deploy/native/wsl_setup.py'
    spec = importlib.util.spec_from_file_location('sandbox_node_setup', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    folder = tmp_path / ('tooling/node-sandbox-' + module.SANDBOX_NODE)
    (folder / 'bin').mkdir(parents=True)
    binary = folder / 'bin/node'
    binary.write_bytes(b'fixture')
    receipt = {
        'version': module.SANDBOX_NODE, 'root': str(folder),
        'archive_sha256': module.SANDBOX_NODE_SHA, 'binary_sha256': module.digest(binary),
    }
    (tmp_path / 'sandbox-node-receipt.json').write_text(json.dumps(receipt))
    return module, binary, receipt


def test_reuses_verified_sandbox_runtime_without_download(setup, monkeypatch):
    module, _, receipt = setup
    monkeypatch.setattr(module, 'download', lambda *args: pytest.fail('不应重新下载'))
    assert module.setup_sandbox_node() == {**receipt, 'reused': True}
    assert module.NODE == '24.21.0'
    assert module.SANDBOX_NODE == '20.11.1'


def test_changed_binary_is_rejected(setup):
    module, binary, _ = setup
    binary.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='sandbox_node_binary_changed'):
        module.verify_sandbox_node()


def test_mismatched_receipt_does_not_fall_back_or_reinstall(setup, monkeypatch):
    module, _, receipt = setup
    receipt['root'] = str(module.ROOT / 'tooling/node')
    (module.ROOT / 'sandbox-node-receipt.json').write_text(json.dumps(receipt))
    monkeypatch.setattr(module, 'download', lambda *args: pytest.fail('不应降级重装'))
    with pytest.raises(RuntimeError, match='sandbox_node_receipt_changed'):
        module.setup_sandbox_node()


def test_partial_install_requires_review(setup, monkeypatch):
    module, _, _ = setup
    (module.ROOT / 'sandbox-node-receipt.json').unlink()
    monkeypatch.setattr(module, 'download', lambda *args: pytest.fail('不应覆盖部分安装'))
    with pytest.raises(FileNotFoundError):
        module.setup_sandbox_node()
