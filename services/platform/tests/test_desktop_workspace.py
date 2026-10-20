"""日常入口只协调平台与专属keeper；全部替身，不启动WSL或真实服务。"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def context(monkeypatch, tmp_path):
    calls = []
    state = {'dify_wsl': {'enabled': True}}
    windows = SimpleNamespace(DEFAULT_ROOT=tmp_path, REPO=tmp_path,
        load=lambda _: state)
    monkeypatch.setitem(sys.modules, 'windows', windows)
    monkeypatch.setitem(sys.modules, 'windows_services', SimpleNamespace())
    source = Path(__file__).resolve().parents[3] / 'deploy/native/desktop.py'
    spec = importlib.util.spec_from_file_location('desktop_workspace_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def platform():
        calls.append('platform')
        return {'reused': True, 'url': module.PLATFORM_URL}

    def ensure():
        calls.append('keeper')
        return {'running': True, 'reused': True, 'runtime_id': 'owned'}

    def proof(*, timeout_seconds):
        calls.append(('proof', timeout_seconds))
        return {'runtime_id': 'owned'}, 'worker-path', {}

    monkeypatch.setattr(module, 'start_platform', platform)
    lifetime = SimpleNamespace(ensure_windows_lifetime=ensure)
    worker = SimpleNamespace(_wsl_proof=proof)
    monkeypatch.setitem(sys.modules, 'wsl_lifetime', lifetime)
    monkeypatch.setitem(sys.modules, 'agent_platform.modules.flows.graph_runtime',
        SimpleNamespace(worker_module=lambda: worker))
    # 若协调器误调用完整Dify启动，则测试立即失败。
    monkeypatch.setitem(sys.modules, 'wsl', SimpleNamespace())
    return module, state, calls, lifetime, worker


def test_unconfigured_computer_starts_platform_without_wsl(context):
    module, state, calls, _, _ = context
    state.clear()
    result = module.start_workspace()
    assert calls == ['platform']
    assert result['graph_runtime'] == {'configured': False, 'ready': False}


def test_configured_workspace_only_reuses_platform_and_keeper(context):
    module, _, calls, _, _ = context
    result = module.start_workspace()
    assert calls == ['platform', 'keeper', ('proof', 5)]
    assert result['graph_runtime'] == {'configured': True, 'environment_ready': True,
        'runtime_id': 'owned', 'reused': True}


def test_platform_failure_prevents_linux_start(context, monkeypatch):
    module, _, calls, _, _ = context

    def fail():
        raise RuntimeError('foreign_listener')

    monkeypatch.setattr(module, 'start_platform', fail)
    with pytest.raises(RuntimeError, match='foreign_listener'):
        module.start_workspace()
    assert calls == []


def test_keeper_failure_does_not_retry_or_run_proof(context):
    module, _, calls, lifetime, _ = context

    def fail():
        calls.append('failed-keeper')
        raise TimeoutError('keeper_failed')

    lifetime.ensure_windows_lifetime = fail
    with pytest.raises(TimeoutError):
        module.start_workspace()
    assert calls == ['platform', 'failed-keeper']


@pytest.mark.parametrize('identity', ['foreign', None])
def test_changed_linux_identity_never_returns_ready(context, identity):
    module, _, _, lifetime, _ = context
    lifetime.ensure_windows_lifetime = lambda: {'running': True, 'runtime_id': identity}
    with pytest.raises(RuntimeError, match='workspace_graph_runtime_mismatch'):
        module.start_workspace()
