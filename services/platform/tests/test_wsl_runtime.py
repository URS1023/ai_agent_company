"""WSL装配的隔离、归属与失败回收测试；不调用WSL、原生进程或数据库。"""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def tool(name):
    if name != 'wsl_identity':
        tool('wsl_identity')
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy/native' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_namespace_listener_requires_owned_socket_inode_and_listening_state(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    folder = tmp_path / '321'
    (folder / 'net').mkdir(parents=True)
    (folder / 'fd').mkdir()
    for name in ['3', '4']:
        (folder / 'fd' / name).touch()
    monkeypatch.setattr(module.os, 'readlink', lambda path: {'3': 'socket:[1234]', '4': '/ordinary/file'}[path.name])
    header = 'sl local_address rem_address st tx_queue rx_queue tr tmwhen retrnsmt uid\n'
    (folder / 'net/tcp').write_text(header +
        '0: 00000000:3A9C 00000000:0000 0A 0:0 00:0 0 1000 0 1234\n' +
        '1: 00000000:FFFF 00000000:0000 0A 0:0 00:0 0 1000 0 9999\n' +
        '2: 00000000:1234 00000000:0000 01 0:0 00:0 0 1000 0 1234\n')
    assert module.namespace_listening_ports(321, tmp_path) == {15004}


@pytest.mark.parametrize('uid', [0, 1000])
def test_code_sandbox_fixed_uid_refuses_existing_different_user(monkeypatch, uid):
    module = tool('wsl_runtime')
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=uid)))
    monkeypatch.setattr(module.subprocess, 'run', lambda *_a, **_k: pytest.fail('不得覆盖既有账户'))
    with pytest.raises(RuntimeError, match='sandbox_existing_uid_mismatch'):
        module.ensure_sandbox_account()


def test_local_environment_discards_inherited_databases_and_model_keys(monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://old.invalid/production')
    monkeypatch.setenv('REDIS_HOST', 'old.invalid')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    secret = {name: 'isolated-test-value' for name in module.SECRETS}
    data = {'id': 'a' * 32}
    for env in [module.safe_environment(data), module.api_environment(data, secret), module.agent_environment(data, secret)]:
        assert not any('old.invalid' in value or 'must-not-inherit' in value for value in env.values())
        assert env['WSL_DISTRO_NAME'] == 'ai-agent-company'
    assert module.api_environment(data, secret)['DB_PORT'] == '25432'
    assert module.api_environment(data, secret)['VECTOR_STORE'] == 'qdrant'
    assert module.agent_environment(data, secret)['DIFY_AGENT_RUNTIME_BACKEND'] == 'local'


@pytest.mark.parametrize('fault', ['none', 'host', 'database', 'role', 'table', 'schema'])
def test_plugin_preflight_reads_only_fixed_local_metadata_and_refuses_foreign_schema(monkeypatch, capsys, fault):
    module = tool('wsl_runtime')
    connections, statements = [], []
    class Cursor:
        def __init__(self, database): self.database = database
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query):
            self.query = query
            statements.append(query)
        def fetchall(self):
            query = self.query
            if query.startswith('EXPLAIN '): return [('Metadata scan',)]
            if 'data_directory' in query:
                return [('/owned/postgres', '25432', 'agent_admin', self.database,
                    '127.0.0.2' if fault == 'host' else '127.0.0.1', 160014)]
            if 'FROM pg_database' in query:
                return [('dify', 'dify_app', False), ('dify_plugin', 'foreign' if fault == 'database' else 'dify_app', False),
                    ('postgres', 'agent_admin', False), ('template0', 'agent_admin', True), ('template1', 'agent_admin', True)]
            if 'FROM pg_roles' in query:
                return [('agent_admin', True, True, True, True, True, True), ('dify_app', True, False, fault == 'role', False, False, False)]
            if 'SELECT nspname' in query: return [('public', 'foreign' if fault == 'schema' else 'pg_database_owner')]
            if 'FROM pg_class' in query: return [(1 if fault == 'table' else 0,)]
            if 'FROM pg_proc' in query: return [(0,)]
            if 'FROM pg_extension' in query: return [('plpgsql',)]
            pytest.fail('预检不得查询业务表')
    class Connection:
        def __init__(self, database): self.database = database
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor(self.database)
    def connect(**kwargs):
        assert kwargs['host'] == '127.0.0.1' and kwargs['port'] == 25432
        assert kwargs['user'] == 'agent_admin' and kwargs['connect_timeout'] == 3
        assert 'default_transaction_read_only=on' in kwargs['options']
        connections.append(kwargs['dbname'])
        return Connection(kwargs['dbname'])
    monkeypatch.setitem(sys.modules, 'psycopg2', SimpleNamespace(connect=connect))
    monkeypatch.setenv('AIC_PG_ADMIN', 'memory-only')
    monkeypatch.setenv('AIC_EXPECTED_PGDATA', '/owned/postgres')
    script = compile(module.plugin_database_probe_script(), '<plugin metadata probe>', 'exec')
    if fault == 'none':
        exec(script, {})
        assert json.loads(capsys.readouterr().out) == {'verified': True, 'connections': 2,
            'metadata_statements': 16, 'database': 'dify_plugin', 'empty_business_schema': True}
        assert connections == ['postgres', 'dify_plugin']
    else:
        with pytest.raises(RuntimeError, match='plugin_initialization_'):
            exec(script, {})
    assert len(statements) <= 16
    assert all(statements[index] == 'EXPLAIN ' + statements[index + 1] for index in range(0, len(statements), 2))


@pytest.mark.parametrize('fail_cli', [False, True])
def test_official_plugin_migration_preserves_history_redacts_logs_and_never_replays(tmp_path, monkeypatch, fail_cli):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    data['databases_initialized'] = True
    data['failures'] = [{'phase': 'older-failure'}]
    monkeypatch.setattr(module, 'plugin_cli_receipt', lambda: {'binary_sha256': 'c' * 64})
    monkeypatch.setattr(module, 'validate_plugin_cluster_files', lambda _: True)
    monkeypatch.setattr(module, 'verify_plugin_database', lambda *a: {'verified': True})
    monkeypatch.setattr(module, 'python313', lambda: '/owned/python3.13')
    monkeypatch.setattr(module, 'directory', lambda path, *a: path.mkdir(parents=True, exist_ok=True))
    def invoke(argv, **kwargs):
        events.append(argv)
        assert argv == [str(tmp_path / 'bin/plugin-commandline'), 'migrate']
        assert kwargs['timeout'] == 120 and kwargs['user'] == 1000
        assert kwargs['env']['DB_DATABASE'] == kwargs['env']['DB_DEFAULT_DATABASE'] == 'dify_plugin'
        assert kwargs['env']['DB_MAX_OPEN_CONNS'] == '1'
        assert kwargs['env']['HOME'] == str(tmp_path / 'config/plugin-migration')
        assert data['plugin_database_initialization']['phase'] == 'migration-attempted'
        if fail_cli:
            raise module.subprocess.CalledProcessError(1, argv, stderr=b'test-pg_app')
        return SimpleNamespace(stdout=b'database migration completed successfully', stderr=b'')
    monkeypatch.setattr(module.subprocess, 'run', invoke)
    secret = {name: 'test-' + name for name in module.SECRETS}
    if fail_cli:
        with pytest.raises(module.subprocess.CalledProcessError):
            module.initialize_plugin_database(data, secret, 'b' * 32)
        assert not data.get('plugin_database_initialized')
        assert data['plugin_database_initialization']['status'] == 'failed'
        log = (tmp_path / 'logs' / ('init-plugin-data-' + 'b' * 32 + '.log')).read_text()
        assert 'test-pg_app' not in log and '[REDACTED]' in log
        with pytest.raises(RuntimeError, match='previous_plugin_database_initialization_requires_review'):
            module.initialize_plugin_database(data, secret, 'd' * 32)
    else:
        assert module.initialize_plugin_database(data, secret, 'b' * 32)['plugin_database_initialized']
        assert data['plugin_database_initialization']['status'] == 'ready'
        assert module.initialize_plugin_database(data, secret, 'd' * 32)['already_initialized']
    assert len(events) == 1 and data['failures'][0] == {'phase': 'older-failure'}


