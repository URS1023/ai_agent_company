"""Windows隔离启动边界；只用临时文件、伪进程和本轮新监听套接字。"""

import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]


def tool(name='windows'):
    spec = importlib.util.spec_from_file_location('native_' + name, ROOT / 'deploy/native' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


def runtime(tmp_path):
    module = tool()
    data = {'kind': 'manufacturing-native-windows', 'root': str(tmp_path.resolve()), 'processes': {},
        'ports': {'openwebui': 13000, 'postgres': 15432}, 'binaries': {}, 'installations': {}}
    module.write_json(tmp_path / 'runtime.json', data)
    (tmp_path / 'logs').mkdir()
    return module, data


def test_environment_does_not_inherit_old_storage_or_models(monkeypatch):
    module = tool()
    for name in ['DATABASE_URL', 'PGHOST', 'REDIS_HOST', 'OPENAI_API_KEY', 'HTTP_PROXY', 'DOCKER_HOST', 'PYTHONPATH']:
        monkeypatch.setenv(name, 'old-do-not-use.invalid')
    env = module.base_environment()
    assert not any('old-do-not-use' in value for value in env.values())
    assert env['PATHEXT'] == '.COM;.EXE;.BAT;.CMD'
    assert str(module.DEFAULT_ROOT) in env['UV_CACHE_DIR']


def test_source_copy_excludes_in_use_environment_data_and_dependencies(tmp_path):
    module = tool()
    source, destination = tmp_path / 'source', tmp_path / 'copy'
    source.mkdir()
    for name in ['app.py', '.env.example', '.env', 'business.db', 'private.key']:
        (source / name).write_text(name, 'utf-8')
    (source / 'node_modules').mkdir()
    (source / 'node_modules/code.js').write_text('old dependency', 'utf-8')
    module.copy_source(source, destination)
    assert sorted(path.name for path in destination.iterdir()) == ['.env.example', 'app.py']


def test_unowned_runtime_cannot_be_reused_or_stopped(tmp_path):
    module = tool()
    (tmp_path / 'runtime.json').write_text('{"kind":"other-service"}', 'utf-8')
    with pytest.raises(ValueError, match='不属于'):
        module.load(tmp_path)
    with pytest.raises(ValueError, match='不属于'):
        module.prepare(tmp_path, tmp_path / 'not-read', tmp_path / 'not-read')


def test_path_and_archive_cannot_escape_runtime(tmp_path):
    module, _ = runtime(tmp_path)
    with pytest.raises(ValueError):
        module.ensure_inside(tmp_path, tmp_path / '../existing-db')
    with pytest.raises(ValueError):
        module.ensure_inside(tmp_path, tmp_path)
    (tmp_path / 'tools').mkdir()
    asset = tmp_path / 'tools/qdrant.zip'
    with zipfile.ZipFile(asset, 'w') as archive:
        archive.writestr('../../escape.exe', b'bad')
    monkey = pytest.MonkeyPatch()
    monkey.setitem(module.ASSETS, 'qdrant', {'filename': asset.name, 'sha256': module.sha(asset)})
    try:
        with pytest.raises(ValueError):
            module.download_asset(tmp_path, 'qdrant')
    finally:
        monkey.undo()
    assert not (tmp_path / 'escape.exe').exists()


def test_tampered_binary_is_not_executed(tmp_path):
    module, _ = runtime(tmp_path)
    (tmp_path / 'tools').mkdir()
    asset = tmp_path / 'tools/qdrant.zip'
    asset.write_bytes(b'not official asset')
    with pytest.raises(ValueError, match='SHA256'):
        module.download_asset(tmp_path, 'qdrant')


def test_process_id_reuse_prevents_stop(tmp_path, monkeypatch):
    module, data = runtime(tmp_path)
    data['processes']['openwebui'] = {'pid': 999, 'create_time': 10, 'executable': 'python.exe'}
    module.write_json(tmp_path / 'runtime.json', data)
    process = SimpleNamespace(create_time=lambda: 20, exe=lambda: 'python.exe')
    monkeypatch.setattr(module.psutil, 'Process', lambda pid: process)
    with pytest.raises(RuntimeError, match='复用'):
        module.stop(tmp_path, 'openwebui')


def test_occupied_port_rejected_before_launch(tmp_path, monkeypatch):
    module, data = runtime(tmp_path)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        data['ports']['openwebui'] = listener.getsockname()[1]
        module.write_json(tmp_path / 'runtime.json', data)
        monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('不允许启动'))
        with pytest.raises(RuntimeError, match='已被其他进程占用'):
            module.spawn(tmp_path, 'openwebui', ['never-run'], cwd=tmp_path, env={})


