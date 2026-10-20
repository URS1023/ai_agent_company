"""Docker控制器纯Mock回归；不启动CLI、容器、数据库或模型。"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from cryptography.fernet import Fernet
import pytest

REPO = Path(__file__).resolve().parents[3]


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def controller(monkeypatch, tmp_path):
    value = module_at('_test_docker_openwebui', REPO / 'deploy/docker/openwebui.py')
    monkeypatch.setattr(value, 'DEFAULT_ROOT', tmp_path / 'new-runtime')
    value._real_host_checks = value._host_checks
    monkeypatch.setattr(value, '_host_checks', lambda: None)
    monkeypatch.setattr(value, '_resources', lambda: {'available_bytes': 3 * value.GIB, 'commit_free_bytes': 4 * value.GIB})
    value._real_disk = value._disk
    monkeypatch.setattr(value, '_disk', lambda root, compressed_bytes=0: value.MIN_DISK)
    monkeypatch.setattr(value, '_port_free', lambda: None)
    # 没有显式Mock的CLI调用直接失败，避免测试误触本机Docker。
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('禁止真实子进程'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('禁止真实子进程'))
    return value


@pytest.fixture
def injector():
    return module_at('_test_docker_provider_entrypoint', REPO / 'deploy/docker/provider_entrypoint.py')


@pytest.fixture
def credentials(injector):
    return {'webui_secret': 'test-runtime-secret-' * 3, 'provider_settings': {
        'chat': {'provider': 'opencode-go', 'base_url': injector.CHAT_URL, 'model': injector.CHAT_MODEL, 'api_key': 'chat-test-only-key'},
        'embedding': {'base_url': injector.EMBEDDING_URL, 'model': injector.EMBEDDING_MODEL, 'api_key': 'embedding-test-only-key'}}}


def candidate():
    return {'Descriptor': {'digest': 'sha256:' + 'a' * 64, 'platform': {'os': 'linux', 'architecture': 'amd64'}},
        'SchemaV2Manifest': {'config': {'digest': 'sha256:' + 'b' * 64, 'size': 80},
            'layers': [{'digest': 'sha256:' + 'c' * 64, 'size': 1200}]}}


def seed(controller, *, phase='pulled', registered=True):
    root = controller.DEFAULT_ROOT
    root.mkdir()
    for folder in ['data', 'entry', 'client']:
        (root / folder).mkdir()
    content = (REPO / 'deploy/docker/provider_entrypoint.py').read_bytes()
    (root / 'entry/provider_entrypoint.py').write_bytes(content)
    (root / 'runtime.key').write_bytes(Fernet.generate_key())
    (root / 'credentials.enc').write_bytes(b'unused-pure-mock-ciphertext')
    image = controller.select_candidate(candidate())
    image.update(id='sha256:' + 'd' * 64, size_bytes=5000)
    data = {'kind': controller.KIND, 'id': 'e' * 32, 'root': str(root), 'engine': controller.ENGINE,
        'project': controller.PROJECT, 'data_directory': str(root / 'data'), 'sqlite_file': str(root / 'data/webui.db'),
        'image': image, 'container': {'id': 'f' * 64 if registered else None, 'name': controller.NAME,
            'created_at': '2026-10-20T00:00:00Z' if registered else None},
        'entrypoint_sha256': hashlib.sha256(content).hexdigest(), 'phase': phase,
        'ready': registered, 'start_attempted': False, 'pull_attempted': False,
        'frontend_expected': True, 'frontend_available': None, 'failures': []}
    controller._atomic_json(root / controller.MANIFEST, data)
    return root, data


def raw_container(controller, root, data, *, running=True):
    ports = {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(controller.PORT)}]}
    return {'Id': data['container']['id'] or 'f' * 64, 'Name': '/' + controller.NAME,
        'Image': data['image']['id'], 'Created': '2026-10-20T00:00:00Z', 'Labels': controller._labels(data),
        'Entrypoint': ['python3'], 'Cmd': ['/run/agent-runtime/provider_entrypoint.py'],
        'Healthcheck': {'Test': ['NONE']},
        'Mounts': [{'Type': 'bind', 'Source': source, 'Destination': target, 'RW': not readonly}
            for target, (source, readonly) in controller._mounts(root).items()],
        'State': {'Running': running, 'Status': 'running' if running else 'exited', 'OOMKilled': False,
            'Dead': False, 'ExitCode': 0, 'Pid': 70, 'StartedAt': '2026-10-20T00:00:01Z', 'FinishedAt': '', 'Error': ''},
        'HostConfig': {'Memory': controller.MEMORY, 'MemorySwap': controller.MEMORY,
            'NanoCpus': controller.NANO_CPUS, 'PidsLimit': controller.PIDS, 'RestartPolicy': {'Name': 'no'},
            'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'],
            'PortBindings': ports, 'NetworkMode': 'bridge'}, 'Ports': ports if running else {}}


def test_candidate_locks_only_one_amd64_manifest(controller):
    arm = copy.deepcopy(candidate())
    arm['Descriptor']['platform']['architecture'] = 'arm64'
    value = controller.select_candidate([arm, candidate()])
    assert value['compressed_size_bytes'] == 1280
    assert value['repo_digest'].endswith('@sha256:' + 'a' * 64)
    assert value['candidate_only'] is True
    assert value['id'] is None
    with pytest.raises(controller.RuntimeBoundaryError, match='ambiguous'):
        controller.select_candidate([candidate(), candidate()])


@pytest.mark.parametrize('field,value', [('digest', 'latest'), ('platform', {'os': 'windows', 'architecture': 'amd64'})])
def test_candidate_rejects_unpinned_or_wrong_os(controller, field, value):
    item = candidate()
    item['Descriptor'][field] = value
    with pytest.raises(controller.RuntimeBoundaryError):
        controller.select_candidate(item)


def test_candidate_requires_layer_sizes(controller):
    item = candidate()
    item['SchemaV2Manifest']['layers'][0]['size'] = -1
    with pytest.raises(controller.RuntimeBoundaryError, match='layer_budget'):
        controller.select_candidate(item)


def test_public_host_resource_guard_returns_same_snapshot_and_failure(controller, monkeypatch):
    value = {'available_bytes': 3 * controller.GIB, 'commit_free_bytes': 4 * controller.GIB}
    monkeypatch.setattr(controller, '_resources', lambda: value)
    assert controller.check_host_resources() is value
    def unavailable(): raise RuntimeError('资源不足')
    monkeypatch.setattr(controller, '_resources', unavailable)
    with pytest.raises(RuntimeError, match='资源不足'):
        controller.check_host_resources()


def test_first_disk_check_walks_real_uncreated_parent_tree(controller, tmp_path, monkeypatch):
    # 使用真实临时目录和disk_usage，避免把首次新目录不存在的问题Mock掉。
    target = tmp_path / 'not-created/agent-platform/new-runtime'
    monkeypatch.setattr(controller, 'MIN_DISK', 1)
    monkeypatch.setattr(controller, 'GIB', 1)
    assert controller._real_disk(target) == 2
    assert not target.parent.exists()


def test_disk_budget_uses_larger_of_floor_and_layer_allowance(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, 'GIB', 100)
    monkeypatch.setattr(controller, 'MIN_DISK', 1200)
    monkeypatch.setattr(controller.shutil, 'disk_usage', lambda path: type('Disk', (), {'free': 1500})())
    assert controller._real_disk(tmp_path / 'new') == 1200
    with pytest.raises(controller.RuntimeBoundaryError, match='disk_budget'):
        controller._real_disk(tmp_path / 'new', compressed_bytes=700)


def test_prepare_new_directory_encrypted_and_no_pull(controller, credentials, monkeypatch):
    calls = []
    def docker(root, args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps(candidate()) if args[:2] == ['manifest', 'inspect'] else ''
    monkeypatch.setattr(controller, '_docker', docker)
    monkeypatch.setattr(controller, '_provider_payload', lambda: credentials['provider_settings'])
    result = controller.prepare(controller.DEFAULT_ROOT)
    root = controller.DEFAULT_ROOT
    assert result['phase'] == 'prepared' and result['frontend_available'] is None
    assert result['source_equivalence_verified'] is False
    assert not any(args[0] in {'pull', 'create', 'start', 'run'} for args, _ in calls)
    assert calls[-1][1]['seconds'] <= controller.PREPARE_SECONDS
    plaintext = json.dumps(result) + (root / 'credentials.enc').read_bytes().decode()
    assert 'chat-test-only-key' not in plaintext and 'embedding-test-only-key' not in plaintext
    decrypted = json.loads(Fernet((root / 'runtime.key').read_bytes()).decrypt((root / 'credentials.enc').read_bytes()))
    assert decrypted['provider_settings'] == credentials['provider_settings']
    assert list((root / 'data').iterdir()) == []
    with pytest.raises(FileExistsError):
        controller.prepare(controller.DEFAULT_ROOT)


def test_prepare_refuses_existing_container_name(controller, credentials, monkeypatch):
    monkeypatch.setattr(controller, '_docker', lambda *a, **kw: '1' * 64)
    monkeypatch.setattr(controller, '_provider_payload', lambda: pytest.fail('不得读取凭据'))
    with pytest.raises(controller.RuntimeBoundaryError, match='existing_container_name'):
        controller.prepare(controller.DEFAULT_ROOT)
    assert not (controller.DEFAULT_ROOT / 'credentials.enc').exists()


def test_load_fixed_sqlite_and_no_parent_escape(controller):
    root, data = seed(controller)
    assert controller.load(root)['sqlite_file'] == str(root / 'data/webui.db')
    for target in [root, root / '../outside']:
        with pytest.raises(controller.RuntimeBoundaryError):
            controller.ensure_inside(root, target)
    data['sqlite_file'] = 'C:/old/webui.db'
    controller._atomic_json(root / controller.MANIFEST, data)
    with pytest.raises(controller.RuntimeBoundaryError, match='sqlite_binding'):
        controller.load(root)


def test_pull_pinned_digest_one_attempt_and_900_total(controller, monkeypatch):
    root, data = seed(controller, phase='prepared', registered=False)
    calls = []
    def docker(root, args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ['image', 'inspect']:
            return json.dumps({'Id': data['image']['id'], 'RepoDigests': [data['image']['repo_digest']], 'Os': 'linux', 'Architecture': 'amd64', 'Size': 5000})
        return ''
    monkeypatch.setattr(controller, '_docker', docker)
    def pull_image(root, data, deadline):
        calls.append((['pull', '--platform', 'linux/amd64', data['image']['repo_digest']],
            {'seconds': deadline - controller.time.monotonic()}))
    monkeypatch.setattr(controller, '_pull_image', pull_image)
    assert controller.pull(root)['phase'] == 'pulled'
    assert calls[0][0] == ['pull', '--platform', 'linux/amd64', data['image']['repo_digest']]
    assert 899 <= calls[0][1]['seconds'] <= 900
    assert calls[1][1]['seconds'] <= 900
    with pytest.raises(controller.RuntimeBoundaryError, match='already_attempted'):
        controller.pull(root)
    assert len(calls) == 2


def test_pull_failure_checkpoint_prevents_replay(controller, monkeypatch):
    root, data = seed(controller, phase='prepared', registered=False)
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('upstream may have continued')
    monkeypatch.setattr(controller, '_pull_image', docker)
    with pytest.raises(TimeoutError):
        controller.pull(root)
    saved = controller.load(root)
    assert saved['phase'] == 'pull_failed' and saved['pull_attempted'] is True
    assert saved['resource_incident']['active'] is True
    assert 'upstream' not in json.dumps(saved)
    with pytest.raises(controller.RuntimeBoundaryError):
        controller.pull(root)
    assert len(calls) == 1


def test_pull_monitors_host_resources_and_terminates_only_owned_cli(controller, monkeypatch):
    root, data = seed(controller, phase='prepared', registered=False)
    class Process:
        pid = 2468
        returncode = None
        terminated = False
        def poll(self): return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -1
        def wait(self, timeout): return self.returncode
        def kill(self): pytest.fail('已停止的CLI不应再kill')
    process, captures = Process(), []
    def spawn(args, **kwargs):
        captures.append((args, kwargs))
        return process
    monkeypatch.setattr(controller.subprocess, 'Popen', spawn)
    monkeypatch.setattr(controller, '_resources', lambda: (_ for _ in ()).throw(RuntimeError('资源门限')))
    with pytest.raises(RuntimeError) as failure:
        controller._pull_image(root, data, controller.time.monotonic() + 900)
    assert process.terminated and failure.value.cli_cleanup['stopped'] is True
    assert captures[0][0][-4:] == ['pull', '--platform', 'linux/amd64', data['image']['repo_digest']]
    assert captures[0][1]['stdout'] == captures[0][1]['stderr'] == controller.subprocess.DEVNULL


def test_pull_expired_budget_does_not_spawn(controller, monkeypatch):
    root, data = seed(controller, phase='prepared', registered=False)
    with pytest.raises(controller.RuntimeBoundaryError, match='900_seconds'):
        controller._pull_image(root, data, controller.time.monotonic() - 1)


@pytest.mark.parametrize('key', ['DOCKER_HOST', 'DOCKER_CONTEXT'])
def test_host_override_rejected_before_inspecting_or_connecting(controller, monkeypatch, key):
    monkeypatch.setenv(key, 'unapproved-target')
    with pytest.raises(controller.RuntimeBoundaryError, match='override_or_non_windows'):
        controller._real_host_checks()


def test_inspect_returns_filtered_state_without_raw_error_or_env(controller, monkeypatch):
    root, data = seed(controller)
    raw = raw_container(controller, root, data, running=False)
    raw['State'].update(OOMKilled=True, Error='secret error body')
    raw['Env'] = ['SECRET=must-never-output']
    calls = []
    def docker(root, args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps(raw)
    monkeypatch.setattr(controller, '_docker', docker)
    result = controller.inspect_owned(root, timeout_seconds=0.8)
    assert result['ownership_verified'] and result['state']['oom_killed'] and not result['state']['running']
    assert result['state']['error_present'] and result['frontend_available'] is None
    assert 'secret' not in json.dumps(result) and 'Env' not in json.dumps(result)
    assert calls[0][1]['seconds'] <= 0.8
    assert 'Config.Env' not in controller.CONTAINER_FORMAT


@pytest.mark.parametrize('mutation', ['cid', 'image', 'label', 'mount_source', 'mount_rw', 'mount_extra', 'created'])
def test_inspect_rejects_identity_changes(controller, monkeypatch, mutation):
    root, data = seed(controller)
    raw = raw_container(controller, root, data)
    if mutation == 'cid': raw['Id'] = '0' * 64
    elif mutation == 'image': raw['Image'] = 'sha256:' + '0' * 64
    elif mutation == 'label': raw['Labels'][controller.LABEL_RUNTIME] = '0' * 32
    elif mutation == 'mount_source': raw['Mounts'][0]['Source'] = 'C:/old/data'
    elif mutation == 'mount_rw': raw['Mounts'][1]['RW'] = True
    elif mutation == 'mount_extra': raw['Mounts'].append(copy.deepcopy(raw['Mounts'][0]))
    else: raw['Created'] = 'different'
    monkeypatch.setattr(controller, '_docker', lambda *a, **kw: json.dumps(raw))
    with pytest.raises(controller.RuntimeBoundaryError):
        controller.inspect_owned(root)


@pytest.mark.parametrize('mutation', ['memory', 'swap', 'cpu', 'pids', 'restart', 'health', 'privileged', 'capadd', 'port', 'entrypoint'])
def test_inspect_rejects_resource_execution_and_public_port_drift(controller, monkeypatch, mutation):
    root, data = seed(controller)
    raw = raw_container(controller, root, data)
    key = {'memory': 'Memory', 'swap': 'MemorySwap', 'cpu': 'NanoCpus', 'pids': 'PidsLimit'}
    if mutation in key: raw['HostConfig'][key[mutation]] = 0
    elif mutation == 'restart': raw['HostConfig']['RestartPolicy']['Name'] = 'always'
    elif mutation == 'health': raw['Healthcheck']['Test'] = ['CMD', 'curl', '/health']
    elif mutation == 'privileged': raw['HostConfig']['Privileged'] = True
    elif mutation == 'capadd': raw['HostConfig']['CapAdd'] = ['SYS_ADMIN']
    elif mutation == 'port': raw['HostConfig']['PortBindings']['8080/tcp'][0]['HostIp'] = '0.0.0.0'
    else: raw['Entrypoint'] = ['bash']
    monkeypatch.setattr(controller, '_docker', lambda *a, **kw: json.dumps(raw))
    with pytest.raises(controller.RuntimeBoundaryError):
        controller.inspect_owned(root)


def test_stop_cas_refuses_replacement_without_cli(controller, monkeypatch):
    root, data = seed(controller)
    monkeypatch.setattr(controller, '_docker', lambda *a, **kw: pytest.fail('CAS失败不得执行Docker'))
    with pytest.raises(controller.RuntimeBoundaryError, match='compare_and_swap'):
        controller.stop_owned(root, expected_container_id='0' * 64)
    with pytest.raises(controller.RuntimeBoundaryError, match='compare_and_swap'):
        controller.stop_owned(root, expected_runtime_id='0' * 32)


def test_stop_drifted_limits_only_stops_verified_cid_preserves_data(controller, monkeypatch):
    root, data = seed(controller)
    (root / 'data/keep.txt').write_text('new-owned-data')
    raw = raw_container(controller, root, data)
    raw['HostConfig']['Memory'] = 0
    calls = []
    def docker(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ['container', 'stop']:
            raw['State']['Running'] = False
            return data['container']['id']
        return json.dumps(raw)
    monkeypatch.setattr(controller, '_docker', docker)
    result = controller.stop_owned(root, 'rss_budget_exceeded', expected_container_id=data['container']['id'], expected_runtime_id=data['id'])
    assert result['verified'] and result['stopped']
    assert calls[1][-1] == data['container']['id']
    assert all('rm' not in args for args in calls)
    assert (root / 'data/keep.txt').read_text() == 'new-owned-data'
    assert not controller.load(root)['ready']


def test_stop_identity_mismatch_never_sends_stop(controller, monkeypatch):
    root, data = seed(controller)
    raw = raw_container(controller, root, data)
    raw['Labels'][controller.LABEL_RUNTIME] = '0' * 32
    calls = []
    def docker(root, args, **kwargs):
        calls.append(args)
        return json.dumps(raw)
    monkeypatch.setattr(controller, '_docker', docker)
    with pytest.raises(controller.RuntimeBoundaryError, match='labels_changed'):
        controller.stop_owned(root)
    assert len(calls) == 1 and calls[0][:2] == ['container', 'inspect']


def test_stop_does_not_fake_success_if_still_running(controller, monkeypatch):
    root, data = seed(controller)
    raw = raw_container(controller, root, data)
    monkeypatch.setattr(controller, '_docker', lambda root, args, **kw: '' if args[:2] == ['container', 'stop'] else json.dumps(raw))
    with pytest.raises(controller.RuntimeBoundaryError, match='still_running'):
        controller.stop_owned(root)
    assert controller.load(root)['stops'][-1]['stopped'] is False


def fake_start_cli(controller, root, data, *, oom=False):
    calls = []
    raw = raw_container(controller, root, data, running=False)
    def docker(current, args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ['image', 'inspect']:
            return json.dumps({'Id': data['image']['id'], 'RepoDigests': [data['image']['repo_digest']], 'Os': 'linux', 'Architecture': 'amd64', 'Size': 5000})
        if args[:2] == ['container', 'ls']: return ''
        if args[:2] == ['container', 'create']:
            (root / 'created.cid').write_text(raw['Id'])
            return raw['Id']
        if args[:2] == ['container', 'start']:
            raw['State']['Running'] = True
            raw['State']['OOMKilled'] = oom
            raw['Ports'] = copy.deepcopy(raw['HostConfig']['PortBindings'])
            return raw['Id']
        if args[:2] == ['container', 'stop']:
            raw['State']['Running'] = False
            return raw['Id']
        if args[:2] == ['container', 'inspect']: return json.dumps(raw)
        if args[:2] == ['container', 'logs']: return 'INFO: Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)'
        pytest.fail('未知CLI分支')
    return docker, calls


def test_start_hard_caps_ro_secrets_no_env_and_no_business_probe(controller, monkeypatch):
    root, data = seed(controller, registered=False)
    docker, calls = fake_start_cli(controller, root, data)
    monkeypatch.setattr(controller, '_docker', docker)
    result = controller.start(root)
    assert result['ready'] and result['frontend_available'] is None
    assert result['limits']['memory_bytes'] == result['limits']['memory_swap_bytes'] == controller.GIB
    create = next(args for args, _ in calls if args[:2] == ['container', 'create'])
    assert '--no-healthcheck' in create and '--restart' in create
    assert '--env' not in create and '--env-file' not in create and 'build' not in create
    assert create[create.index('--publish') + 1] == '127.0.0.1:13080:8080'
    mounts = [create[index + 1] for index, arg in enumerate(create) if arg == '--mount']
    assert len(mounts) == 4 and sum(',readonly' in item for item in mounts) == 3
    assert all(kw['seconds'] <= 180 for _, kw in calls if 'seconds' in kw)
    assert controller.load(root)['start_attempted'] is True
    count = len(calls)
    with pytest.raises(controller.RuntimeBoundaryError, match='already_attempted'):
        controller.start(root)
    assert len(calls) == count


def test_start_oom_stops_only_created_container_and_preserves_failure(controller, monkeypatch):
    root, data = seed(controller, registered=False)
    docker, calls = fake_start_cli(controller, root, data, oom=True)
    monkeypatch.setattr(controller, '_docker', docker)
    with pytest.raises(controller.RuntimeBoundaryError, match='exited_or_oom'):
        controller.start(root)
    saved = controller.load(root)
    assert saved['phase'] == 'start_failed' and saved['start_attempted']
    assert saved['failures'][-1]['cleanup']['verified'] and saved['failures'][-1]['cleanup']['stopped']
    stops = [args for args, _ in calls if args[:2] == ['container', 'stop']]
    assert len(stops) == 1 and stops[0][-1] == 'f' * 64
    assert (root / 'data').is_dir() and not any('rm' in args for args, _ in calls)


def test_start_host_resource_drop_enters_same_owned_cleanup(controller, monkeypatch):
    root, data = seed(controller, registered=False)
    docker, calls = fake_start_cli(controller, root, data)
    monkeypatch.setattr(controller, '_docker', docker)
    snapshots = []
    def resources():
        snapshots.append(True)
        if len(snapshots) >= 2:
            raise RuntimeError('主机提交余量不足')
        return {'available_bytes': 3 * controller.GIB, 'commit_free_bytes': 4 * controller.GIB}
    monkeypatch.setattr(controller, '_resources', resources)
    with pytest.raises(RuntimeError, match='主机提交余量'):
        controller.start(root)
    stops = [args for args, _ in calls if args[:2] == ['container', 'stop']]
    assert len(stops) == 1 and stops[0][-1] == 'f' * 64
    saved = controller.load(root)
    assert saved['failures'][-1]['cleanup']['stopped'] is True
    assert saved['resource_incident']['active'] is True


def test_create_receipt_registered_before_inspection_failure_still_cleans_owned_container(controller, monkeypatch):
    root, data = seed(controller, registered=False)
    docker, calls = fake_start_cli(controller, root, data)
    failed = []
    def wrapped(current, args, **kwargs):
        if args[:2] == ['container', 'inspect'] and not failed:
            failed.append(True)
            raise RuntimeError('登记核验中断')
        return docker(current, args, **kwargs)
    monkeypatch.setattr(controller, '_docker', wrapped)
    with pytest.raises(RuntimeError, match='登记核验中断'):
        controller.start(root)
    assert controller.load(root)['failures'][-1]['cleanup']['stopped'] is True
    # 容器尚未start，故只核归属并确认已停，不产生多余start/stop调用。
    assert not any(args[:2] in [['container', 'start'], ['container', 'stop']] for args, _ in calls)


def test_start_existing_data_rejected_before_create(controller, monkeypatch):
    root, data = seed(controller, registered=False)
    (root / 'data/old.db').write_bytes(b'old')
    docker, calls = fake_start_cli(controller, root, data)
    monkeypatch.setattr(controller, '_docker', docker)
    with pytest.raises(controller.RuntimeBoundaryError, match='existing_data'):
        controller.start(root)
    assert not any(args[:2] == ['container', 'create'] for args, _ in calls)
    assert controller.load(root)['start_attempted'] is False


@pytest.mark.parametrize('method', ['inspect_owned', 'stop_owned'])
def test_nonpositive_deadline_never_calls_docker(controller, monkeypatch, method):
    monkeypatch.setattr(controller, '_docker', lambda *a, **kw: pytest.fail('禁止过期后调用CLI'))
    with pytest.raises(controller.RuntimeBoundaryError, match='deadline'):
        getattr(controller, method)(timeout_seconds=0)


def test_docker_wrapper_fixed_engine_sanitizes_env_and_stderr(controller, monkeypatch):
    captures = []
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-never-forward')
    monkeypatch.setenv('DATABASE_URL', 'postgres://old-business')
    def run(args, **kwargs):
        captures.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b'normal', b'INFO: listen marker')
    monkeypatch.setattr(subprocess, 'run', run)
    assert controller._docker(controller.DEFAULT_ROOT, ['version']) == 'normal'
    args, kwargs = captures[0]
    assert args[0] == str(controller.DOCKER) and args[1:3] == ['--host', controller.ENGINE]
    assert 'OPENAI_API_KEY' not in kwargs['env'] and 'DATABASE_URL' not in kwargs['env']
    assert kwargs['shell'] is False
    assert 'listen marker' in controller._docker(controller.DEFAULT_ROOT, ['container', 'logs', '--tail', '80', 'f' * 64])


def test_cli_does_not_print_raw_docker_error(controller, monkeypatch, capsys):
    def failure(*args, **kwargs):
        raise OSError('secret=must-never-print')
    monkeypatch.setattr(controller, 'prepare', failure)
    assert controller.main(['prepare']) == 1
    output = capsys.readouterr().out
    assert 'secret' not in output and 'operation_failed' in output


def test_provider_env_forces_local_storage_offline_and_real_go_headers(injector, credentials):
    inherited = {'PATH': '/usr/bin', 'DATABASE_URL': 'postgres://old', 'REDIS_URL': 'redis://old',
        'QDRANT_URI': 'http://old', 'CHROMA_HTTP_HOST': 'old', 'CHROMA_HTTP_HEADERS': 'secret=old',
        'HTTP_PROXY': 'http://old', 'https_proxy': 'http://old', 'SPACE_ID': 'old',
        'ADMIN_USER_PASSWORD': 'old', 'OPENAI_API_KEY': 'old', 'FRONTEND_BUILD_DIR': '/old/build'}
    env = injector.build_environment(credentials, inherited)
    assert env['DATABASE_URL'] == 'sqlite:////app/backend/data/webui.db' and env['VECTOR_DB'] == 'chroma'
    assert env['CHROMA_HTTP_HOST'] == '' and int(env['CHROMA_HTTP_PORT']) == 8000
    assert env['REDIS_URL'] == '' and env['WEBSOCKET_MANAGER'] == ''
    assert all(key not in env for key in ['QDRANT_URI', 'HTTP_PROXY', 'https_proxy', 'SPACE_ID', 'ADMIN_USER_PASSWORD', 'FRONTEND_BUILD_DIR'])
    assert env['HF_HUB_OFFLINE'] == '1' and env['TRANSFORMERS_OFFLINE'] == '1'
    assert env['RAG_EMBEDDING_MODEL_AUTO_UPDATE'] == 'False' and env['USE_OLLAMA_DOCKER'] == 'False'
    assert env['ENABLE_AUTOMATIONS'] == 'False' and env['ENABLE_CALENDAR'] == 'False'
    assert env['OPENAI_API_KEY'] == 'chat-test-only-key' and env['RAG_OPENAI_API_KEY'] == 'embedding-test-only-key'
    headers = json.loads(env['OPENAI_API_CONFIGS'])['0']['headers']
    assert headers['x-opencode-session'] == 'owui-{{USER_ID}}-{{CHAT_ID}}'
    assert 'manufacturing-agent-platform' in headers['User-Agent']


@pytest.mark.parametrize('role,field,value', [('chat', 'base_url', 'http://lan-db/'),
    ('chat', 'model', 'unapproved'), ('chat', 'provider', 'other'), ('embedding', 'api_key', 'bad\nkey')])
def test_provider_scope_fails_closed(injector, credentials, role, field, value):
    credentials['provider_settings'][role][field] = value
    with pytest.raises(ValueError):
        injector.build_environment(credentials, {})


def test_entrypoint_decrypts_and_only_exec_environment_gets_keys(injector, credentials, tmp_path, monkeypatch, capsys):
    key = Fernet.generate_key()
    (tmp_path / 'runtime.key').write_bytes(key)
    (tmp_path / 'credentials.enc').write_bytes(Fernet(key).encrypt(json.dumps(credentials).encode()))
    assert injector.read_credentials(tmp_path) == credentials
    monkeypatch.setattr(injector, 'read_credentials', lambda: credentials)
    captured = []
    def execve(file, args, env):
        captured.append((file, args, env))
        raise OSError('模拟exec退出')
    monkeypatch.setattr(injector.os, 'execve', execve)
    assert injector.main() == 1
    file, args, env = captured[0]
    assert file == '/bin/bash' and args == ['/bin/bash', '/app/backend/start.sh']
    assert 'test-only-key' not in json.dumps(args)
    assert env['OPENAI_API_KEY'] == 'chat-test-only-key'
    assert 'test-only-key' not in capsys.readouterr().err


def test_desktop_mount_normalization_accepts_only_known_d_forms(controller):
    assert controller._source_path('/run/desktop/mnt/host/d/Docker/a') == controller._source_path('D:\\Docker\\a')
    assert controller._source_path('/host_mnt/d/Docker/a') == controller._source_path('D:/Docker/a')
    assert controller._source_path('/somewhere/Docker/a') != controller._source_path('D:/Docker/a')