@pytest.mark.parametrize('already_running', [False, True])
def test_plugin_only_initialization_does_not_reinitialize_main_db_or_stop_reused_pg(tmp_path, monkeypatch, already_running):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    data['databases_initialized'] = True
    if already_running:
        data['processes']['postgres'] = {'launch_id': 'old-owned-launch'}
    monkeypatch.setattr(module, 'plugin_cli_receipt', lambda: {'binary_sha256': 'c' * 64})
    monkeypatch.setattr(module, 'validate_plugin_cluster_files', lambda _: bool(data['processes']))
    monkeypatch.setattr(module, 'initialize_plugin_database', lambda *a: {'plugin_database_initialized': True})
    assert module.init_plugin_data()['plugin_database_initialized']
    assert not any(isinstance(event, list) for event in events)
    assert events == ([] if already_running else ['start:postgres', 'ready:postgres', 'stop:postgres'])


def test_plugin_metadata_failure_never_invokes_migration_and_blocks_start(tmp_path, monkeypatch):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    data['databases_initialized'] = True
    monkeypatch.setattr(module, 'plugin_cli_receipt', lambda: {'binary_sha256': 'c' * 64})
    monkeypatch.setattr(module, 'validate_plugin_cluster_files', lambda _: True)
    def reject(*a): raise RuntimeError('plugin_initialization_schema_not_empty')
    monkeypatch.setattr(module, 'verify_plugin_database', reject)
    with pytest.raises(RuntimeError, match='schema_not_empty'):
        module.initialize_plugin_database(data, {}, 'b' * 32)
    assert data['plugin_database_initialization']['status'] == 'failed' and events == []
    (tmp_path / 'runtime.json').write_text('{}')
    monkeypatch.setattr(module, 'dependency_checks', lambda: [])
    monkeypatch.setattr(module, 'landlock_abi', lambda: 7)
    assert module.doctor()['ready_to_start'] is False
    assert module.doctor()['plugin_database_initialized'] is False
    with pytest.raises(RuntimeError, match='native_dependencies_or_database_not_ready'):
        module.start()


@pytest.mark.parametrize('fault', ['none', 'digest', 'source', 'package', 'elf'])
def test_plugin_cli_existing_artifact_requires_source_and_binary_identity(tmp_path, monkeypatch, fault):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'bin').mkdir()
    binary = tmp_path / 'bin/plugin-commandline'
    header = b'\x7fELF\x02\x01' + b'\x00' * 12 + b'\x3e\x00'
    binary.write_bytes(b'bad-format' if fault == 'elf' else header)
    receipt = {'version': '0.6.10', 'package': './cmd/commandline/', 'source_sha256': module.PLUGIN_ZIP_SHA,
        'binary_sha256': module.digest(binary)}
    if fault == 'digest': receipt['binary_sha256'] = '0' * 64
    if fault == 'source': receipt['source_sha256'] = '0' * 64
    if fault == 'package': receipt['package'] = './cmd/server'
    (tmp_path / 'plugin-cli-receipt.json').write_text(json.dumps(receipt))
    monkeypatch.setattr(module, 'command', lambda *a, **k: pytest.fail('现有制品不可重新构建'))
    if fault == 'none':
        assert module.build_plugin_cli()['reused']
    else:
        with pytest.raises(RuntimeError, match='plugin_cli_'):
            module.build_plugin_cli()


@pytest.mark.parametrize('fault', ['none', 'owner', 'version', 'receipt', 'live_plugin', 'foreign_pg'])
def test_plugin_initialization_checks_pg_files_and_does_not_adopt_live_foreign_process(tmp_path, monkeypatch, fault):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    pgdata = tmp_path / 'data/postgres'
    (pgdata / 'global').mkdir()
    (pgdata / 'PG_VERSION').write_text('15' if fault == 'version' else '16')
    (pgdata / 'postgresql.conf').write_text('port=25432')
    (pgdata / 'global/pg_control').write_bytes(b'owned-new-cluster')
    (tmp_path / 'prepare-attempt.json').write_text(json.dumps({'status': 'ready',
        'runtime_id': 'z' * 32 if fault == 'receipt' else data['id']}))
    uid = pgdata.stat().st_uid
    monkeypatch.setattr(module, 'account', lambda _: SimpleNamespace(pw_uid=uid + (1 if fault == 'owner' else 0)))
    monkeypatch.setattr(module, 'owned_process', lambda *a: None)
    if fault == 'live_plugin': data['processes']['plugin'] = {'cgroup': {'live': True}}
    if fault == 'foreign_pg': data['processes']['postgres'] = {'cgroup': {'live': True}}
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(live_members=lambda group: [99] if group.get('live') else []))
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): events.append(address)
    monkeypatch.setattr(module.socket, 'socket', Socket)
    if fault == 'none':
        assert module.validate_plugin_cluster_files(data) is False
        assert events == [('127.0.0.1', 15002), ('127.0.0.1', 25432)]
    else:
        with pytest.raises(RuntimeError, match='plugin_initialization_'):
            module.validate_plugin_cluster_files(data)


def test_plugin_dependency_failure_reserves_attempt_before_spawn_and_requires_review(tmp_path, monkeypatch):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    data['databases_initialized'] = True
    monkeypatch.setattr(module, 'plugin_cli_receipt', lambda: {'binary_sha256': 'c' * 64})
    monkeypatch.setattr(module, 'validate_plugin_cluster_files', lambda _: False)
    def fail_spawn(*args):
        assert data['plugin_database_initialization']['phase'] == 'dependencies'
        raise RuntimeError('owned_spawn_failed')
    monkeypatch.setattr(module, 'spawn', fail_spawn)
    with pytest.raises(RuntimeError, match='owned_spawn_failed'):
        module.init_plugin_data()
    assert data['plugin_database_initialization']['status'] == 'failed'
    with pytest.raises(RuntimeError, match='previous_plugin_initialization_requires_review'):
        module.init_plugin_data()
    assert events == []


def test_fresh_main_migration_precedes_plugin_and_does_not_repeat_main_after_plugin_failure(tmp_path, monkeypatch):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    def main_migration(*args):
        data['databases_initialized'] = True
        events.append('main-migrated')
        return {'databases_initialized': True}
    def plugin_migration(*args):
        assert data['databases_initialized'] and events[-1] == 'main-migrated'
        events.append('plugin-attempted')
        raise RuntimeError('plugin_migration_failed')
    monkeypatch.setattr(module, 'migrate_databases', main_migration)
    monkeypatch.setattr(module, 'initialize_plugin_database', plugin_migration)
    with pytest.raises(RuntimeError, match='plugin_migration_failed'):
        module.init_data()
    assert data['databases_initialized'] and not data.get('plugin_database_initialized')
    assert events[-4:] == ['main-migrated', 'plugin-attempted', 'stop:redis', 'stop:postgres']
    with pytest.raises(RuntimeError, match='explicit_init_plugin_data'):
        module.init_data()
    assert events.count('main-migrated') == 1


def test_wsl_stage_budgets_separate_new_plugin_work_from_existing_api_timeout(monkeypatch):
    module = tool('wsl')
    monkeypatch.setattr(module, 'linux_path', lambda path: '/owned/' + path.name)
    command, seconds = module.specification('init-plugin-data')
    assert command[-1] == 'init-plugin-data' and seconds == 240
    assert module.specification('init-data')[1] == 600
    assert module.specification('plugin-cli-build')[1] == 180


def test_namespace_plan_limits_only_own_new_namespace():
    module = tool('wsl_network')
    for plan in module.plans('a' * 32):
        assert len(plan['host_device']) <= 15 and len(plan['job_device']) <= 15
        commands = module.commands(plan)
        firewall = [line for line in commands if 'iptables' in line or 'ip6tables' in line]
        assert firewall and all(line[:4] == ['ip', 'netns', 'exec', plan['name']] for line in firewall)
        assert ['ip', 'netns', 'exec', plan['name'], 'iptables', '-P', 'OUTPUT', 'DROP'] in firewall
        assert not any('sysctl' in line or 'MASQUERADE' in line for line in commands)


