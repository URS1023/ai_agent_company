"""Docker有限验收只用MockTransport/临时密文；不执行Docker、HTTP或数据库。"""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import time

from cryptography.fernet import Fernet
import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location('docker_api_verifier_test', ROOT / 'tools/verify_docker_api.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def native_test_support():
    """复用既有准确消息/令牌Mock，不复制或更改原生协议流程。"""
    spec = importlib.util.spec_from_file_location('native_api_verifier_support', Path(__file__).with_name('test_native_api_verifier.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class DockerRuntime:
    """替身只提供部署模块已约定的公开投影，没有subprocess或网络入口。"""

    def __init__(self, module):
        self.manifest = {'kind': 'manufacturing-openwebui-docker', 'id': 'docker-runtime-01',
                         'root': str(module.DEFAULT_ROOT), 'engine': module.ENGINE,
                         'container': {'id': 'a' * 64, 'name': 'owned-owui', 'created_at': '2026-10-20T01:00:00Z'},
                         'image': {'id': 'sha256:' + 'b' * 64, 'repo_digest': 'ghcr.io/open-webui/open-webui@sha256:' + 'c' * 64},
                         'frontend_expected': True, 'frontend_available': None}
        self.inspection = {'runtime_id': self.manifest['id'], 'root': self.manifest['root'], 'engine': module.ENGINE,
                           'container_id': self.manifest['container']['id'], 'container_name': 'owned-owui',
                           'image_id': self.manifest['image']['id'], 'image_digest': self.manifest['image']['repo_digest'],
                           'host': '127.0.0.1', 'port': 13080, 'internal_port': 8080,
                           'data_directory': str(module.DEFAULT_ROOT / 'data'), 'sqlite_file': str(module.DEFAULT_ROOT / 'data/webui.db'),
                           'ownership_verified': True, 'ready': True, 'labels': {'runtime_id': self.manifest['id']},
                           'mounts': [{'source': str(module.DEFAULT_ROOT / 'data'), 'destination': '/app/backend/data', 'read_only': False, 'type': 'bind'}],
                           'limits': {'memory_bytes': module.RSS_LIMIT, 'memory_swap_bytes': module.RSS_LIMIT,
                                      'nano_cpus': 1_000_000_000, 'pids_limit': 256, 'restart_policy': 'no',
                                      'healthcheck_disabled': True, 'privileged': False, 'cap_drop': ['ALL']},
                           'state': {'running': True, 'status': 'running', 'oom_killed': False, 'dead': False,
                                     'error_present': False, 'pid': 1234, 'exit_code': 0}}
        self.inspections, self.stops = 0, []
        self.inspection_timeouts = []
        self.inspect_error = None
        self.resource_checks = 0
        self.resource_snapshot = {'available_bytes': 4 * module.RSS_LIMIT, 'commit_free_bytes': 5 * module.RSS_LIMIT}
        self.resource_error = None

    def load(self, root):
        return deepcopy(self.manifest)

    def inspect_owned(self, root, *, timeout_seconds=3):
        self.inspections += 1
        self.inspection_timeouts.append(timeout_seconds)
        if self.inspect_error:
            raise RuntimeError('upstream-sensitive-env-must-not-leak')
        return deepcopy(self.inspection)

    def check_host_resources(self):
        self.resource_checks += 1
        if self.resource_error is not None:
            raise self.resource_error
        return deepcopy(self.resource_snapshot)

    def stop_owned(self, root, reason, *, expected_container_id=None, expected_runtime_id=None, timeout_seconds=5):
        self.stops.append({'reason': reason, 'expected_container_id': expected_container_id, 'expected_runtime_id': expected_runtime_id, 'timeout_seconds': timeout_seconds})
        assert expected_container_id == self.manifest['container']['id'] and expected_runtime_id == self.manifest['id']
        return {'verified': True, 'stopped': True, 'container_id': expected_container_id, 'running': False,
                'Env': ['MUST_NOT_BE_EXPOSED'], 'raw_error': 'MUST_NOT_BE_EXPOSED'}

    @staticmethod
    def ensure_inside(root, value):
        value.resolve().relative_to(root.resolve())
        return value

    @staticmethod
    def runtime_lock(root):
        return nullcontext()


@pytest.fixture
def runtime(module):
    return DockerRuntime(module)


@pytest.fixture
def ledger(module, runtime, tmp_path):
    key = Fernet.generate_key()
    (tmp_path / 'runtime.key').write_bytes(key)
    (tmp_path / 'credentials.enc').write_bytes(Fernet(key).encrypt(json.dumps({'keep': 'unmodified'}).encode()))
    return module.CredentialLedger(tmp_path, runtime)


def make_verifier(module, runtime, ledger, handler, run_id='mock-docker-run-01'):
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 60)

    def local_request(request):
        assert request.url.scheme == 'http' and request.url.host == '127.0.0.1' and request.url.port == 13080
        return handler(request)

    verifier = module.DockerVerifier(ledger.root, run_id, runtime, None, guard=guard, ledger=ledger, transport=httpx.MockTransport(local_request))
    guard.deadline = verifier.deadline
    verifier.verification_started_at = datetime.now(timezone.utc).isoformat()
    return verifier


@pytest.mark.parametrize('transport', ['saved_chat_events', 'http_sse'])
def test_docker_reuses_exact_bounded_workflow_and_keeps_ui_observation_separate(module, runtime, ledger, native_test_support, transport):
    api = native_test_support.NativeAPI(ledger, completion_transport=transport)
    verifier = make_verifier(module, runtime, ledger, api)
    result = module.acceptance_result(verifier, asyncio.run(verifier.execute()))
    assert result['status'] == 'succeeded' and result['http_attempts'] == result['http_budget'] == len(api.calls) == 14
    assert result['model_calls'] == len(api.model_payloads) == 1 and api.model_payloads[0]['params']['max_tokens'] == 64
    assert result['checks']['completion_transport'] == transport
    assert result['checks']['sse_transport_observed'] is (transport == 'http_sse')
    assert result['checks']['real_reply_persisted'] is True
    assert result['maximum_rss_bytes'] is None and result['memory_limit_bytes'] == 1024 ** 3
    assert result['rss_measurement'] == 'not_measured' and result['remaining_business_http'] == 6
    assert result['frontend_available'] is None and result['frontend_expected'] is True and result['native_ui_observed'] is False
    assert result['ui_eligible'] is True and result['full_native_acceptance'] is False
    assert result['origin'] == 'http://127.0.0.1:13080' and result['checked_at']
    assert datetime.fromisoformat(result['verification_started_at']).tzinfo is not None
    assert not runtime.stops and verifier.guard.stop_result is None
    record = ledger.read()[ledger.namespace][verifier.run_id]
    assert record['model_attempted'] is True and record['completion_request']['chat_id'] == api.chat_id
    encrypted, public = ledger.path.read_bytes(), json.dumps(result)
    assert record['accounts']['admin']['password'].encode() not in encrypted
    assert record['accounts']['admin']['email'] not in public and 'session-token' not in public and 'MUST_NOT_BE_EXPOSED' not in public


def test_sampling_uses_cached_inspect_and_does_not_invent_measured_rss(module, runtime):
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 60)
    assert guard.inspect() == {'id': runtime.manifest['id']}
    first = runtime.inspections
    for _ in range(100):
        guard.sample()
    assert runtime.inspections == first and runtime.resource_checks == first and guard.maximum_rss is None
    guard.last_inspected_at -= 2
    guard.sample()
    assert runtime.inspections == first + 1 and runtime.resource_checks == first + 1 and guard.maximum_rss is None


@pytest.mark.parametrize('root', ['C:/Docker/agent-platform/openwebui-20261020-01', 'D:/Docker/agent-platform/other', 'D:/Docker/agent-platform/openwebui-20261020-01/../other', '//server/share/openwebui-20261020-01'])
def test_only_current_authorized_d_drive_runtime_can_be_verified(module, runtime, root):
    with pytest.raises(module.VerificationError, match='docker_runtime_root_not_authorized'):
        module.DockerGuard(Path(root), runtime, time.monotonic() + 60)
    assert runtime.inspections == 0 and runtime.stops == []


@pytest.mark.parametrize('key,value', [('memory_bytes', 2 * 1024 ** 3), ('memory_swap_bytes', -1),
    ('nano_cpus', 2_000_000_000), ('pids_limit', 0), ('restart_policy', 'always'),
    ('healthcheck_disabled', False), ('privileged', True), ('cap_drop', [])])
def test_docker_hard_limits_are_required_and_failed_guard_stops_only_once(module, runtime, key, value):
    runtime.inspection['limits'][key] = value
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 60)
    with pytest.raises(module.VerificationError, match='docker_resource_limit_mismatch'):
        guard.inspect()
    guard.stop_owned('must-not-retry')
    assert len(runtime.stops) == 1 and guard.stop_result['verified'] is True
    assert 'Env' not in guard.stop_result and 'raw_error' not in guard.stop_result and guard.maximum_rss is None