def test_unverified_database_dependency_never_connects(tmp_path):
    service = tool('windows_services')
    runtime = SimpleNamespace(load=lambda root: {'ports': {'postgres': 15432}}, owned_process=lambda *args: None)
    with pytest.raises(RuntimeError, match='禁止回退旧服务'):
        service.require_service(runtime, tmp_path, 'postgres')


def test_dify_storage_settings_are_explicit_and_local(tmp_path):
    module = tool()
    service = tool('windows_services')
    data = {'ports': module.PORTS, 'sources': {'dify': str(tmp_path / 'sources/dify')}}
    values = {name: 'generated-test-only' for name in ['postgres_password', 'redis_password', 'qdrant_key',
        'dify_secret', 'plugin_key', 'plugin_inner_key', 'sandbox_key']}
    env = service.dify_environment(module, tmp_path, data, values)
    assert env['DB_HOST'] == '127.0.0.1' and env['DB_PORT'] == '15432'
    assert env['REDIS_PORT'] == '16379' and env['DB_DATABASE'] == 'dify'
    assert env['QDRANT_URL'] == 'http://127.0.0.1:16333'
    assert env['CODE_EXECUTION_ENDPOINT'] == 'http://127.0.0.1:18194'
    assert str(tmp_path) in env['OPENDAL_FS_ROOT']


def test_manifest_updates_merge_instead_of_replacing_other_components(tmp_path):
    module, _ = runtime(tmp_path)
    module.update_runtime(tmp_path, lambda state: state['installations'].update(a={'done': True}))
    module.update_runtime(tmp_path, lambda state: state['installations'].update(b={'done': True}))
    assert set(module.load(tmp_path)['installations']) == {'a', 'b'}


def api_runtime(tmp_path, monkeypatch):
    module, service = tool(), tool('windows_services')
    module.REPO = tmp_path / 'project'
    root = module.REPO / '.local/native_windows'
    root.mkdir(parents=True)
    source = root / 'sources/openwebui'
    (source / 'backend/open_webui').mkdir(parents=True)
    (source / 'backend/open_webui/main.py').write_text('# 测试源码占位，不会导入\n', 'utf-8')
    (source / 'static/static').mkdir(parents=True)
    (source / 'package.json').write_text('{"version":"0.12.3"}', 'utf-8')
    python = root / 'venvs/openwebui/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'fixture-not-executable')
    (python.parent.parent / 'pyvenv.cfg').write_text('version_info = 3.12.13\ninclude-system-site-packages = false\n', 'utf-8')
    data = {'kind': 'manufacturing-native-windows', 'id': 'isolated-fixture', 'root': str(root),
        'processes': {}, 'ports': module.PORTS, 'sources': {'openwebui': str(source)},
        'resource_incident': {'active': True}, 'installations': {}}
    module.write_json(root / 'runtime.json', data)
    monkeypatch.setattr(service, 'require_local_drive', lambda path: None)
    monkeypatch.setitem(sys.modules, 'windows_services', service)
    return module, service, root, data


def test_incident_ack_cannot_enable_install_or_missing_frontend(tmp_path, monkeypatch):
    module, _, root, _ = api_runtime(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match='API单次确认不能恢复安装'):
        module.install(root, 'openwebui')
    with pytest.raises(RuntimeError, match='已暂停'):
        module.start(root, 'openwebui', api_only=True)
    with pytest.raises(RuntimeError, match='原生前端尚未构建'):
        module.start(root, 'openwebui', ack_resource_incident=True)
    with pytest.raises(ValueError, match='仅适用于Open WebUI'):
        module.start(root, 'postgres', api_only=True, ack_resource_incident=True)
    assert module.load(root)['resource_incident']['active'] is True