def test_namespace_rules_keep_policy_and_remove_only_volatile_counters():
    module = tool('wsl_network')
    assert module.stable_rules('# saved at time\n:OUTPUT DROP [2:15]\n-A OUTPUT -j DROP') == [
        ':OUTPUT DROP [0:0]', '-A OUTPUT -j DROP']


def test_namespace_refuses_existing_subnet_and_accepts_only_nonconflicting_routes():
    module = tool('wsl_network')
    expected = module.plans('a' * 32)
    module.check_route_conflicts([{'dst': 'default'}, {'dst': '172.20.0.0/20'}, {'dst': '127.0.0.0/8'}], expected)
    with pytest.raises(RuntimeError, match='private_network_subnet_conflicts'):
        module.check_route_conflicts([{'dst': '10.203.17.0/24'}], expected)


@pytest.mark.parametrize('runtime,launch,role', [('short', 'a' * 32, 'api'), ('a' * 32, 'other', 'api'),
    ('a' * 32, 'b' * 32, '../api'), ('a' * 32, 'b' * 32, 'api/worker')])
def test_cgroup_names_reject_unowned_identity(runtime, launch, role):
    with pytest.raises(RuntimeError):
        tool('wsl_cgroups').names(runtime, launch, role)


def test_cgroup_manifest_cannot_point_to_other_service(tmp_path, monkeypatch):
    module = tool('wsl_cgroups')
    monkeypatch.setattr(module, 'BASE', tmp_path)
    with pytest.raises(RuntimeError, match='cgroup_manifest_path_changed'):
        module.verify({'runtime_id': 'a' * 32, 'launch_id': 'b' * 32, 'role': 'api',
            'path': str(tmp_path / 'some-existing-service')})


def test_stop_recovers_orphans_even_when_original_parent_pid_no_longer_exists(monkeypatch):
    module = tool('wsl_runtime')
    group = {'runtime_id': 'a' * 32, 'launch_id': 'b' * 32, 'role': 'api'}
    monkeypatch.setattr(module, 'load', lambda: {'id': 'a' * 32, 'processes': {
        'api': {'pid': 123, 'identity_version': 2, 'start_ticks': '18', 'launch_id': 'b' * 32, 'cgroup': group}}})
    calls = []
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(stop=lambda value, **kwargs:
        calls.append(value) or {'verified': True, 'stopped': True, 'alive_pids': []}))
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: pytest.fail('不得因父PID消失跳过专属cgroup'))
    assert module.stop_role('api', expected_launch='b' * 32)['stopped']
    assert calls == [group]


def test_stop_keeps_residual_failure_visible_and_preserves_data(monkeypatch):
    module = tool('wsl_runtime')
    group = {'runtime_id': 'a' * 32, 'launch_id': 'b' * 32, 'role': 'api'}
    monkeypatch.setattr(module, 'load', lambda: {'id': 'a' * 32, 'processes': {
        'api': {'identity_version': 2, 'start_ticks': '18', 'launch_id': 'b' * 32, 'cgroup': group}}})
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(stop=lambda *a, **k:
        {'verified': True, 'stopped': False, 'alive_pids': [777]}))
    result = module.stop_role('api')
    assert result['stopped'] is False and result['alive_pids'] == [777] and result['data_preserved']


def test_stop_launch_cas_refuses_other_attempt(monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'load', lambda: {'processes': {'api': {'launch_id': 'b' * 32}}})
    with pytest.raises(RuntimeError, match='stop_launch_compare_and_swap_failed'):
        module.stop_role('api', expected_launch='c' * 32)


def test_source_recovery_preserves_original_attempted_history(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    previous = {'sources': {'status': 'attempted', 'at': '2026-10-21T16:20:56Z'}}
    (tmp_path / 'setup-stages.json').write_text(json.dumps(previous))
    monkeypatch.setattr(module, 'recover_sources', lambda *a, **k: {'verified': True})
    args = SimpleNamespace(stage='verify-resume', archive=tmp_path / 'a', archive_receipt=tmp_path / 'a.json', verify_only=False)
    assert module.execute_stage(args) == 0
    actual = json.loads((tmp_path / 'setup-stages.json').read_text())
    assert actual['sources'] == previous['sources']
    assert actual['verify-resume']['status'] == 'ready'


def test_failed_recovery_refuses_automatic_replay(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'setup-stages.json').write_text(json.dumps({'verify-resume': {'status': 'failed'}}))
    monkeypatch.setattr(module, 'recover_sources', lambda *a, **k: pytest.fail('不得自动重放失败恢复'))
    args = SimpleNamespace(stage='verify-resume', archive=tmp_path / 'a', archive_receipt=tmp_path / 'a.json', verify_only=False)
    with pytest.raises(RuntimeError, match='previous_failed_stage_requires_review'):
        module.execute_stage(args)


def test_verify_only_does_not_mutate_history(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    before = '{"sources":{"status":"attempted"}}'
    (tmp_path / 'setup-stages.json').write_text(before)
    monkeypatch.setattr(module, 'recover_sources', lambda *a, **k: {'verified': True})
    args = SimpleNamespace(stage='verify-resume', archive=tmp_path / 'a', archive_receipt=tmp_path / 'a.json', verify_only=True)
    module.execute_stage(args)
    assert (tmp_path / 'setup-stages.json').read_text() == before


def test_cgroup_stop_uses_exact_registered_inode_and_keeps_failure_visible(tmp_path, monkeypatch):
    module = tool('wsl_cgroups')
    calls = []
    path = tmp_path / 'owned'
    path.mkdir()
    kill = path / 'cgroup.kill'
    kill.write_text('0')
    monkeypatch.setattr(module, 'verify', lambda info: calls.append(info['inode']) or path)
    monkeypatch.setattr(module, 'live_members', lambda info: [101])
    monkeypatch.setattr(module, 'contains', lambda info, pid: True)
    signals = []
    monkeypatch.setattr(module.os, 'kill', lambda pid, sig: signals.append(pid))
    clock = iter([0, 9.5, 9.8, 10.1])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    result = module.stop({'inode': 731}, seconds=10)
    assert result['stopped'] is False and result['alive_pids'] == [101]
    assert signals == [101] and kill.read_text() == '1' and calls == [731, 731]


def test_replaced_cgroup_is_never_signalled(monkeypatch):
    module = tool('wsl_cgroups')
    def replaced(_):
        raise RuntimeError('cgroup_directory_replaced')
    monkeypatch.setattr(module, 'verify', replaced)
    monkeypatch.setattr(module.os, 'kill', lambda *_: pytest.fail('归属改变不可发信号'))
    with pytest.raises(RuntimeError, match='cgroup_directory_replaced'):
        module.stop({})


def test_spawn_failure_records_safe_cleanup_without_starting_another_process(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'process-launches').mkdir()
    group = {'runtime_id': 'a' * 32, 'launch_id': 'b' * 32, 'role': 'worker'}
    data = {'id': 'a' * 32, 'processes': {}, 'failures': [], 'cgroup_root_inode': 57}
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'update', lambda change: change(data) or data)
    monkeypatch.setattr(module, 'account', lambda _: SimpleNamespace(pw_uid=1000))
    cleanup = []
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(prepare_root=lambda *a: 57,
        create=lambda *a: group, stop=lambda info, **kwargs: cleanup.append(info) or {'stopped': True}))
    def reject_spawn(*args, **kwargs):
        raise OSError('not enough operating system resources')
    monkeypatch.setattr(module.subprocess, 'Popen', reject_spawn)
    with pytest.raises(OSError):
        module.spawn('worker', {'user': 'agentapp', 'argv': ['/bin/bash', '/safe/entrypoint.sh'],
            'cwd': tmp_path, 'env': {}}, data, 'b' * 32)
    assert cleanup == [group]
    assert data['failures'][0]['phase'] == 'spawn'
    assert data['failures'][0]['error_type'] == 'OSError'
    assert 'not enough' not in json.dumps(data)