@pytest.mark.parametrize('key,value,code', [('oom_killed', True, 'docker_oom_killed'),
    ('running', False, 'docker_container_not_running'), ('status', 'restarting', 'docker_container_not_running'),
    ('dead', True, 'docker_container_not_running'), ('error_present', True, 'docker_container_not_running')])
def test_oom_or_unhealthy_state_stops_owned_container_without_http(module, runtime, key, value, code):
    runtime.inspection['state'][key] = value
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 60)
    with pytest.raises(module.VerificationError, match=code):
        guard.inspect()
    assert len(runtime.stops) == 1 and guard.stop_result['verified'] is True


def test_replacement_container_is_never_stopped(module, runtime):
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 60)
    guard.inspect()
    runtime.manifest['container']['id'] = 'd' * 64
    runtime.inspection['container_id'] = 'd' * 64
    guard.last_inspected_at -= 2
    with pytest.raises(module.VerificationError, match='docker_container_changed'):
        guard.sample()
    guard.stop_owned('changed-container')
    guard.stop_owned('must-not-retry')
    assert runtime.stops == [] and guard.stop_result['verified'] is False
    assert guard.stop_result['reason'] == 'container_changed_no_container_touched'


@pytest.mark.parametrize('target,key,value', [('manifest', 'engine', 'tcp://remote.invalid:2375'),
    ('inspection', 'host', '0.0.0.0'), ('inspection', 'port', 13000),
    ('inspection', 'ownership_verified', False), ('inspection', 'data_directory', 'C:/old/data')])