def test_ack_is_only_for_this_api_start(tmp_path, monkeypatch):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'credentials', lambda target: {'webui_secret': 'test-only'})
    calls = []
    monkeypatch.setattr(service, 'start_openwebui', lambda *args, **kwargs: calls.append(kwargs) or {'mode': 'api_only'})
    assert module.start(root, 'openwebui', api_only=True, ack_resource_incident=True)['mode'] == 'api_only'
    assert calls == [{'api_only': True, 'acknowledged_incident': True, 'startup_seconds': 45}]
    assert module.load(root)['resource_incident']['active'] is True
    with pytest.raises(RuntimeError):
        module.start(root, 'redis')


def test_api_paths_refuse_external_source_or_unregistered_sqlite(tmp_path, monkeypatch):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    bad = {**data, 'sources': {'openwebui': str(tmp_path / 'old-openwebui')}}
    with pytest.raises(ValueError, match='隔离运行目录'):
        service.openwebui_paths(module, root, bad, api_only=True)
    target = root / 'openwebui/data/webui.db'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'not-opened-by-test')
    with pytest.raises(ValueError, match='禁止自动接管'):
        service.openwebui_paths(module, root, data, api_only=True)


def test_api_resource_gate_fails_before_spawn(monkeypatch):
    service = tool('windows_services')
    monkeypatch.setattr(service, 'memory_snapshot', lambda: {'available_bytes': 1024**3, 'commit_free_bytes': 5 * 1024**3})
    with pytest.raises(RuntimeError, match='至少2GiB'):
        service.require_api_resources()
    monkeypatch.setattr(service, 'memory_snapshot', lambda: {'available_bytes': 4 * 1024**3, 'commit_free_bytes': 2 * 1024**3})
    with pytest.raises(RuntimeError, match='至少3GiB'):
        service.require_api_resources()


def test_api_environment_disables_all_weight_updates_and_dotenv(tmp_path, monkeypatch):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    paths = service.openwebui_paths(module, root, data, api_only=True)
    monkeypatch.setattr(module, 'openwebui_provider_environment', lambda: {'RAG_EMBEDDING_ENGINE': 'openai', 'OPENAI_API_KEY': 'fixture'})
    env = service.openwebui_environment(module, paths, {'webui_secret': 'fixture'})
    assert env['DATABASE_URL'] == 'sqlite:///' + paths['sqlite'].as_posix()
    assert env['PYTHON_DOTENV_DISABLED'] == '1'
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    for name in ['RAG_EMBEDDING_MODEL_AUTO_UPDATE', 'RAG_RERANKING_MODEL_AUTO_UPDATE', 'WHISPER_MODEL_AUTO_UPDATE', 'ENABLE_OLLAMA_API']:
        assert env[name] == 'False'
    assert not paths['frontend'].exists()


def test_install_lock_is_exclusive_and_released(tmp_path):
    service = tool('windows_services')
    with service.exclusive_operation(tmp_path, 'install'):
        with pytest.raises(RuntimeError, match='禁止并行'):
            with service.exclusive_operation(tmp_path, 'install'):
                pytest.fail('不能进入第二个安装器')
    with service.exclusive_operation(tmp_path, 'install'):
        pass


def test_rss_overrun_records_incident_without_http_or_sql(tmp_path, monkeypatch):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'owned_process', lambda *args: object())
    monkeypatch.setattr(service, 'process_tree_rss', lambda process: service.API_RSS_LIMIT + 1)
    with pytest.raises(RuntimeError, match='RSS超过1GiB'):
        service.wait_for_api(module, root)
    incident = module.load(root)['resource_incident']
    assert incident['active'] and incident['events'][-1]['reason'] == 'rss_budget_exceeded'