def test_owned_process_accepts_only_declared_exec_transition_and_launch(monkeypatch):
    module = tool('wsl_runtime')
    environment = {'AGENT_WSL_RUNTIME_ID': 'a' * 32, 'AGENT_WSL_LAUNCH_ID': 'b' * 32, 'AGENT_WSL_ROLE': 'api'}
    candidate = SimpleNamespace(pid=101, create_time=lambda: pytest.fail('不再使用壁钟出生时间'), uids=lambda: SimpleNamespace(real=1000),
        exe=lambda: '/owned/python', environ=lambda: environment)
    monkeypatch.setattr(module.psutil, 'Process', lambda _: candidate)
    monkeypatch.setattr(module.os, 'getpgid', lambda _: 101, raising=False)
    monkeypatch.setattr(module, 'read_start_ticks', lambda _: '1750')
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(contains=lambda *_: True))
    info = {'pid': 101, 'created_at': 17.5, 'identity_version': 2, 'start_ticks': '1750',
        'allowed_uids': [0, 1000], 'allowed_executables': ['/owned/python'],
        'process_group': 101, 'launch_id': 'b' * 32, 'role': 'api', 'cgroup': {}}
    assert module.owned_process(info, {'id': 'a' * 32}) is candidate
    environment['AGENT_WSL_LAUNCH_ID'] = 'c' * 32
    assert module.owned_process(info, {'id': 'a' * 32}) is None


@pytest.mark.parametrize('fault', ['ticks', 'race', 'uid', 'exe', 'pgid', 'cgroup', 'netns', 'runtime', 'launch', 'role'])
def test_raw_ticks_do_not_weaken_any_existing_role_ownership_boundary(monkeypatch, fault):
    module = tool('wsl_runtime')
    info = {'pid': 101, 'created_at': 17.5, 'identity_version': 2, 'start_ticks': '1750',
        'allowed_uids': [0, 1000], 'allowed_executables': ['/owned/python'], 'process_group': 101,
        'launch_id': 'b' * 32, 'role': 'api', 'cgroup': {}}
    environment = {'AGENT_WSL_RUNTIME_ID': 'a' * 32, 'AGENT_WSL_LAUNCH_ID': 'b' * 32, 'AGENT_WSL_ROLE': 'api'}
    for name, key in [('runtime', 'AGENT_WSL_RUNTIME_ID'), ('launch', 'AGENT_WSL_LAUNCH_ID'), ('role', 'AGENT_WSL_ROLE')]:
        if fault == name:
            environment[key] = 'changed'
    candidate = SimpleNamespace(pid=101, create_time=lambda: pytest.fail('不读壁钟'),
        uids=lambda: SimpleNamespace(real=2000 if fault == 'uid' else 1000),
        exe=lambda: '/other/python' if fault == 'exe' else '/owned/python', environ=lambda: environment)
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: candidate)
    reads = iter(['1750', '1751']) if fault == 'race' else None
    monkeypatch.setattr(module, 'read_start_ticks', lambda _: next(reads) if reads else '1751' if fault == 'ticks' else '1750')
    monkeypatch.setattr(module.os, 'getpgid', lambda _: 102 if fault == 'pgid' else 101, raising=False)
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(contains=lambda *_: fault != 'cgroup'))
    if fault == 'netns':
        info.update(namespace='owned', namespace_inode=1)
        monkeypatch.setattr(module, 'Path', lambda *_: SimpleNamespace(stat=lambda: SimpleNamespace(st_ino=2)))
    assert module.owned_process(info, {'id': 'a' * 32}) is None
    assert info['created_at'] == 17.5 and info['start_ticks'] == '1750'


def test_new_spawn_records_raw_ticks_and_keeps_created_at_for_audit(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'process-launches').mkdir()
    group = {'runtime_id': 'a' * 32, 'launch_id': 'b' * 32, 'role': 'worker'}
    data = {'id': 'a' * 32, 'processes': {}, 'failures': [], 'cgroup_root_inode': 57}
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'update', lambda change: change(data) or data)
    monkeypatch.setattr(module, 'account', lambda _: SimpleNamespace(pw_uid=1000))
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(prepare_root=lambda *a: 57,
        create=lambda *a: group, contains=lambda *_: True))
    environment = {'AGENT_WSL_RUNTIME_ID': 'a' * 32, 'AGENT_WSL_LAUNCH_ID': 'b' * 32, 'AGENT_WSL_ROLE': 'worker'}
    candidate = SimpleNamespace(pid=123, create_time=lambda: 3025.5, exe=lambda: str((tmp_path / 'tooling/bin/python').resolve()),
        uids=lambda: SimpleNamespace(real=0), environ=lambda: environment)
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: candidate)
    monkeypatch.setattr(module, 'read_start_ticks', lambda _: '1750')
    monkeypatch.setattr(module.os, 'getpgid', lambda _: 123, raising=False)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *_a, **_k: SimpleNamespace(pid=123, poll=lambda: None))
    info = module.spawn('worker', {'user': 'agentapp', 'argv': ['/bin/bash', '/safe/entrypoint.sh'],
        'cwd': tmp_path, 'env': {'AGENT_WSL_RUNTIME_ID': 'a' * 32}}, data, 'b' * 32)
    assert info['identity_version'] == 2 and info['start_ticks'] == '1750' and info['created_at'] == 3025.5
    assert data['processes']['worker'] == info


def test_verify_existing_agent_binaries_never_recompiles_plugin(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'bin').mkdir()
    packages = {'shellctl': 'shellctl', 'shellctl-sanitize-pty': 'sanitize-pty', 'shellctl-runner-exit': 'runner-exit',
        'shellctl-runner': 'runner', 'dify-agent': 'dify-agent-cli'}
    header = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\x00'
    for name in packages:
        (tmp_path / 'bin' / name).write_bytes(header)
    calls = []
    def inspect(args, **kwargs):
        assert args[1:3] == ['version', '-m']
        calls.append(args)
        name = Path(args[-1]).name
        return SimpleNamespace(stdout='\tpath\tgithub.com/langgenius/dify/dify-agent-runtime/cmd/' + packages[name] +
            '\n\tbuild\tGOOS=linux\n\tbuild\tGOARCH=amd64\n')
    monkeypatch.setattr(module.subprocess, 'run', inspect)
    result = module.verify_binaries(include_plugin=False)
    assert set(result['binaries']) == set(packages)
    assert len(calls) == 5 and result['compilation_performed'] is False
    assert (tmp_path / 'go-agent-binaries-receipt.json').exists()
    assert not (tmp_path / 'go-binaries-receipt.json').exists()


