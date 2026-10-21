"""日常入口协调平台、原聊天与专属keeper；全部替身，不启动真实服务。"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def context(monkeypatch, tmp_path):
    calls = []
    state = {'dify_wsl': {'enabled': True}, 'sources': {'openwebui': 'owned-source'},
        'processes': {'openwebui': {'mode': 'full', 'ready': True}}}
    windows = SimpleNamespace(DEFAULT_ROOT=tmp_path, REPO=tmp_path,
        load=lambda _: state,
        owned_process=lambda *args: calls.append('chat') or object(),
        start=lambda *args, **kwargs: calls.append(('start-chat', args, kwargs)))
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
    assert result['chat'] == {'configured': False, 'ready': False}


def test_configured_workspace_reuses_chat_platform_and_keeper(context):
    module, _, calls, _, _ = context
    result = module.start_workspace()
    assert calls == ['platform', 'chat', 'keeper', ('proof', 5)]
    assert result['chat']['reused'] and result['chat']['ready']
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
    assert calls == ['platform', 'chat', 'failed-keeper']


@pytest.mark.parametrize('identity', ['foreign', None])
def test_changed_linux_identity_never_returns_ready(context, identity):
    module, _, _, lifetime, _ = context
    lifetime.ensure_windows_lifetime = lambda: {'running': True, 'runtime_id': identity}
    with pytest.raises(RuntimeError, match='workspace_graph_runtime_mismatch'):
        module.start_workspace()


@pytest.mark.parametrize('change', [{'mode': 'api_only'}, {'ready': False}])
def test_existing_incomplete_chat_is_not_restarted(context, change):
    module, state, calls, _, _ = context
    state['processes']['openwebui'].update(change)
    with pytest.raises(RuntimeError, match='完整模式'):
        module.start_workspace()
    assert calls == ['platform', 'chat']


def test_missing_chat_starts_once_with_existing_authorized_budget(context):
    module, _, calls, _, _ = context
    module.windows.owned_process = lambda *args: None
    result = module.start_chat()
    assert result['ready'] and not result['reused']
    assert len(calls) == 1 and calls[0][0] == 'start-chat'
    assert calls[0][2] == {'ack_resource_incident': True, 'startup_seconds': 180, 'limit_memory': False}


def test_chat_start_failure_is_not_retried_or_followed_by_linux(context):
    module, _, calls, _, _ = context
    module.windows.owned_process = lambda *args: None
    def fail(*args, **kwargs):
        calls.append('chat-failed')
        raise TimeoutError('chat_startup_failed')
    module.windows.start = fail
    with pytest.raises(TimeoutError):
        module.start_workspace()
    assert calls == ['platform', 'chat-failed']