def test_explicit_unlimited_memory_still_requires_owned_listener(tmp_path, monkeypatch):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'owned_process', lambda *args: object())
    monkeypatch.setattr(service, 'process_tree_rss', lambda process: 2 * service.API_RSS_LIMIT)
    monkeypatch.setattr(service, 'listening_processes', lambda process: {13000})
    result = service.wait_for_api(module, root, rss_limit=None)
    assert result['listening_verified'] and result['rss_limit_bytes'] is None
    assert result['startup_max_rss_bytes'] == 2 * service.API_RSS_LIMIT


def test_unlimited_memory_cannot_skip_explicit_confirmation(tmp_path, monkeypatch):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='取消内存限制'):
        module.start(root, 'openwebui', limit_memory=False)
    with pytest.raises(ValueError, match='取消内存限制'):
        service.start_openwebui(module, root, {}, {}, api_only=False,
            acknowledged_incident=False, limit_memory=False)


def test_api_timeout_is_bounded_and_no_retry(tmp_path, monkeypatch):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    moments = iter([0, 0.1, 46])
    monkeypatch.setattr(service.time, 'monotonic', lambda: next(moments))
    monkeypatch.setattr(service.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(module, 'owned_process', lambda *args: object())
    monkeypatch.setattr(service, 'process_tree_rss', lambda process: 100)
    monkeypatch.setattr(service, 'listening_processes', lambda process: set())
    with pytest.raises(RuntimeError, match='45秒预算'):
        service.wait_for_api(module, root)
    assert module.load(root)['resource_incident']['events'][-1]['reason'] == 'startup_timeout'


def test_api_smoke_does_not_require_frontend_or_clear_incident(tmp_path, monkeypatch):
    import httpx
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    data['processes']['openwebui'] = {'mode': 'api_only', 'acknowledged_resource_incident': True}
    module.write_json(root / 'runtime.json', data)
    monkeypatch.setattr(service, 'require_service', lambda *args: None)
    called = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            called.append(url)
            return SimpleNamespace(status_code=200, headers={'content-type': 'application/json'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: Client())
    result = module.smoke(root, 'openwebui')
    assert result['passed'] and result['frontend_available'] is False
    assert called == ['http://127.0.0.1:13000/health']
    assert module.load(root)['resource_incident']['active'] is True


def test_api_start_keeps_binding_and_incident_after_owned_listener(tmp_path, monkeypatch):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(service, 'require_api_resources', lambda: {'available_bytes': 3 * 1024**3, 'commit_free_bytes': 4 * 1024**3})
    monkeypatch.setattr(module, 'owned_process', lambda *args: None)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(module, 'openwebui_provider_environment', lambda: {'RAG_EMBEDDING_ENGINE': 'openai'})
    def spawn(target, name, command, *, cwd, env, metadata):
        assert command[-2:] == ['--workers', '1']
        assert env['WEBUI_SECRET_KEY'] == 'test-only'
        identity = {'pid': 123, 'create_time': 1, 'executable': 'fixture.exe', 'launch_id': 'this-launch'}
        module.update_runtime(root, lambda state: state['processes'].update({name: {**identity, **metadata}}))
        return {'started': True, **identity, **metadata}
    monkeypatch.setattr(module, 'spawn', spawn)
    monkeypatch.setattr(service, 'wait_for_api', lambda *args, **kwargs: {'listening_verified': True, 'startup_max_rss_bytes': 100})
    result = service.start_openwebui(module, root, data, {'webui_secret': 'test-only'}, api_only=True, acknowledged_incident=True)
    state = module.load(root)
    assert result['ready'] and result['frontend_available'] is False
    assert not result['http_health_tested'] and not result['functional_acceptance']
    assert state['resource_incident']['active'] is True
    assert state['databases']['openwebui']['sqlite_file'] == str(root / 'openwebui/data/webui.db')
    assert state['processes']['openwebui']['ready']


@pytest.mark.parametrize('replace_pid', [False, True])
def test_failed_api_start_only_stops_this_invocation_pid(tmp_path, monkeypatch, replace_pid):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(service, 'require_api_resources', lambda: {'available_bytes': 3 * 1024**3, 'commit_free_bytes': 4 * 1024**3})
    monkeypatch.setattr(module, 'owned_process', lambda *args: None)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(module, 'openwebui_provider_environment', lambda: {})
    def spawn(target, name, command, **kwargs):
        identity = {'pid': 123, 'create_time': 1, 'executable': 'fixture.exe', 'launch_id': 'this-launch'}
        module.update_runtime(root, lambda state: state['processes'].update({name: identity}))
        return {'started': True, **identity}
    def fail(*args, **kwargs):
        if replace_pid:
            module.update_runtime(root, lambda state: state['processes']['openwebui'].update(pid=456))
        raise RuntimeError('测试预算失败')
    stopped = []
    monkeypatch.setattr(module, 'spawn', spawn)
    monkeypatch.setattr(service, 'wait_for_api', fail)
    monkeypatch.setattr(module, 'stop', lambda target, name: stopped.append(name))
    with pytest.raises(RuntimeError, match='API启动失败'):
        service.start_openwebui(module, root, data, {'webui_secret': 'test-only'}, api_only=True, acknowledged_incident=True)
    assert stopped == ([] if replace_pid else ['openwebui'])


def test_spawn_creation_failure_is_typed_and_hides_exception_text(tmp_path, monkeypatch):
    module, _ = runtime(tmp_path)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    def fail(*args, **kwargs):
        raise OSError('sensitive-fixture-value')
    monkeypatch.setattr(module.subprocess, 'Popen', fail)
    with pytest.raises(module.NativeSpawnError) as captured:
        module.spawn(tmp_path, 'openwebui', ['fake.exe'], cwd=tmp_path, env={})
    error = captured.value
    assert error.phase == 'create' and error.registered_process is None
    assert error.cleanup['verified'] and error.cleanup['alive_pids'] == []
    assert 'sensitive-fixture-value' not in str(error)


def test_early_process_exit_is_failure_and_never_registered(tmp_path, monkeypatch):
    module, _ = runtime(tmp_path)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(module.time, 'sleep', lambda value: None)
    process = SimpleNamespace(pid=123, poll=lambda: 1, wait=lambda timeout: 1)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    with pytest.raises(module.NativeSpawnError) as captured:
        module.spawn(tmp_path, 'openwebui', ['fake.exe'], cwd=tmp_path, env={})
    assert captured.value.phase == 'initial_process_check'
    assert captured.value.registered_process is None
    assert 'openwebui' not in module.load(tmp_path)['processes']


@pytest.mark.parametrize('registered_before_error', [False, True])
def test_registration_failure_reports_surviving_child_without_false_stop(tmp_path, monkeypatch, registered_before_error):
    module, _ = runtime(tmp_path)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(module.time, 'sleep', lambda value: None)
    child = SimpleNamespace(pid=456, terminate=lambda: None)
    owner = SimpleNamespace(children=lambda recursive: [child], create_time=lambda: 1, exe=lambda: 'fixture.exe')
    class Process:
        pid = 123
        code = None
        def poll(self):
            return self.code
        def terminate(self):
            self.code = -1
        def wait(self, timeout):
            return self.code
    process = Process()
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(module.psutil, 'Process', lambda pid: owner)
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda processes, timeout: ([], [child]))
    update = module.update_runtime
    def fail_registration(target, change):
        if registered_before_error:
            update(target, change)
        raise OSError('sensitive-registry-fixture')
    monkeypatch.setattr(module, 'update_runtime', fail_registration)
    with pytest.raises(module.NativeSpawnError) as captured:
        module.spawn(tmp_path, 'openwebui', ['fake.exe'], cwd=tmp_path, env={})
    error = captured.value
    assert error.cleanup['verified'] is False and error.cleanup['alive_pids'] == [456]
    assert '回收未能全部核实' in str(error)
    assert 'sensitive-registry-fixture' not in str(error)
    if registered_before_error:
        assert error.registered_process['pid'] == 123
        assert module.load(tmp_path)['processes']['openwebui']['launch_id'] == error.registered_process['launch_id']
    else:
        assert error.registered_process is None
        assert 'openwebui' not in module.load(tmp_path)['processes']


@pytest.mark.parametrize('phase', ['prepare_environment', 'spawn'])
def test_api_early_failure_records_safe_event_without_stopping_unowned_registration(tmp_path, monkeypatch, phase):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    other = {'pid': 987, 'create_time': 9, 'executable': 'other.exe', 'launch_id': 'other-launch'}
    data['processes']['openwebui'] = other
    module.write_json(root / 'runtime.json', data)
    monkeypatch.setattr(service, 'require_api_resources', lambda: {'available_bytes': 3 * 1024**3, 'commit_free_bytes': 4 * 1024**3})
    monkeypatch.setattr(module, 'owned_process', lambda *args: None)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    def environment(*args):
        if phase == 'prepare_environment':
            raise RuntimeError('sensitive-env-fixture')
        return {}
    monkeypatch.setattr(service, 'openwebui_environment', environment)
    def fail_spawn(*args, **kwargs):
        raise module.NativeSpawnError(phase='initial_process_check', error_type='RuntimeError',
            cleanup={'verified': True, 'alive_pids': [], 'attempted_pids': [123], 'errors': []})
    monkeypatch.setattr(module, 'spawn', fail_spawn)
    monkeypatch.setattr(module, 'stop', lambda *args: pytest.fail('没有已登记的本次PID，禁止按名称停止'))
    with pytest.raises(RuntimeError, match='API启动失败') as captured:
        service.start_openwebui(module, root, data, {'webui_secret': 'test-only'}, api_only=True, acknowledged_incident=True)
    state = module.load(root)
    event = state['resource_incident']['events'][-1]
    assert event['reason'] == 'api_start_failed' and event['details']['phase'] == phase
    assert state['resource_incident']['active'] is True
    assert state['processes']['openwebui'] == other
    assert 'sensitive-env-fixture' not in json.dumps(event)
    assert 'sensitive-env-fixture' not in str(captured.value)


def test_launch_identity_includes_nonce_creation_time_and_executable():
    module = tool()
    own = {'pid': 123, 'create_time': 1, 'executable': 'fixture.exe', 'launch_id': 'this-launch'}
    assert module.same_launch(dict(own), own)
    for field, value in [('pid', 456), ('create_time', 2), ('executable', 'other.exe'), ('launch_id', 'other-launch')]:
        assert not module.same_launch({**own, field: value}, own)
    assert not module.same_launch({'pid': 123}, own)


@pytest.mark.parametrize('version,accepted', [('3.12', True), ('3.12.13', True), ('3.120', False), ('3.13', False), ('3.12evil', False)])
def test_uv_python_version_metadata_accepts_major_minor_only(tmp_path, monkeypatch, version, accepted):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    cfg = root / 'venvs/openwebui/pyvenv.cfg'
    cfg.write_text('version_info = ' + version + '\ninclude-system-site-packages = false\n', 'utf-8')
    if accepted:
        paths = service.openwebui_paths(module, root, data, api_only=True)
        assert paths['python'] == root / 'venvs/openwebui/Scripts/python.exe'
    else:
        with pytest.raises(ValueError, match='独立Python3.12'):
            service.openwebui_paths(module, root, data, api_only=True)


@pytest.mark.parametrize('seconds', [45, 180])
def test_explicit_api_start_budget_reaches_metadata_and_deadline_without_changing_resources(tmp_path, monkeypatch, seconds):
    module, service, root, data = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(service, 'require_api_resources', lambda: {'available_bytes': 3 * 1024**3, 'commit_free_bytes': 4 * 1024**3})
    monkeypatch.setattr(module, 'owned_process', lambda *args: None)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(service, 'openwebui_environment', lambda *args: {})
    monkeypatch.setattr(service.time, 'monotonic', lambda: 100)
    recorded = {}

    def spawn(target, name, command, *, cwd, env, metadata):
        recorded['metadata'] = dict(metadata)
        identity = {'pid': 123, 'create_time': 1, 'executable': 'fixture.exe', 'launch_id': 'this-launch'}
        module.update_runtime(root, lambda state: state['processes'].update({name: {**identity, **metadata}}))
        return {'started': True, **identity, **metadata}

    def ready(*args, **kwargs):
        recorded['wait'] = kwargs
        return {'listening_verified': True, 'startup_max_rss_bytes': 100, 'startup_budget_seconds': kwargs['seconds']}

    monkeypatch.setattr(module, 'spawn', spawn)
    monkeypatch.setattr(service, 'wait_for_api', ready)
    result = service.start_openwebui(module, root, data, {'webui_secret': 'test-only'},
        api_only=True, acknowledged_incident=True, startup_seconds=seconds)
    assert recorded['wait'] == {'seconds': seconds, 'deadline': 100 + seconds}
    assert recorded['metadata']['startup_budget_seconds'] == result['startup_budget_seconds'] == seconds
    assert recorded['metadata']['rss_limit_bytes'] == service.API_RSS_LIMIT == 1024**3
    assert service.API_MIN_PHYSICAL == 2 * 1024**3 and service.API_MIN_COMMIT == 3 * 1024**3
    state = module.load(root)
    assert state['resource_incident']['active'] is True
    assert state['processes']['openwebui']['startup_budget_seconds'] == seconds


@pytest.mark.parametrize('api_only,acknowledged', [(False, False), (True, False)])
def test_180_second_service_budget_requires_explicit_ack(tmp_path, api_only, acknowledged):
    service = tool('windows_services')
    # 拒绝须发生在路径、凭据、资源检查和spawn之前，不能靠上层CLI单独守住。
    with pytest.raises(ValueError, match='180秒预算仅适用于'):
        service.start_openwebui(object(), tmp_path, {}, {}, api_only=api_only,
            acknowledged_incident=acknowledged, startup_seconds=180)


@pytest.mark.parametrize('component,api_only,acknowledged', [
    ('redis', False, False), ('openwebui', False, False), ('openwebui', True, False),
])
def test_180_second_dispatch_budget_cannot_escape_api_scope(tmp_path, monkeypatch, component, api_only, acknowledged):
    module, _, root, _ = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'credentials', lambda *args: pytest.fail('非法范围不能读取凭据或启动'))
    with pytest.raises(ValueError, match='180秒预算仅适用于'):
        module.start(root, component, api_only=api_only, ack_resource_incident=acknowledged, startup_seconds=180)