def test_plugin_build_is_independent_and_requires_restored_storage(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    calls = []
    monkeypatch.setattr(module, 'command', lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(module, 'verify_binaries', lambda: {'verified': True})
    with pytest.raises(RuntimeError, match='plugin_storage_source_missing'):
        module.build_plugin()
    path = tmp_path / 'sources/plugin/internal/storage/baidubos/bos.go'
    path.parent.mkdir(parents=True)
    path.write_text('package baidubos')
    assert module.build_plugin() == {'verified': True}
    assert len(calls) == 1 and calls[0][0][0][-1] == './cmd/server'
    assert calls[0][1]['cwd'] == tmp_path / 'sources/plugin'


def test_repaired_source_stage_does_not_erase_failed_binary_history(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    previous = {'binaries': {'status': 'failed', 'error_type': 'CalledProcessError'},
        'verify-resume': {'status': 'ready'}}
    (tmp_path / 'setup-stages.json').write_text(json.dumps(previous))
    monkeypatch.setattr(module, 'recover_sources', lambda *a, **k: {'verified': True, 'filter_revision': 2})
    args = SimpleNamespace(stage='sources-storage-repair', archive=tmp_path / 'a', archive_receipt=tmp_path / 'a.json', verify_only=False)
    module.execute_stage(args)
    actual = json.loads((tmp_path / 'setup-stages.json').read_text())
    assert actual['binaries'] == previous['binaries'] and actual['verify-resume'] == previous['verify-resume']
    assert actual['sources-storage-repair']['status'] == 'ready'


def test_runtime_specs_keep_writable_state_outside_source(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    tool('wsl_network')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'python313', lambda: str(tmp_path / 'tooling/python/python3.13'))
    data = {'id': 'a' * 32}
    secret = {name: 'isolated' for name in module.SECRETS}
    specs = module.specs(data, secret)
    assert specs['beat']['argv'][-2:] == ['--schedule', str(tmp_path / 'data/dify/celerybeat-schedule')]
    assert specs['agent']['env']['DIFY_AGENT_LOCAL_SANDBOX_HOME_SNAPSHOT_ROOT'] == str(tmp_path / 'jobs/snapshots')
    assert specs['shellctl']['env']['SHELLCTL_ENABLE_PATH_ISOLATION'] == 'true'
    assert specs['code']['namespace'] != specs['shellctl']['namespace']
    assert specs['api']['env']['DB_DATABASE'] == 'dify'
    assert b'\nset-proc-title no\n' in specs['redis']['stdin']
    assert specs['redis']['env']['AGENT_WSL_RUNTIME_ID'] == data['id']
    assert specs['qdrant']['cwd'] == tmp_path / 'data/qdrant'
    assert specs['qdrant']['env']['QDRANT__STORAGE__SNAPSHOTS_PATH'] == str(tmp_path / 'data/qdrant/snapshots')
    assert specs['plugin']['env']['UV_PATH'] == str(tmp_path / 'tooling/bin/uv')
    assert specs['plugin']['env']['LOG_LEVEL'] == 'WARN'
    assert specs['api']['env']['DISABLE_TELEMETRY'] == 'true'
    assert specs['api']['env']['DO_NOT_TRACK'] == 'true'
    assert '/etc/passwd' in specs['code']['env']['PYTHON_LIB_PATH'].split(',')
    assert specs['gateway']['supervise'] and specs['web']['supervise']


def test_windows_bridge_discards_wsl_environment_forwarding(monkeypatch):
    module = tool('wsl')
    for name in ['WSLENV', 'DATABASE_URL', 'PGHOST', 'REDIS_HOST', 'HTTP_PROXY', 'DOCKER_HOST']:
        monkeypatch.setenv(name, 'must-not-forward')
    assert not any(value == 'must-not-forward' for value in module.clean_environment().values())


def test_explicit_reviewed_source_resume_keeps_original_failure_in_history(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    failure = {'status': 'failed', 'at': 'original-at', 'error_type': 'RuntimeError'}
    (tmp_path / 'setup-stages.json').write_text(json.dumps({'sources-storage-repair': failure}))
    monkeypatch.setattr(module, 'recover_sources', lambda *a, **k: {'verified': True})
    args = SimpleNamespace(stage='sources-storage-repair', archive=tmp_path / 'a', archive_receipt=tmp_path / 'a.json',
        verify_only=False, reviewed_extras=tmp_path / 'audit', reviewed_extras_sha256='exact-reviewed-sha', resume_reviewed_failure=True)
    module.execute_stage(args)
    actual = json.loads((tmp_path / 'setup-stages.json').read_text())['sources-storage-repair']
    assert actual['status'] == 'ready' and actual['history'] == [failure]
    assert actual['reviewed_extras_sha256'] == 'exact-reviewed-sha'


def test_reviewed_source_resume_cannot_replay_build_or_database_stage(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'setup-stages.json').write_text(json.dumps({'binaries': {'status': 'failed'}}))
    args = SimpleNamespace(stage='binaries', verify_only=False, reviewed_extras=tmp_path / 'audit',
        reviewed_extras_sha256='reviewed-sha', resume_reviewed_failure=True)
    with pytest.raises(RuntimeError, match='reviewed_resume_requires_failed_source_stage'):
        module.execute_stage(args)


def test_doctor_before_prepare_reports_dependencies_without_creating_data(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'dependency_checks', lambda: ['sandbox-build-pending'])
    monkeypatch.setattr(module, 'landlock_abi', lambda: 7)
    result = module.doctor()
    assert result['prepared'] is False and result['databases_initialized'] is False
    assert 'sandbox-build-pending' in result['missing'] and result['ready_to_start'] is False
    assert not list(tmp_path.iterdir())


def test_boot_reconcile_refuses_live_original_pid_and_does_not_signal(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'boot_identity', lambda: 'new-boot')
    data = {'id': 'a' * 32, 'boot_id': 'old-boot', 'processes': {'api': {
        'pid': 123, 'created_at': 18, 'identity_version': 2, 'start_ticks': '18'}}}
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(names=lambda *a: (tmp_path / 'absent', None)))
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: SimpleNamespace(pid=123))
    monkeypatch.setattr(module, 'read_start_ticks', lambda _: '18')
    monkeypatch.setattr(module, 'update', lambda *_: pytest.fail('旧进程还在，不能改归属'))
    with pytest.raises(RuntimeError, match='old_process_still_exists'):
        module.reconcile_boot()


def test_boot_reconcile_preserves_old_process_history_without_killing_reused_pid(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'boot_identity', lambda: 'new-boot')
    old_processes = {'api': {'pid': 123, 'created_at': 18, 'identity_version': 2, 'start_ticks': '18'}}
    data = {'id': 'a' * 32, 'boot_id': 'old-boot', 'processes': old_processes, 'boot_history': []}
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'update', lambda change: change(data) or data)
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(names=lambda *a: (tmp_path / 'absent', None)))
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: SimpleNamespace(pid=123))
    monkeypatch.setattr(module, 'read_start_ticks', lambda _: '250')
    result = module.reconcile_boot()
    assert result['services_started'] is False and data['processes'] == {}
    assert data['boot_history'][0]['processes'] == old_processes
    assert data['boot_id'] == 'new-boot' and data['cgroup_root_inode'] is None


def test_missing_ticks_never_claims_exit_or_clears_the_group(monkeypatch):
    module = tool('wsl_runtime')
    info = {'pid': 123, 'created_at': 18, 'launch_id': 'b' * 32, 'cgroup': {}}
    monkeypatch.setattr(module, 'load', lambda: {'id': 'a' * 32, 'processes': {'api': info}})
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: pytest.fail('旧记录不能自动认领当前PID'))
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(stop=lambda *_a, **_k: pytest.fail('迁移前不得清组')))
    for operation in [lambda: module.owned_process(info, {'id': 'a' * 32}), lambda: module.stop_role('api')]:
        with pytest.raises(RuntimeError, match='requires_identity_migration'):
            operation()
    assert info['created_at'] == 18 and 'start_ticks' not in info


def test_boot_mismatch_reports_registered_roles_as_unknown_instead_of_an_empty_list(monkeypatch):
    module = tool('wsl_runtime')
    data = {'id': 'a' * 32, 'boot_id': 'legacy', 'databases_initialized': True,
        'processes': {'api': {'pid': 123, 'launch_id': 'b' * 32}}}
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'boot_identity', lambda: 'stable')
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: pytest.fail('不明启动不能认领PID'))
    result = module.status()
    assert result['requires_identity_migration'] and result['process_state'] == 'identity_unknown'
    assert result['processes'][0]['role'] == 'api'
    assert result['processes'][0]['owned'] is None and result['processes'][0]['running'] is None


def test_reconcile_refuses_live_legacy_record_without_signalling(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'boot_identity', lambda: 'new')
    monkeypatch.setattr(module, 'load', lambda: {'id': 'a' * 32, 'boot_id': 'old',
        'processes': {'api': {'pid': 123, 'created_at': 18}}})
    monkeypatch.setattr(module.psutil, 'Process', lambda *_: SimpleNamespace(pid=123))
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(names=lambda *_: (tmp_path / 'absent', None)))
    monkeypatch.setattr(module, 'update', lambda *_: pytest.fail('旧记录仍活不能更改清单'))
    with pytest.raises(RuntimeError, match='requires_identity_migration'):
        module.reconcile_boot()


