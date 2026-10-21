"""工具桥日常重绑定的状态、并发和失败边界；仅进程及配置替身。"""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def subject(monkeypatch, tmp_path):
    calls = []
    record = {'launch_id': 'old', 'ready': True, 'network': {'id': 'nat'},
              'upstreams': {'platform': 'previous', 'openwebui': 'chat'}}
    state = {'processes': {'bridge': record}}
    context = {'owned': True, 'configured': True}

    def update(_, callback):
        callback(state)
        return deepcopy(state)

    windows = SimpleNamespace(load=lambda _: deepcopy(state), update_runtime=update)
    network = {'id': 'nat'}
    upstreams = {'platform': 'new', 'openwebui': 'chat'}

    def stop(_, *, expected_launch_id):
        calls.append(('stop', expected_launch_id))
        assert state['processes']['bridge']['launch_id'] == expected_launch_id
        state['processes'].pop('bridge')
        return {'stopped': True}

    def start(_):
        calls.append('start')
        reused = 'bridge' in state['processes']
        state['processes']['bridge'] = {'launch_id': 'old' if reused else 'new', 'ready': True,
                                       'network': network, 'upstreams': upstreams}
        return {'ready': True, 'reused': reused}

    bridge = SimpleNamespace(COMPONENT='bridge', owned_bridge=lambda *args: context['owned'],
        capture_upstreams=lambda _: deepcopy(upstreams), stop_bridge=stop, start_bridge=start)
    proxy = SimpleNamespace(reconcile_windows=lambda _: calls.append('proxy') or {'configured': True})
    for name, module in {'windows': windows, 'wsl_http_bridge': bridge,
                        'wsl': SimpleNamespace(verified_nat_network=lambda: deepcopy(network)),
                        'wsl_bridge_startup': proxy,
                        'wsl_tool_credentials': SimpleNamespace(ToolCredentialStore=object)}.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[3] / 'deploy/native/wsl_bridge_lifecycle.py'
    spec = importlib.util.spec_from_file_location('bridge_lifecycle_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'configured', lambda _: context['configured'])
    return module, state, context, calls, bridge, proxy


def test_owned_changed_upstream_stops_exact_launch_once(subject, tmp_path):
    module, state, _, calls, _, _ = subject
    value = module.reconcile(tmp_path)
    assert calls == [('stop', 'old'), 'start', 'proxy']
    assert value['ready'] and value['rebuilt']
    assert state['bridge_lifecycle']['status'] == 'ready'
    assert state['bridge_lifecycle']['launch_id'] == 'new'


def test_unchanged_bridge_reuses_and_preserves_process(subject, tmp_path):
    module, state, _, calls, bridge, _ = subject
    state['processes']['bridge']['upstreams'] = bridge.capture_upstreams(tmp_path)
    assert module.reconcile(tmp_path)['reused'] is True
    assert calls == ['start', 'proxy']
    assert state['processes']['bridge']['launch_id'] == 'old'


def test_unconfigured_never_probes_or_mutates(subject, tmp_path):
    module, state, context, calls, _, _ = subject
    context['configured'] = False
    assert module.reconcile(tmp_path) == {'configured': False, 'ready': False}
    assert calls == [] and 'bridge_lifecycle' not in state


@pytest.mark.parametrize('fault', ['unready', 'missing', 'previous_failed', 'concurrent', 'startup_failed'])
def test_unknown_or_failed_state_never_starts(subject, tmp_path, fault):
    module, state, context, calls, _, _ = subject
    if fault == 'unready':
        state['processes']['bridge']['ready'] = False
    elif fault == 'missing':
        context['owned'] = False
    elif fault in {'previous_failed', 'concurrent'}:
        state['bridge_lifecycle'] = {'status': 'failed' if fault == 'previous_failed' else 'running'}
    else:
        state['processes'].clear()
        state['bridge_failures'] = [{'error_type': 'TimeoutError'}]
    with pytest.raises(RuntimeError, match='requires_review'):
        module.reconcile(tmp_path)
    assert calls == []


@pytest.mark.parametrize('stage', ['stop_bridge', 'start_bridge', 'proxy'])
def test_failed_action_blocks_repeat_without_retry(subject, tmp_path, stage):
    module, state, _, calls, bridge, proxy = subject

    def fail(*args, **kwargs):
        calls.append(stage + '-failed')
        raise TimeoutError('controlled_failure')

    setattr(proxy if stage == 'proxy' else bridge,
            'reconcile_windows' if stage == 'proxy' else stage, fail)
    with pytest.raises(TimeoutError):
        module.reconcile(tmp_path)
    before = list(calls)
    with pytest.raises(RuntimeError, match='previous_attempt_requires_review'):
        module.reconcile(tmp_path)
    assert calls == before and state['bridge_lifecycle']['status'] == 'failed'


def test_explicit_clean_stop_can_start_registered_tools(subject, tmp_path):
    module, state, context, calls, _, _ = subject
    state['processes'].clear()
    context['owned'] = False
    assert module.reconcile(tmp_path)['ready']
    assert calls == ['start', 'proxy']


def test_stop_compare_rejects_replacement_before_process_operation(monkeypatch, tmp_path):
    """执行真实stop_bridge函数，其他模块仅替身；新进程不会被旧协调动作停止。"""
    path = Path(__file__).resolve().parents[3] / 'deploy/native/wsl_http_bridge.py'
    import ast
    tree = ast.parse(path.read_text('utf-8'))
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'stop_bridge')
    calls = []
    scope = {'Path': Path, 'COMPONENT': 'bridge',
        'windows': SimpleNamespace(DEFAULT_ROOT=tmp_path, load=lambda _: {'processes': {
            'bridge': {'launch_id': 'replacement'}}}, stop=lambda *args: calls.append('stop')),
        'windows_services': SimpleNamespace(exclusive_operation=lambda *args: nullcontext()),
        'owned_bridge': lambda *args: calls.append('probe') or object()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
    with pytest.raises(RuntimeError, match='launch_changed_before_stop'):
        scope['stop_bridge'](tmp_path, expected_launch_id='old')
    assert calls == []