def test_wrong_engine_port_or_storage_cannot_open_business_http(module, runtime, ledger, native_test_support, target, key, value):
    getattr(runtime, target)[key] = value
    api = native_test_support.NativeAPI(ledger)
    verifier = make_verifier(module, runtime, ledger, api)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'failed' and result['http_attempts'] == result['model_calls'] == 0 and api.calls == []
    assert runtime.stops == []


def test_completion_failure_keeps_unknown_and_never_replays_native_run(module, runtime, ledger, native_test_support):
    api = native_test_support.NativeAPI(ledger, model_error=True, completion_transport='http_sse')
    verifier = make_verifier(module, runtime, ledger, api)
    result = module.acceptance_result(verifier, asyncio.run(verifier.execute()))
    assert result['status'] == 'unknown' and result['http_attempts'] == 13 and result['model_calls'] == 1
    assert result['ui_eligible'] is False and result['stop_result']['verified'] is True and len(runtime.stops) == 1
    original = ledger.path.read_bytes()
    second = make_verifier(module, runtime, ledger, api)
    duplicate = module.acceptance_result(second, asyncio.run(second.execute()))
    assert duplicate['error_code'] == 'actual_run_id_already_attempted' and duplicate['previous_attempt_exists'] is True
    assert duplicate['http_attempts'] == 13 and duplicate['remaining_business_http'] == 7 and duplicate['ui_eligible'] is False
    assert len(api.calls) == 13 and len(api.model_payloads) == 1 and ledger.path.read_bytes() == original
    assert 'do-not-log' not in json.dumps(result)


def test_saved_native_unknown_is_not_rewritten_or_replayed(module, runtime, ledger):
    run_id = 'native-api-20261020-01'
    ledger.begin(run_id, 'earlier-native-runtime')
    ledger.update(run_id, lambda run: run.update(status='unknown', phase='model_attempted', model_attempted=True, http_attempts=13))
    before = ledger.path.read_bytes()
    calls = []
    verifier = make_verifier(module, runtime, ledger, lambda request: calls.append(request), run_id=run_id)
    result = module.acceptance_result(verifier, asyncio.run(verifier.execute()))
    assert result['error_code'] == 'actual_run_id_already_attempted' and result['http_attempts'] == 13
    assert calls == [] and ledger.path.read_bytes() == before and result['ui_eligible'] is False


def test_acceptance_result_is_atomic_and_cannot_reset_used_http_budget(module, runtime, tmp_path):
    result = {'status': 'succeeded', 'http_attempts': 14, 'remaining_business_http': 6}
    module.save_acceptance(tmp_path, runtime, result)
    saved = (tmp_path / 'acceptance.json').read_bytes()
    with pytest.raises(module.VerificationError, match='acceptance_result_already_exists'):
        module.save_acceptance(tmp_path, runtime, {'http_attempts': 0, 'remaining_business_http': 20})
    assert (tmp_path / 'acceptance.json').read_bytes() == saved
    assert json.loads(saved)['http_attempts'] == 14 and not list(tmp_path.glob('*.acceptance.pending'))