def initialization_harness(tmp_path, monkeypatch):
    """只构造临时目录和Mock进程，任何数据库或WSL命令均不会实际执行。"""
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    for folder in ('data/postgres', 'secrets', 'logs', 'init'):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    data = {'id': 'a' * 32, 'boot_id': 'boot', 'databases_initialized': False,
        'database_initialization_attempted': False, 'processes': {}, 'failures': []}
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'boot_identity', lambda: 'boot')
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'update', lambda change: change(data) or data)
    monkeypatch.setattr(module, 'vault', lambda: {name: 'test-' + name for name in module.SECRETS})
    monkeypatch.setattr(module, 'account', lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000))
    monkeypatch.setattr(module.os, 'chown', lambda *a: None, raising=False)
    monkeypatch.setattr(module, 'directory', lambda *a: None)
    monkeypatch.setattr(module, 'specs', lambda *a: {'postgres': {}, 'redis': {}})
    events = []
    def spawn(role, spec, value, launch):
        events.append('start:' + role)
        data['processes'][role] = {'launch_id': launch}
    monkeypatch.setattr(module, 'spawn', spawn)
    monkeypatch.setattr(module, 'wait_listening', lambda role: events.append('ready:' + role))
    monkeypatch.setattr(module, 'stop_role', lambda role, **kw: events.append('stop:' + role) or {'stopped': True})
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kw: events.append(args) or SimpleNamespace(stdout=b'', stderr=b''))
    return module, data, events


@pytest.mark.parametrize('fail_migration', [False, True])
def test_fresh_initialization_requires_redis_and_reaps_both_dependencies(tmp_path, monkeypatch, fail_migration):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    def migrate(*args):
        assert events[-2:] == ['start:redis', 'ready:redis']
        events.append('migrate')
        if fail_migration:
            raise RuntimeError('migration_failed')
        return {'databases_initialized': True}
    monkeypatch.setattr(module, 'migrate_databases', migrate)
    monkeypatch.setattr(module, 'initialize_plugin_database', lambda *a: {'plugin_database_initialized': True})
    if fail_migration:
        with pytest.raises(RuntimeError, match='migration_failed'):
            module.init_data()
        assert data['failures'][-1]['phase'] == 'init-data'
    else:
        assert module.init_data()['databases_initialized']
    assert events[-3:] == ['migrate', 'stop:redis', 'stop:postgres']
    assert sum(isinstance(event, list) and event[0].endswith('/initdb') for event in events) == 1


def test_dependency_cleanup_does_not_abandon_postgres_after_redis_stop_error(tmp_path, monkeypatch):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    data['processes'] = {role: {'launch_id': 'b' * 32} for role in ('redis', 'postgres')}
    def stop(role, **kwargs):
        events.append(role)
        if role == 'redis':
            raise OSError('sensitive OS diagnostic')
        return {'stopped': True}
    monkeypatch.setattr(module, 'stop_role', stop)
    with pytest.raises(RuntimeError, match='initialization_cleanup_incomplete'):
        module.cleanup_initialization('b' * 32)
    assert events == ['redis', 'postgres']
    assert data['failures'][-1]['cleanup'] == [{'role': 'redis', 'error_type': 'OSError'}]


def reviewed_redis_failure(module, data, tmp_path):
    data['database_initialization_attempted'] = True
    data['failures'] = [{'phase': 'init-data', 'error_type': 'CalledProcessError', 'at': 'original'}]
    content = b'db_migration_lock.py\nacquired = bool(lock.acquire(*args, **kwargs))\nredis.exceptions.ConnectionError: Error 111 connecting to 127.0.0.1:26379. Connection refused.\n'
    (tmp_path / 'logs/init-data-migration.log').write_bytes(content)
    return module.hashlib.sha256(content).hexdigest(), content


@pytest.mark.parametrize('fault', ['runtime', 'digest', 'timeout', 'sql', 'attempted', 'migrated'])
def test_empty_recovery_rejects_different_failure_or_automatic_replay(tmp_path, monkeypatch, fault):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    digest, content = reviewed_redis_failure(module, data, tmp_path)
    expected = data['id']
    if fault == 'runtime': expected = 'b' * 32
    if fault == 'digest': digest = '0' * 64
    if fault == 'timeout': data['failures'][-1]['error_type'] = 'TimeoutExpired'
    if fault == 'attempted': data['empty_migration_recovery'] = {'status': 'failed'}
    if fault == 'migrated': data['databases_initialized'] = True
    if fault == 'sql':
        content += b'Running upgrade abc -> def\n'
        (tmp_path / 'logs/init-data-migration.log').write_bytes(content)
        digest = module.hashlib.sha256(content).hexdigest()
    with pytest.raises(RuntimeError):
        module.recover_empty_migration(expected, digest)
    assert events == []


@pytest.mark.parametrize('preflight_fails', [False, True])
def test_empty_recovery_never_reinitializes_or_recreates_database_and_keeps_failure(tmp_path, monkeypatch, preflight_fails):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    digest, content = reviewed_redis_failure(module, data, tmp_path)
    monkeypatch.setattr(module, 'validate_empty_cluster_files', lambda value: events.append('files-verified'))
    def preflight(*args):
        assert events[-2:] == ['start:redis', 'ready:redis']
        events.append('preflight')
        if preflight_fails:
            raise RuntimeError('empty_recovery_business_schema_not_empty')
        return {'verified': True}
    monkeypatch.setattr(module, 'verify_empty_databases', preflight)
    monkeypatch.setattr(module, 'migrate_databases', lambda *a: events.append('migrate') or {'databases_initialized': True})
    monkeypatch.setattr(module, 'initialize_plugin_database', lambda *a: {'plugin_database_initialized': True})
    if preflight_fails:
        with pytest.raises(RuntimeError, match='business_schema_not_empty'):
            module.recover_empty_migration(data['id'], digest)
        assert 'migrate' not in events and data['empty_migration_recovery']['status'] == 'failed'
    else:
        assert module.recover_empty_migration(data['id'], digest)['recovered_empty_migration']
        assert data['empty_migration_recovery']['status'] == 'ready'
    assert not any(isinstance(event, list) for event in events)
    assert data['failures'][0] == {'phase': 'init-data', 'error_type': 'CalledProcessError', 'at': 'original'}
    archive = tmp_path / 'logs' / data['empty_migration_recovery']['failure_log_archive']
    assert archive.read_bytes() == content
    assert events[-2:] == ['stop:redis', 'stop:postgres']
    with pytest.raises(RuntimeError, match='previous_empty_migration_recovery_requires_review'):
        module.recover_empty_migration(data['id'], digest)


def test_migration_common_helper_retains_timeouts_and_redacts_secret_diagnostics(tmp_path, monkeypatch):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    secret = {'redis': 'sensitive-token'}
    monkeypatch.setattr(module, 'api_environment', lambda *a: {'PATH': '/usr/bin'})
    def migrate(args, **kwargs):
        assert kwargs['timeout'] == 300
        assert kwargs['env']['PGOPTIONS'] == '-c statement_timeout=120000 -c lock_timeout=10000'
        raise module.subprocess.CalledProcessError(1, args, output=b'partial', stderr=b'sensitive-token')
    monkeypatch.setattr(module.subprocess, 'run', migrate)
    with pytest.raises(module.subprocess.CalledProcessError):
        module.migrate_databases(data, secret)
    text = (tmp_path / 'logs/init-data-migration.log').read_text()
    assert 'sensitive-token' not in text and '[REDACTED]' in text
    assert not data['databases_initialized']