@pytest.mark.parametrize('api_only', [True, False])
def test_explicit_180_dispatch_forwards_only_the_selected_budget_and_keeps_incident(tmp_path, monkeypatch, api_only):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'credentials', lambda target: {'webui_secret': 'test-only'})
    if not api_only:
        frontend = root / 'sources/openwebui/build'
        frontend.mkdir()
        (frontend / 'index.html').write_text('<html></html>', 'utf-8')
    calls = []
    monkeypatch.setattr(service, 'start_openwebui', lambda *args, **kwargs: calls.append(kwargs) or {'mock': True})
    assert module.start(root, 'openwebui', api_only=api_only, ack_resource_incident=True, startup_seconds=180) == {'mock': True}
    assert calls == [{'api_only': api_only, 'acknowledged_incident': True, 'startup_seconds': 180}]
    assert module.load(root)['resource_incident']['active'] is True


@pytest.mark.parametrize('seconds', [0, 46, 181, 180.0, True, None])
def test_dispatch_budget_only_accepts_the_two_integer_choices(tmp_path, monkeypatch, seconds):
    module, _, root, _ = api_runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='只允许45或180秒'):
        module.start(root, 'openwebui', api_only=True, ack_resource_incident=True, startup_seconds=seconds)


@pytest.mark.parametrize('seconds', [45, 180])
def test_selected_budget_timeout_message_and_event_match_without_retry(tmp_path, monkeypatch, seconds):
    module, service, root, _ = api_runtime(tmp_path, monkeypatch)
    moments, calls = iter([0, 0.1, seconds + 1]), []
    monkeypatch.setattr(service.time, 'monotonic', lambda: next(moments))
    monkeypatch.setattr(service.time, 'sleep', lambda value: None)
    monkeypatch.setattr(module, 'owned_process', lambda *args: calls.append('owned') or object())
    monkeypatch.setattr(service, 'process_tree_rss', lambda process: 100)
    monkeypatch.setattr(service, 'listening_processes', lambda process: set())
    with pytest.raises(RuntimeError, match=str(seconds) + '秒预算'):
        service.wait_for_api(module, root, seconds=seconds)
    event = module.load(root)['resource_incident']['events'][-1]
    assert event['reason'] == 'startup_timeout' and event['details']['budget_seconds'] == seconds
    assert calls == ['owned']


