"""仅用进程替身核验前台WSL任务归属，不启动发行版或业务服务。"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def tool():
    spec = importlib.util.spec_from_file_location('wsl_lifetime', ROOT / 'deploy/native/wsl_lifetime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record():
    return dict(runtime_id='runtime', boot_id='boot', pid=321, created_at=50,
                executable='/owned/python', command=['/owned/python', 'run'])


def process(**changes):
    values = dict(pid=321, create_time=lambda: 50, exe=lambda: '/owned/python',
                  cmdline=lambda: ['/owned/python', 'run'], uids=lambda: SimpleNamespace(real=0),
                  status=lambda: 'running')
    return SimpleNamespace(**(values | changes))


@pytest.mark.parametrize('runtime,boot', [('another', 'boot'), ('runtime', 'previous')])
def test_other_runtime_or_boot_never_looks_up_pid(monkeypatch, runtime, boot):
    module = tool()
    monkeypatch.setattr(module.psutil, 'Process', lambda _: pytest.fail('不应读取非所属PID'))
    assert module.linux_process(record(), runtime, boot) is None


def test_reused_pid_is_not_owned(monkeypatch):
    module = tool()
    monkeypatch.setattr(module.psutil, 'Process', lambda _: process(create_time=lambda: 60))
    assert module.linux_process(record(), 'runtime', 'boot') is None


@pytest.mark.parametrize('changed', [
    {'exe': lambda: '/other/python'}, {'cmdline': lambda: ['/owned/python', 'other']},
    {'uids': lambda: SimpleNamespace(real=1000)},
])
def test_changed_identity_fails_closed(monkeypatch, changed):
    module = tool()
    monkeypatch.setattr(module.psutil, 'Process', lambda _: process(**changed))
    with pytest.raises(RuntimeError, match='identity_changed|owner_changed'):
        module.linux_process(record(), 'runtime', 'boot')


def test_owned_process_and_zombie_are_distinct(monkeypatch):
    module = tool()
    candidate = process()
    monkeypatch.setattr(module.psutil, 'Process', lambda _: candidate)
    assert module.linux_process(record(), 'runtime', 'boot') is candidate
    candidate.status = lambda: module.psutil.STATUS_ZOMBIE
    assert module.linux_process(record(), 'runtime', 'boot') is None


def test_stop_refuses_other_keeper_before_signalling(tmp_path, monkeypatch):
    module = tool()
    path = tmp_path / 'lifetime.json'
    path.write_text('{"keeper_id":"new-instance"}')
    monkeypatch.setattr(module, 'linux_context', lambda: (None, {}, path))
    with pytest.raises(RuntimeError, match='instance_changed'):
        module.stop_linux('previous-instance')


def test_windows_reuse_requires_both_sides(monkeypatch):
    module = tool()
    state = {'processes': {module.COMPONENT: {'keeper_id': 'same'}}}
    windows = SimpleNamespace(DEFAULT_ROOT='root', load=lambda _: state,
        owned_process=lambda *_: process())
    monkeypatch.setitem(sys.modules, 'windows', windows)
    monkeypatch.setitem(sys.modules, 'wsl', SimpleNamespace())
    monkeypatch.setattr(module, 'linux_call', lambda *_: dict(running=True, keeper_id='same', runtime_id='runtime'))
    assert module.ensure_windows_lifetime()['reused']
    monkeypatch.setattr(module, 'linux_call', lambda *_: dict(running=False))
    with pytest.raises(RuntimeError, match='sides_do_not_match'):
        module.ensure_windows_lifetime()


def bridge():
    spec = importlib.util.spec_from_file_location('wsl', ROOT / 'deploy/native/wsl.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_partial_services_never_auto_starts(monkeypatch):
    module = bridge()
    monkeypatch.setitem(sys.modules, 'windows', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'wsl_lifetime', SimpleNamespace(
        ensure_windows_lifetime=lambda: {'runtime_id': 'owned'}))
    actions = []
    def call(action):
        actions.append(action)
        return {'runtime_id': 'owned', 'plugin_database_initialized': True,
            'processes': [{'role': 'postgres', 'owned': True, 'running': True}]}
    monkeypatch.setattr(module, 'runtime_call', call)
    with pytest.raises(RuntimeError, match='partial_services'):
        module.start_runtime()
    assert actions == ['status']


def test_bridge_preserves_keeper_on_incomplete_shutdown(monkeypatch):
    module = bridge()
    monkeypatch.setitem(sys.modules, 'wsl_lifetime', SimpleNamespace(
        stop_windows_lifetime=lambda: pytest.fail('服务尚未全部停止，不能关闭keeper')))
    monkeypatch.setattr(module, 'runtime_call', lambda _: {'stopped': False})
    with pytest.raises(RuntimeError, match='cleanup_incomplete'):
        module.stop_runtime()
