"""Dify验收入口的密文、不可重放和真实控制器契约测试；仅Mock HTTP/临时文件。"""

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'tools'))
spec = importlib.util.spec_from_file_location('verify_dify_native', REPO / 'tools/verify_dify_native.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
def ledger(tmp_path):
    (tmp_path / '.local').mkdir()
    (tmp_path / '.local/desktop-access.key').write_bytes(Fernet.generate_key())
    value = verifier.DifyLedger(tmp_path, SimpleNamespace(runtime_lock=lambda _: nullcontext()))
    value.prepare('dify-mock-01', 'a' * 32)
    return value


def test_account_is_persisted_encrypted_before_any_setup_and_run_cannot_replay(ledger):
    account = ledger.record('dify-mock-01')['account']
    encrypted = ledger.path.read_bytes()
    assert account['password'].encode() not in encrypted and account['email'].encode() not in encrypted
    record = ledger.claim('dify-mock-01')
    assert record['status'] == 'running' and record['model_attempts'] == 0
    with pytest.raises(verifier.VerificationError, match='already_attempted'):
        ledger.claim('dify-mock-01')
    with pytest.raises(verifier.VerificationError, match='already_exists'):
        ledger.prepare('dify-mock-01', 'a' * 32)


def test_workflow_checkpoint_is_atomic_bound_to_app_and_one_shot(ledger):
    ledger.claim('dify-mock-01')
    app_id = '00000000-0000-0000-0000-000000000001'
    ledger.update('dify-mock-01', lambda r: r['flows'].update(python3={'app_id': app_id}))
    with pytest.raises(verifier.VerificationError, match='already_attempted_or_changed'):
        ledger.checkpoint('dify-mock-01', 'python3', 'another-app', verifier.digest({'inputs': {}, 'files': []}))
    ledger.checkpoint('dify-mock-01', 'python3', app_id, verifier.digest({'inputs': {}, 'files': []}))
    with pytest.raises(verifier.VerificationError, match='already_attempted_or_changed'):
        ledger.checkpoint('dify-mock-01', 'python3', app_id, verifier.digest({'inputs': {}, 'files': []}))
    assert ledger.record('dify-mock-01')['flows']['python3']['dispatch_attempted'] is True


@pytest.mark.parametrize('language', ['python3', 'javascript'])
def test_fixed_workflows_have_no_model_tool_file_or_external_input(language):
    plan = verifier.workflow_plan(language, 'dify-mock-01')
    assert [node['data']['type'] for node in plan['graph']['nodes']] == ['start', 'code', 'end']
    assert plan['graph']['nodes'][0]['data']['variables'] == []
    assert plan['graph']['nodes'][1]['data']['variables'] == []
    assert plan['features']['file_upload']['enabled'] is False
    assert plan['marker'] in plan['code'] and plan['editor_comment'] in plan['edited_code']
    if language == 'python3':
        namespace = {}
        exec(compile(plan['code'], '<fixed offline Python>', 'exec'), namespace)
        assert namespace['main']() == {'result': plan['marker'], 'value': 42}


def test_console_uses_real_cookie_csrf_and_current_strict_draft_fields(ledger):
    ledger.claim('dify-mock-01')
    requests = []
    app_ids = iter(['00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'])
    def transport(request):
        requests.append(request)
        assert request.url.host == '127.0.0.1' and request.url.port == 13001
        assert not request.headers.get('Authorization')
        path = request.url.path
        if path.endswith('/init'): return httpx.Response(200, json={'status': 'finished'})
        if path.endswith('/setup') and request.method == 'GET': return httpx.Response(200, json={'step': 'not_started'})
        if path.endswith('/setup'):
            assert ledger.record('dify-mock-01')['setup_attempted']
            body = json.loads(request.content)
            assert body['password'] == ledger.record('dify-mock-01')['account']['password']
            return httpx.Response(201, json={'result': 'success'})
        if path.endswith('/login'):
            assert json.loads(request.content)['password'] != ledger.record('dify-mock-01')['account']['password']
            return httpx.Response(200, json={'result': 'success'}, headers=[('Set-Cookie', 'access_token=access; Path=/; HttpOnly'),
                ('Set-Cookie', 'csrf_token=csrf; Path=/')])
        assert request.headers['X-CSRF-Token'] == 'csrf' if request.method == 'POST' else True
        assert 'access_token=access' in request.headers['Cookie']
        if path.endswith('/apps'): return httpx.Response(201, json={'id': next(app_ids)})
        if path.endswith('/workflows/draft') and request.method == 'GET': return httpx.Response(404, json={'code': 'draft_workflow_not_exist'})
        if path.endswith('/workflows/draft'):
            body = json.loads(request.content)
            assert 'environment_variables' not in body and body['_is_collaborative'] is False
            assert set(body) == {'graph', 'features', 'hash', 'force', '_is_collaborative', 'conversation_variables'}
            return httpx.Response(200, json={'result': 'success', 'hash': 'new-draft-hash'})
        pytest.fail('非预期HTTP端点')
    session = verifier.ConsoleSession(ledger, 'dify-mock-01', time.monotonic() + 30, httpx.MockTransport(transport))
    flows = verifier.prepare_console(session, ledger, 'dify-mock-01')
    assert len(flows) == 2 and session.attempts == 10
    assert not any(request.url.path.endswith('/run') for request in requests)
    assert all(not flow.get('dispatch_attempted') for flow in ledger.record('dify-mock-01')['flows'].values())
    assert 'access' not in ledger.path.read_text(errors='ignore')
    session.client.close()


def test_preexisting_setup_is_refused_without_registration_or_fallback(ledger):
    ledger.claim('dify-mock-01')
    seen = []
    def transport(request):
        seen.append(request.method)
        return httpx.Response(200, json={'status': 'finished'} if request.url.path.endswith('/init') else {'step': 'finished'})
    session = verifier.ConsoleSession(ledger, 'dify-mock-01', time.monotonic() + 30, httpx.MockTransport(transport))
    with pytest.raises(verifier.VerificationError, match='not_fresh_setup'):
        verifier.prepare_console(session, ledger, 'dify-mock-01')
    assert seen == ['GET', 'GET']
    session.client.close()


def test_deadline_and_scope_reject_requests_before_transport(ledger):
    session = verifier.ConsoleSession(ledger, 'dify-mock-01', time.monotonic() - 1,
        httpx.MockTransport(lambda _: pytest.fail('截止后不得发送HTTP')))
    with pytest.raises(verifier.VerificationError, match='unexpected_console_endpoint'):
        session.request('GET', 'http://old.invalid')
    with pytest.raises(verifier.VerificationError, match='total_deadline'):
        session.request('GET', '/console/api/setup')
    session.client.close()


def test_ownership_probe_has_only_fixed_local_process_and_config_checks():
    script = verifier.ownership_script('a' * 32, {'api/controllers/console/setup.py': 'b' * 64})
    compile(script, '<ownership read-only>', 'exec')
    assert 'psycopg2' not in script and 'httpx' not in script and 'requests' not in script
    assert 'data/postgres' in script and "env.get('DATABASE_URL')" in script
    assert 'candidate.environ()' in script and 'print(json.dumps' in script


def test_verifier_failure_preserves_account_and_forbids_automatic_retry(ledger, monkeypatch):
    monkeypatch.setattr(verifier, 'check_ownership', lambda *a: (_ for _ in ()).throw(verifier.VerificationError('owned_listener_missing')))
    account = ledger.record('dify-mock-01')['account']
    with pytest.raises(verifier.VerificationError, match='owned_listener_missing'):
        verifier.verify(ledger, 'dify-mock-01')
    assert ledger.record('dify-mock-01')['account'] == account
    assert ledger.record('dify-mock-01')['status'] == 'failed'
    with pytest.raises(verifier.VerificationError, match='already_attempted'):
        verifier.verify(ledger, 'dify-mock-01')


def test_http_total_deadline_cancels_single_attempt_without_retry(ledger):
    attempts = []
    async def transport(request):
        attempts.append(request.url.path)
        await verifier.asyncio.Event().wait()
    session = verifier.ConsoleSession(ledger, 'dify-mock-01', time.monotonic() + 0.04,
        httpx.MockTransport(transport))
    with pytest.raises(TimeoutError):
        session.request('GET', '/console/api/setup')
    assert attempts == ['/console/api/setup'] and session.attempts == 1
    session.client.close()


def test_browser_cleanup_never_signals_reused_or_foreign_pid(monkeypatch):
    candidate = SimpleNamespace(create_time=lambda: 99, exe=lambda: 'foreign.exe',
        kill=lambda: pytest.fail('归属不符不得停止'))
    monkeypatch.setattr(verifier.psutil, 'Process', lambda _: candidate)
    with pytest.raises(verifier.VerificationError, match='cleanup_identity_changed'):
        verifier.stop_browser_tree(SimpleNamespace(pid=123), 1, 'owned-node.exe')


def prepared_browser_failure(ledger):
    """构造已完成原十次API准备、尚未创建浏览器进程的真实路径错误状态。"""
    ledger.claim('dify-mock-01')
    proof = {'verified': True, 'runtime_id': 'a' * 32, 'boot_id': 'boot-one',
        'origin': verifier.ORIGIN, 'postgres_data': '/opt/ai-agent-company/data/postgres',
        'processes': [{'role': 'api', 'pid': 100, 'launch_id': 'original'},
            {'role': 'code', 'pid': 101, 'launch_id': 'original'}]}
    def failed(record):
        record.update(status='failed', error_code='AttributeError', setup_attempted=True, setup_succeeded=True,
            http_attempts=10, ownership=proof, cookies=[
                {'name': 'access_token', 'value': 'access-original', 'domain': '127.0.0.1', 'path': '/'},
                {'name': 'csrf_token', 'value': 'csrf-original', 'domain': '127.0.0.1', 'path': '/'}])
        for language, suffix in [('python3', '1'), ('javascript', '2')]:
            plan = verifier.workflow_plan(language, 'dify-mock-01')
            record['flows'][language] = {'app_id': '00000000-0000-0000-0000-00000000000' + suffix,
                'create_attempted': True, 'draft_saved': True, 'graph_sha256': verifier.digest(plan['graph'])}
        record['phase'] = 'POST:/console/api/apps/' + record['flows']['javascript']['app_id'] + '/workflows/draft'
    ledger.update('dify-mock-01', failed)
    (ledger.root / 'dify-mock-01').mkdir()
    return proof


@pytest.mark.parametrize('fault', ['identity', 'runtime', 'error', 'setup', 'count', 'draft', 'dispatch', 'browser', 'history', 'graph', 'mode_phase'])
def test_browser_recovery_refuses_unreviewed_or_partially_dispatched_state_without_http(ledger, monkeypatch, fault):
    prepared_browser_failure(ledger)
    reviewed, runtime_id = 'dify-mock-01', 'a' * 32
    if fault == 'identity': reviewed = 'another-failed-run'
    if fault == 'runtime': runtime_id = 'b' * 32
    def corrupt(record):
        if fault == 'error': record['error_code'] = 'TimeoutExpired'
        if fault == 'setup': record['setup_succeeded'] = False
        if fault == 'count': record['http_attempts'] = 11
        if fault == 'draft': record['flows']['javascript']['draft_saved'] = False
        if fault == 'dispatch': record['flows']['python3']['dispatch_attempted'] = True
        if fault == 'browser': record['browser_process'] = {}
        if fault == 'history': record['history'] = [{'status': 'failed'}]
        if fault == 'graph': record['flows']['python3']['graph_sha256'] = 'changed'
        if fault == 'mode_phase': record['phase'] = 'POST:/console/api/login'
    ledger.update('dify-mock-01', corrupt)
    monkeypatch.setattr(verifier, 'check_ownership', lambda *a: pytest.fail('门槛拒绝后不得探测或HTTP'))
    with pytest.raises(verifier.VerificationError, match='browser_recovery_'):
        verifier.recover_browser(ledger, 'dify-mock-01', reviewed, runtime_id)
    assert ledger.record('dify-mock-01')['status'] == 'failed'


@pytest.mark.parametrize('remote_change', ['none', 'graph', 'mode'])
def test_browser_recovery_only_reads_two_drafts_and_preserves_original_failure_and_http_count(ledger, monkeypatch, remote_change):
    proof = prepared_browser_failure(ledger)
    # 本轮明确受审的code定点重启可换PID，API/PG和其余运行证据不能更换。
    current_proof = json.loads(json.dumps(proof))
    current_proof['processes'][1].update(pid=102, launch_id='reviewed-code-only')
    monkeypatch.setattr(verifier, 'check_ownership', lambda *a: current_proof)
    monkeypatch.setattr(verifier, 'prepare_console', lambda *a: pytest.fail('续作不得重setup/login/创建应用'))
    calls = []
    def transport(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.path.endswith('/export')
        assert dict(request.url.params) == {'format': 'yaml', 'include_secret': 'false'}
        assert request.headers['X-CSRF-Token'] == 'csrf-original'
        assert 'access_token=access-original' in request.headers['Cookie']
        language = 'python3' if request.url.path.split('/')[-2].endswith('1') else 'javascript'
        plan = verifier.workflow_plan(language, 'dify-mock-01')
        if remote_change == 'graph': plan['graph']['nodes'][1]['data']['code'] = 'changed code'
        exported = {'kind': 'app', 'app': {'mode': 'advanced-chat' if remote_change == 'mode' else 'workflow'}, 'dependencies': [],
            'workflow': {'graph': plan['graph'], 'features': plan['features'],
                'environment_variables': [], 'conversation_variables': []}}
        return httpx.Response(200, json={'data': verifier.yaml.safe_dump(exported)})
    original_session = verifier.ConsoleSession
    monkeypatch.setattr(verifier, 'ConsoleSession', lambda *a, **kw:
        original_session(*a, transport=httpx.MockTransport(transport), **kw))
    browser_calls = []
    def browser(value, run_id, flows, attempts, deadline):
        browser_calls.append(attempts)
        assert len(flows) == 2 and attempts == 12
        for flow in flows:
            value.checkpoint(run_id, flow['language'], flow['app_id'], verifier.digest({'inputs': {}, 'files': []}))
        return {'business_http': 20, 'static_requests': 15, 'screenshots': ['four-reviewed-shots']}
    monkeypatch.setattr(verifier, 'run_browser', browser)
    if remote_change != 'none':
        with pytest.raises(verifier.VerificationError, match='remote_graph_changed|app_mode_changed'):
            verifier.recover_browser(ledger, 'dify-mock-01', 'dify-mock-01', 'a' * 32)
        assert not browser_calls and len(calls) == 1
    else:
        assert verifier.recover_browser(ledger, 'dify-mock-01', 'dify-mock-01', 'a' * 32)['passed']
        assert len(calls) == 2 and browser_calls == [12]
    record = ledger.record('dify-mock-01')
    assert record['history'][0]['status'] == 'failed' and record['history'][0]['error_code'] == 'AttributeError'
    assert record['history'][0]['http_attempts'] == 10 and record['history'][0]['account'] == record['account']
    assert record['browser_recovery']['reviewed_code_restart'] is True
    with pytest.raises(verifier.VerificationError):
        verifier.recover_browser(ledger, 'dify-mock-01', 'dify-mock-01', 'a' * 32)
    assert len(calls) == (1 if remote_change != 'none' else 2)


def test_native_browser_uses_real_binary_contract_and_never_overwrites_original_recovery_directory(ledger, monkeypatch):
    prepared_browser_failure(ledger)
    record = ledger.claim_browser_recovery('dify-mock-01', 'dify-mock-01', 'a' * 32)
    expected_node = Path('owned-node24.exe')
    native = SimpleNamespace(LOCAL_BINARIES={'node24': expected_node}, base_environment=lambda: {})
    monkeypatch.setattr(verifier, 'modules', lambda: (native, None))
    monkeypatch.setattr(verifier.psutil, 'Process', lambda _: SimpleNamespace(create_time=lambda: 1, exe=lambda: str(expected_node)))
    def spawn(args, **kwargs):
        assert args[0] == str(expected_node)
        payload = json.loads(kwargs['env']['DIFY_VERIFY_INPUT'])
        output = Path(payload['output'])
        assert output != ledger.root / 'dify-mock-01'
        assert record['browser_recovery']['id'] in output.name
        (output / 'receipt.json').write_text(json.dumps({'passed': True, 'model_requests': 0,
            'workflow_requests': 2, 'business_http': 12}))
        return SimpleNamespace(pid=123, communicate=lambda **kw: None, returncode=0)
    monkeypatch.setattr(verifier.subprocess, 'Popen', spawn)
    flows = [{**verifier.workflow_plan(language, 'dify-mock-01'), 'app_id': item['app_id']}
        for language, item in record['flows'].items()]
    result = verifier.run_browser(ledger, 'dify-mock-01', flows, 12, time.monotonic() + 30)
    assert result['passed']
    assert not any((ledger.root / 'dify-mock-01').iterdir())
    with pytest.raises(FileExistsError):
        verifier.run_browser(ledger, 'dify-mock-01', [], 12, time.monotonic() + 30)


def test_browser_recovery_refuses_other_service_restart_before_http(ledger, monkeypatch):
    proof = prepared_browser_failure(ledger)
    proof['processes'][0]['pid'] = 888
    monkeypatch.setattr(verifier, 'check_ownership', lambda *a: proof)
    monkeypatch.setattr(verifier, 'ConsoleSession', lambda *a, **kw: pytest.fail('API归属改变后不得发HTTP'))
    with pytest.raises(verifier.VerificationError, match='runtime_launch_changed'):
        verifier.recover_browser(ledger, 'dify-mock-01', 'dify-mock-01', 'a' * 32)