def test_inspection_error_does_not_expose_environment_or_retry(module, runtime, ledger, native_test_support):
    runtime.inspect_error = True
    api = native_test_support.NativeAPI(ledger)
    result = asyncio.run(make_verifier(module, runtime, ledger, api).execute())
    assert result['status'] == 'failed' and result['error_code'] == 'docker_ownership_inspection_failed'
    assert runtime.inspections == 1 and not api.calls and len(runtime.stops) == 1
    assert result['stop_result']['verified'] is True
    assert 'upstream-sensitive-env' not in json.dumps(result)


def test_docker_inspection_uses_verification_budget_and_cleanup_has_separate_limit(module, runtime):
    guard = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() + 2)
    guard.inspect()
    assert 0 < runtime.inspection_timeouts[-1] <= 2
    guard.stop_owned('bounded-cleanup')
    assert 0 < runtime.stops[-1]['timeout_seconds'] <= module.CLEANUP_SECONDS
    expired = module.DockerGuard(module.DEFAULT_ROOT, runtime, time.monotonic() - 1)
    with pytest.raises(module.VerificationError, match='docker_guard_deadline'):
        expired.inspect()
    assert runtime.inspections == 1


def test_expired_validation_still_stops_once_without_another_http(module, runtime, ledger, native_test_support):
    api = native_test_support.NativeAPI(ledger)
    verifier = None

    def deadline_after_first_request(request):
        response = api(request)
        # 模拟首个请求已派发后达到总验证截止；后续只允许独立清理，不能再派业务请求。
        expired = time.monotonic() - 1
        verifier.deadline = verifier.work_deadline = verifier.guard.deadline = expired
        return response

    verifier = make_verifier(module, runtime, ledger, deadline_after_first_request)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'unknown' and result['http_attempts'] == len(api.calls) == 1
    assert result['model_calls'] == 0 and not api.model_payloads
    assert result['stop_result']['verified'] is True and len(runtime.stops) == 1
    assert 0 < runtime.stops[0]['timeout_seconds'] <= module.CLEANUP_SECONDS
    verifier.guard.stop_owned('must-not-retry-after-deadline')
    assert len(runtime.stops) == 1
    with pytest.raises(module.VerificationError, match='docker_guard_deadline'):
        verifier.guard.sample()
    assert len(api.calls) == 1


def test_oom_observed_at_deadline_end_has_independent_cleanup_and_no_more_http(module, runtime, ledger, native_test_support, monkeypatch):
    api = native_test_support.NativeAPI(ledger)
    verifier = None
    original_inspect = runtime.inspect_owned

    def deadline_during_oom_inspection(root, *, timeout_seconds):
        inspected = original_inspect(root, timeout_seconds=timeout_seconds)
        if inspected['state']['oom_killed']:
            # inspect在预算内开始，但OOM结果返回时总验证期限已过。
            expired = time.monotonic() - 1
            verifier.deadline = verifier.work_deadline = verifier.guard.deadline = expired
        return inspected

    def oom_after_first_request(request):
        response = api(request)
        runtime.inspection['state']['oom_killed'] = True
        verifier.guard.last_inspected_at -= 2
        return response

    monkeypatch.setattr(runtime, 'inspect_owned', deadline_during_oom_inspection)
    verifier = make_verifier(module, runtime, ledger, oom_after_first_request)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'unknown' and result['http_attempts'] == len(api.calls) == 1
    assert result['model_calls'] == 0 and not api.model_payloads
    assert result['stop_result']['verified'] is True and len(runtime.stops) == 1
    assert runtime.stops[0]['reason'] == 'docker_oom_killed'
    assert 0 < runtime.stops[0]['timeout_seconds'] <= module.CLEANUP_SECONDS
    verifier.guard.stop_owned('must-not-repeat-oom-cleanup')
    assert len(runtime.stops) == 1 and len(api.calls) == 1


def test_ui_cannot_receive_a_fresh_budget_after_the_shared_deadline(module, runtime, ledger, native_test_support):
    verifier = make_verifier(module, runtime, ledger, native_test_support.NativeAPI(ledger))
    result = asyncio.run(verifier.execute())
    verifier.verification_started_at = '2000-01-01T00:00:00+00:00'
    public = module.acceptance_result(verifier, result)
    assert public['status'] == 'succeeded' and public['http_attempts'] == 14 and public['ui_eligible'] is False