@pytest.mark.parametrize('explicit_seconds', [None, '180'])
@pytest.mark.parametrize('api_only', [True, False])
def test_cli_forwards_confirmed_start_mode(tmp_path, monkeypatch, capsys, explicit_seconds, api_only):
    module = tool()
    calls = []
    monkeypatch.setattr(module, 'start', lambda *args, **kwargs: calls.append(kwargs) or {'mock': True})
    arguments = ['windows.py', 'start', 'openwebui', '--ack-resource-incident', '--root', str(tmp_path)]
    if api_only:
        arguments.append('--api-only')
    if explicit_seconds is not None:
        arguments.extend(['--startup-seconds', explicit_seconds])
    monkeypatch.setattr(sys, 'argv', arguments)
    module.main()
    assert calls == [{'api_only': api_only, 'ack_resource_incident': True,
        'startup_seconds': 45 if explicit_seconds is None else 180}]
    assert json.loads(capsys.readouterr().out) == {'mock': True}


@pytest.mark.parametrize('arguments', [
    ['doctor', '--startup-seconds', '180'],
    ['install', 'openwebui', '--startup-seconds', '180'],
    ['start', 'redis', '--startup-seconds', '180'],
    ['start', 'openwebui', '--startup-seconds', '180'],
    ['start', 'openwebui', '--api-only', '--startup-seconds', '180'],
    ['start', 'openwebui', '--api-only', '--ack-resource-incident', '--startup-seconds', '120'],
])
def test_cli_rejects_unapproved_or_unknown_extended_budget_before_dispatch(monkeypatch, arguments):
    module = tool()
    for name in ['start', 'install', 'doctor', 'prepare', 'smoke', 'stop']:
        monkeypatch.setattr(module, name, lambda *args, **kwargs: pytest.fail('非法CLI不得派发任何运行操作'))
    monkeypatch.setattr(sys, 'argv', ['windows.py', *arguments])
    with pytest.raises(SystemExit) as captured:
        module.main()
    assert captured.value.code == 2