@pytest.mark.parametrize('foreign', ['none', 'table', 'owner', 'role', 'schema'])
def test_empty_database_metadata_probe_is_readonly_local_and_rejects_foreign_content(tmp_path, monkeypatch, capsys, foreign):
    module = tool('wsl_runtime')
    connections = []
    statements = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query):
            statements.append(query)
            self.query = query
        def fetchall(self):
            query = self.query
            if query.startswith('EXPLAIN '): return [('Metadata scan',)]
            if 'data_directory' in query:
                address = '127.0.0.1' if 'host(inet_server_addr())' in query else '127.0.0.1/32'
                return [('/owned/new/postgres', '25432', 'agent_admin', 'postgres', address, 160014)]
            if 'FROM pg_database' in query:
                return [('dify', 'foreign' if foreign == 'owner' else 'dify_app', False), ('dify_plugin', 'dify_app', False),
                    ('postgres', 'agent_admin', False), ('template0', 'agent_admin', True), ('template1', 'agent_admin', True)]
            if 'FROM pg_roles' in query:
                return [('agent_admin', True, True, True, True, True, True), ('dify_app', True, foreign == 'role', False, False, False, False)]
            if 'SELECT nspname' in query: return [('pgfoo' if foreign == 'schema' else 'public', 'pg_database_owner')]
            if 'FROM pg_class' in query: return [(1 if foreign == 'table' else 0,)]
            if 'FROM pg_proc' in query: return [(0,)]
            if 'FROM pg_extension' in query: return [('plpgsql',)]
            pytest.fail('意外查询')
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    def connect(**kwargs):
        assert kwargs['host'] == '127.0.0.1' and kwargs['port'] == 25432
        assert kwargs['connect_timeout'] == 3
        assert 'default_transaction_read_only=on' in kwargs['options']
        connections.append(kwargs['dbname'])
        return Connection()
    monkeypatch.setitem(sys.modules, 'psycopg2', SimpleNamespace(connect=connect))
    monkeypatch.setenv('AIC_PG_ADMIN', 'in-memory-only')
    monkeypatch.setenv('AIC_EXPECTED_PGDATA', '/owned/new/postgres')
    script = compile(module.empty_database_probe_script(), '<isolated metadata probe>', 'exec')
    if foreign == 'none':
        exec(script, {})
        receipt = json.loads(capsys.readouterr().out)
        assert receipt['connections'] == 3 and receipt['metadata_statements'] == 22
        assert connections == ['postgres', 'dify', 'dify_plugin']
    else:
        with pytest.raises(RuntimeError, match='empty_recovery_'):
            exec(script, {})
    assert len(statements) <= 22
    assert all(query.startswith(('EXPLAIN SELECT ', 'SELECT ')) for query in statements)
    assert all(statements[index] == 'EXPLAIN ' + statements[index + 1] for index in range(0, len(statements), 2))


@pytest.mark.parametrize('fault', ['none', 'owner', 'version', 'pid', 'live', 'receipt'])
def test_empty_cluster_files_require_same_prepared_owner_and_no_old_service(tmp_path, monkeypatch, fault):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    pgdata = tmp_path / 'data/postgres'
    (pgdata / 'global').mkdir()
    (pgdata / 'PG_VERSION').write_text('15' if fault == 'version' else '16')
    (pgdata / 'postgresql.conf').write_text("listen_addresses='127.0.0.1'\nport=25432\n")
    (pgdata / 'global/pg_control').write_bytes(b'local-new-cluster')
    (tmp_path / 'prepare-attempt.json').write_text(json.dumps({'status': 'ready',
        'runtime_id': 'b' * 32 if fault == 'receipt' else data['id']}))
    owner = pgdata.stat().st_uid
    monkeypatch.setattr(module, 'account', lambda _: SimpleNamespace(pw_uid=owner + (1 if fault == 'owner' else 0)))
    if fault == 'pid': (pgdata / 'postmaster.pid').write_text('123')
    if fault == 'live': data['processes'] = {'postgres': {'cgroup': {}}}
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace(live_members=lambda _: [123]))
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): events.append(address)
    monkeypatch.setattr(module.socket, 'socket', Socket)
    if fault == 'none':
        module.validate_empty_cluster_files(data)
        assert events == [('127.0.0.1', 25432), ('127.0.0.1', 26379)]
    else:
        with pytest.raises(RuntimeError, match='empty_recovery_'):
            module.validate_empty_cluster_files(data)
        assert events == []


@pytest.mark.parametrize('fault', ['none', 'different', 'sql', 'preflight', 'cleanup', 'another_role', 'replayed'])
def test_reviewed_redis_spawn_resume_requires_precise_no_sql_failure_and_preserves_history(tmp_path, monkeypatch, fault):
    module, data, events = initialization_harness(tmp_path, monkeypatch)
    digest, content = reviewed_redis_failure(module, data, tmp_path)
    old_launch = 'b' * 32
    previous = {'status': 'failed', 'runtime_id': data['id'], 'launch_id': old_launch,
        'failure_log_sha256': digest, 'error_type': 'RuntimeError'}
    data['empty_migration_recovery'] = previous
    data['failures'].extend([
        {'phase': 'spawn', 'role': 'redis', 'launch_id': old_launch, 'error_type': 'RuntimeError',
            'cleanup': {'verified': True, 'stopped': True, 'alive_pids': []}},
        {'phase': 'recover-empty-migration', 'launch_id': old_launch, 'error_type': 'RuntimeError'}])
    if fault == 'different': old_launch = 'c' * 32
    if fault == 'sql': previous['phase'] = 'empty-preflight-attempted'
    if fault == 'preflight': previous['preflight'] = {'verified': True}
    if fault == 'cleanup': data['failures'][1]['cleanup']['stopped'] = False
    if fault == 'another_role': data['failures'][1]['role'] = 'postgres'
    if fault == 'replayed': previous['history'] = [{'status': 'failed'}]
    monkeypatch.setattr(module, 'validate_empty_cluster_files', lambda *_: events.append('files-verified'))
    monkeypatch.setattr(module, 'verify_empty_databases', lambda *a: {'verified': True})
    monkeypatch.setattr(module, 'migrate_databases', lambda *a: {'databases_initialized': True})
    monkeypatch.setattr(module, 'initialize_plugin_database', lambda *a: {'plugin_database_initialized': True})
    original_failures = json.loads(json.dumps(data['failures']))
    if fault == 'none':
        assert module.recover_empty_migration(data['id'], digest, old_launch)['recovered_empty_migration']
        assert data['empty_migration_recovery']['history'] == [previous]
        assert data['failures'] == original_failures
        assert not any(isinstance(event, list) for event in events)
        assert events[-2:] == ['stop:redis', 'stop:postgres']
    else:
        with pytest.raises(RuntimeError, match='reviewed_redis_spawn_failure_'):
            module.recover_empty_migration(data['id'], digest, old_launch)
        assert not events


def tool_network(module, data):
    return {'distro': module.DISTRO, 'root': str(module.ROOT), 'runtime_id': data['id'], 'boot_id': data['boot_id'],
        'keeper_id': 'c' * 32, 'source_ip': '172.24.81.83', 'bind_ip': '172.24.80.1', 'prefix_length': 20}


@pytest.mark.parametrize('fault', ['none', 'runtime', 'boot', 'keeper', 'gateway', 'source', 'stopped', 'reconcile', 'public', 'loopback'])
def test_tool_bridge_exception_requires_current_runtime_keeper_and_exact_private_nat(tmp_path, monkeypatch, fault):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    data = {'id': 'a' * 32, 'boot_id': 'b' * 64}
    network = tool_network(module, data)
    current = {**network, 'running': True, 'requires_boot_reconciliation': False}
    if fault == 'runtime': network['runtime_id'] = 'd' * 32
    if fault == 'boot': network['boot_id'] = 'd' * 64
    if fault == 'keeper': current['keeper_id'] = 'd' * 32
    if fault == 'gateway': current['bind_ip'] = '172.24.80.2'
    if fault == 'source': current['source_ip'] = '172.24.81.84'
    if fault == 'stopped': current['running'] = False
    if fault == 'reconcile': current['requires_boot_reconciliation'] = True
    if fault == 'public': network['bind_ip'] = '8.8.8.8'
    if fault == 'loopback': network['bind_ip'] = '127.0.0.1'
    if fault == 'none':
        module.validate_tool_bridge_network(data, network, current)
    else:
        with pytest.raises(RuntimeError, match='tool_bridge_'):
            module.validate_tool_bridge_network(data, network, current)