def test_running_but_unready_container_cannot_start_business_requests(module, runtime, ledger, native_test_support):
    runtime.inspection['ready'] = False
    api = native_test_support.NativeAPI(ledger)
    result = asyncio.run(make_verifier(module, runtime, ledger, api).execute())
    assert result['status'] == 'failed' and result['error_code'] == 'docker_container_not_running'
    assert result['http_attempts'] == 0 and not api.calls and len(runtime.stops) == 1


def test_cli_record_failure_keeps_real_attempts_and_stops_owned_container(module, runtime, ledger, native_test_support, monkeypatch, capsys):
    api = native_test_support.NativeAPI(ledger)
    original_verifier = module.DockerVerifier

    def verifier_with_mock(*args, **kwargs):
        return original_verifier(*args, **kwargs, transport=httpx.MockTransport(api))

    def failed_save(*args):
        raise PermissionError('private-filesystem-detail')

    monkeypatch.setattr(module, 'docker_module', lambda: runtime)
    monkeypatch.setattr(module, 'CredentialLedger', lambda *args: ledger)
    monkeypatch.setattr(module, 'DockerVerifier', verifier_with_mock)
    monkeypatch.setattr(module, 'save_acceptance', failed_save)
    # 主函数的存在性检查也映射本轮临时目录，不读取真实D盘runtime文件。
    monkeypatch.setattr(runtime, 'ensure_inside', lambda root, value: ledger.root / value.name)
    monkeypatch.setattr(module.sys, 'argv', ['verify_docker_api.py', '--root', str(module.DEFAULT_ROOT), '--run-id', module.RUN_ID])
    with pytest.raises(SystemExit) as exit_result:
        module.main()
    result = json.loads(capsys.readouterr().out)
    assert exit_result.value.code == 1 and result['status'] == 'unknown'
    assert result['http_attempts'] == 14 and result['model_calls'] == 1 and result['remaining_business_http'] == 6
    assert result['ui_eligible'] is False and result['stop_result']['verified'] is True and len(runtime.stops) == 1
    assert result['error_code'] == 'docker_acceptance_record_failed' and 'private-filesystem-detail' not in json.dumps(result)


@pytest.mark.parametrize('failure', ['os_error', 'runtime_error', 'missing_snapshot', 'missing_commit', 'low_physical', 'low_commit'])
def test_host_resource_failure_stops_owned_candidate_before_any_business_request(module, runtime, ledger, native_test_support, failure):
    if failure == 'os_error':
        runtime.resource_error = OSError('private-host-resource-detail')
    elif failure == 'runtime_error':
        runtime.resource_error = RuntimeError('private-host-resource-detail')
    elif failure == 'missing_snapshot':
        runtime.resource_snapshot = None
    elif failure == 'missing_commit':
        runtime.resource_snapshot.pop('commit_free_bytes')
    elif failure == 'low_physical':
        runtime.resource_snapshot['available_bytes'] = 2 * module.RSS_LIMIT - 1
    else:
        runtime.resource_snapshot['commit_free_bytes'] = 3 * module.RSS_LIMIT - 1
    api = native_test_support.NativeAPI(ledger)
    verifier = make_verifier(module, runtime, ledger, api)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'failed' and result['error_code'] == 'docker_host_resource_check_failed'
    assert result['http_attempts'] == result['model_calls'] == 0 and not api.calls
    assert runtime.resource_checks == 1 and runtime.inspections == 0
    assert result['stop_result']['verified'] is True and len(runtime.stops) == 1
    assert verifier.guard.initial is None
    with pytest.raises(module.VerificationError, match='docker_host_resource_check_failed'):
        verifier.guard.inspect()
    assert runtime.resource_checks == 1 and len(runtime.stops) == 1
    assert 'private-host-resource-detail' not in json.dumps(result)


def test_host_resource_detection_failure_after_first_request_stops_without_followup(module, runtime, ledger, native_test_support):
    api = native_test_support.NativeAPI(ledger)
    verifier = None

    def host_failure_after_first_request(request):
        response = api(request)
        runtime.resource_error = OSError('private-resource-reader-error')
        verifier.guard.last_inspected_at -= 2
        return response

    verifier = make_verifier(module, runtime, ledger, host_failure_after_first_request)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'docker_host_resource_check_failed'
    assert result['http_attempts'] == len(api.calls) == 1 and result['model_calls'] == 0 and not api.model_payloads
    assert runtime.resource_checks == 2 and runtime.inspections == 1
    assert len(runtime.stops) == 1 and result['stop_result']['verified'] is True
    verifier.guard.stop_owned('must-not-repeat-resource-cleanup')
    assert len(runtime.stops) == 1 and len(api.calls) == 1
    assert 'private-resource-reader-error' not in json.dumps(result)