def test_registered_spawn_failure_removes_only_verified_stopped_launch(tmp_path, monkeypatch):
    module, _ = runtime(tmp_path)
    monkeypatch.setattr(module, 'available_port', lambda port: True)
    monkeypatch.setattr(module.time, 'sleep', lambda value: None)
    child = SimpleNamespace(pid=456, terminate=lambda: None)
    owner = SimpleNamespace(children=lambda recursive: [child], create_time=lambda: 1, exe=lambda: 'fixture.exe')
    class Process:
        pid = 123
        code = None
        def poll(self):
            return self.code
        def terminate(self):
            self.code = -1
        def wait(self, timeout):
            return self.code
    process = Process()
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(module.psutil, 'Process', lambda pid: owner)
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda processes, timeout: ([child], []))
    update = module.update_runtime
    calls = []
    def write_then_fail_once(target, change):
        value = update(target, change)
        calls.append(True)
        if len(calls) == 1:
            raise OSError('模拟原子写入后的调用失败')
        return value
    monkeypatch.setattr(module, 'update_runtime', write_then_fail_once)
    with pytest.raises(module.NativeSpawnError) as captured:
        module.spawn(tmp_path, 'openwebui', ['fake.exe'], cwd=tmp_path, env={}, metadata={'mode': 'api_only'})
    error = captured.value
    assert error.cleanup['verified'] and error.cleanup['alive_pids'] == []
    assert error.registered_process['pid'] == 123
    assert set(error.registered_process) == {'pid', 'create_time', 'executable', 'port', 'launch_id'}
    assert 'openwebui' not in module.load(tmp_path)['processes']