def test_squid_exception_is_only_exact_host_port_get_and_sixteen_existing_paths():
    import re
    module = tool('wsl_runtime')
    paths = module.tool_bridge_paths()
    assert len(paths) == 16 and '/api/v1/business-tools/openapi.json' in paths
    acl = module.tool_bridge_acl({}, {'proof': {'network': {'bind_ip': '172.24.80.1'}}})
    config = module.squid_configuration({}, special=False, tool_acl=acl)
    assert 'acl Safe_ports port 80 443\n' in config
    assert 'acl aic_tools_dst dst 172.24.80.1/32\n' in config
    assert 'acl aic_tools_port port 13002\n' in config and 'acl aic_tools_method method GET\n' in config
    allow = 'http_access allow aic_tools_dst aic_tools_port aic_tools_method aic_tools_path'
    assert config.index(allow) < config.index('http_access deny private_dst') < config.index('http_access deny !Safe_ports')
    patterns = next(line for line in acl.splitlines() if line.startswith('acl aic_tools_path')).split()[3:]
    for path in paths:
        concrete = '/platform' + path.replace('{id}', 'sample-id')
        assert any(re.fullmatch(pattern, concrete + '?limit=10') for pattern in patterns)
    for path in ['/platform/api/v1/secret-references', '/platform/api/v1/devices/abc/extra',
            '/owui/api/v1/models', '/platform/api/v1/queries', '/platform/api/v1/devicesABC']:
        assert not any(re.fullmatch(pattern, path) for pattern in patterns)
    agent = module.squid_configuration({}, special=True, tool_acl=acl)
    assert 'aic_tools_' not in agent and 'http_access allow aic_control aic_control_ports' in agent
    assert 'http_access deny private_dst' in agent and 'http_access deny !Safe_ports' in agent


@pytest.mark.parametrize('value', [r'\\server\share\config.json', r'..\config.json', 'E:relative.json', r'E:\root\..\other'])
def test_tool_bridge_snapshot_cannot_redirect_to_unc_or_relative_windows_path(value):
    module = tool('wsl_runtime')
    with pytest.raises(RuntimeError, match='windows_path_invalid'):
        module.mounted_windows_path(value)


@pytest.mark.parametrize('fault', ['none', 'sha', 'not_ready', 'port', 'network', 'upstreams', 'runtime'])
def test_tool_bridge_snapshot_matches_current_windows_reservation_before_any_write(tmp_path, monkeypatch, fault):
    module = tool('wsl_runtime')
    project = tmp_path / 'project'
    monkeypatch.setattr(module, '__file__', str(project / 'deploy/native/wsl_runtime.py'))
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'linux-runtime')
    data = {'id': 'a' * 32, 'boot_id': 'b' * 64}
    network = tool_network(module, data)
    folder = project / '.local/native_windows/wsl-http-bridge'
    folder.mkdir(parents=True)
    path = folder / ('d' * 32 + '.json')
    config = {'kind': 'ai-agent-company-wsl-http-bridge-v1', 'port': 13002, 'native_root': 'windows-root',
        'native_runtime_id': 'e' * 32, 'network': network, 'upstreams': {'openwebui': {}, 'platform': {}}}
    path.write_text(json.dumps(config))
    info = {'ready': True, 'config_file': 'windows-config', 'config_sha256': module.file_sha(path),
        'network': network, 'upstreams': config['upstreams'], 'listener': {'pid': 123},
        'bridge_port': 13002, 'launch_id': 'f' * 24}
    if fault == 'sha': info['config_sha256'] = '0' * 64
    if fault == 'not_ready': info['ready'] = False
    if fault == 'port': info['bridge_port'] = 8080
    if fault == 'network': info['network'] = {}
    if fault == 'upstreams': info['upstreams'] = {}
    native = {'id': '0' * 32 if fault == 'runtime' else 'e' * 32, 'processes': {'wsl-http-bridge': info}}
    (folder.parent / 'runtime.json').write_text(json.dumps(native))
    monkeypatch.setattr(module, 'mounted_windows_path', lambda value: {'windows-root': folder.parent, 'windows-config': path}[value])
    monkeypatch.setattr(module, 'tool_bridge_context', lambda: {**network, 'running': True})
    monkeypatch.setattr(module, 'atomic_json', lambda *a: pytest.fail('核验不应写入或启动任何东西'))
    if fault == 'none':
        assert module.verify_tool_bridge_snapshot(data, path)['bridge_launch_id'] == 'f' * 24
    else:
        with pytest.raises(RuntimeError, match='windows_reservation_changed'):
            module.verify_tool_bridge_snapshot(data, path)


def test_explicit_tool_bridge_configuration_preserves_agent_proxy_and_does_not_reload(tmp_path, monkeypatch):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/agent-squid.conf').write_text('unchanged-agent-proxy')
    (tmp_path / 'config/nginx.conf').write_text('unchanged-gateway')
    data = {'id': 'a' * 32, 'boot_id': 'b' * 64}
    proof = {'network': tool_network(module, data), 'bridge_launch_id': 'd' * 24, 'snapshot_sha256': 'e' * 64}
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'load', lambda: data)
    monkeypatch.setattr(module, 'update', lambda change: change(data) or data)
    monkeypatch.setattr(module, 'verify_tool_bridge_snapshot', lambda *a: proof)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('配置不能启动/重载/发请求'))
    result = module.configure_tool_bridge(tmp_path / 'reviewed.json')
    assert result['requires_proxy_reload'] and not result['agent_proxy_changed']
    assert (tmp_path / 'config/agent-squid.conf').read_text() == 'unchanged-agent-proxy'
    assert (tmp_path / 'config/nginx.conf').read_text() == 'unchanged-gateway'
    assert 'aic_tools_dst dst 172.24.80.1/32' in (tmp_path / 'config/squid.conf').read_text()
    assert json.loads((tmp_path / 'tool-bridge.json').read_text())['proof'] == proof
    assert data['tool_bridge_history'][0]['snapshot_sha256'] == 'e' * 64


@pytest.mark.parametrize('fault', ['none', 'stale', 'writable'])
def test_tool_bridge_receipt_revalidates_snapshot_and_drops_stale_or_writable_binding(tmp_path, monkeypatch, fault):
    module = tool('wsl_runtime')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    receipt = tmp_path / 'tool-bridge.json'
    proof = {'network': {'bind_ip': '172.24.80.1'}}
    receipt.write_text(json.dumps({'snapshot': 'old.json', 'proof': proof}))
    original_stat, events = Path.stat, []
    monkeypatch.setattr(Path, 'stat', lambda path, *a, **k: SimpleNamespace(st_uid=0,
        st_mode=0o666 if fault == 'writable' else 0o600) if path == receipt else original_stat(path, *a, **k))
    def verify(*a):
        events.append('snapshot-checked')
        if fault == 'stale': raise RuntimeError('stale-boot')
        return proof
    monkeypatch.setattr(module, 'verify_tool_bridge_snapshot', verify)
    acl = module.tool_bridge_acl({})
    assert bool(acl) == (fault == 'none')
    assert events == ([] if fault == 'writable' else ['snapshot-checked'])
    if fault != 'none':
        assert 'aic_tools_' not in module.squid_configuration({}, special=False, tool_acl=acl)


def test_runtime_start_rewrites_stale_proxy_configuration_before_any_service_or_network_start(monkeypatch):
    module = tool('wsl_runtime')
    events = []
    monkeypatch.setattr(module, 'check_root', lambda: None)
    monkeypatch.setattr(module, 'doctor', lambda: {'ready_to_start': True})
    monkeypatch.setattr(module, 'load', lambda: {'id': 'a' * 32, 'processes': {}})
    monkeypatch.setattr(module, 'write_configs', lambda *_: events.append('proxy-config-refreshed'))
    def create(*a):
        assert events == ['proxy-config-refreshed']
        raise RuntimeError('mock-stop-before-service-start')
    monkeypatch.setitem(sys.modules, 'wsl_cgroups', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'wsl_network', SimpleNamespace(create=create))
    with pytest.raises(RuntimeError, match='mock-stop-before-service-start'):
        module.start()
