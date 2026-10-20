"""原生API验收工具的纯Mock边界；不启动服务、不执行数据库或外部请求。"""

import asyncio
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

from cryptography.fernet import Fernet
import httpx
import psutil
import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location('native_api_verifier', ROOT / 'tools/verify_native_api.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class LedgerRuntime:
    """仅提供本轮临时文件边界，锁在单线程用例中无副作用。"""

    @staticmethod
    def ensure_inside(root, value):
        value.resolve().relative_to(root.resolve())
        return value

    @staticmethod
    def runtime_lock(root):
        return nullcontext()


@pytest.fixture
def ledger(module, tmp_path):
    key = Fernet.generate_key()
    (tmp_path / 'runtime.key').write_bytes(key)
    (tmp_path / 'credentials.enc').write_bytes(Fernet(key).encrypt(json.dumps({'existing': 'preserve-original'}).encode()))
    return module.CredentialLedger(tmp_path, LedgerRuntime())


class FakeGuard:
    """资源边界的替身不读取真实进程或监听端口。"""

    def __init__(self):
        self.initial = None
        self.maximum_rss = 32 * 1024 * 1024
        self.stop_result = None
        self.samples = 0

    def inspect(self):
        self.initial = {'launch_id': 'owned-launch'}
        return {'id': 'new-local-runtime'}

    def sample(self):
        self.samples += 1

    def stop_owned(self, reason):
        self.stop_result = {'verified': True, 'alive_pids': [], 'reason': reason}


class NativeAPI:
    """按真实API路径响应，显式区分管理员令牌与普通用户令牌。"""

    chat_id = '56b95e67-691c-4d46-94dd-378253308b27'

    def __init__(self, ledger, *, model_error=False, redirect_health=False, completion_transport='saved_chat_events'):
        self.ledger = ledger
        self.calls = []
        self.model_payloads = []
        self.model_error = model_error
        self.redirect_health = redirect_health
        self.completion_transport = completion_transport

    def __call__(self, request):
        method, path = request.method, request.url.path
        payload = json.loads(request.content) if request.content else None
        token = request.headers.get('authorization')
        self.calls.append((method, path, token))
        if path == '/health':
            if self.redirect_health:
                return httpx.Response(307, headers={'location': 'https://never-follow.invalid/'})
            return httpx.Response(200, json={'status': True})
        if path == '/api/v1/auths/signup':
            run = next(iter(self.ledger.read()[self.ledger.namespace].values()))
            assert run['phase'] == 'admin_signup_attempted'
            assert payload['password'] == run['accounts']['admin']['password']
            return httpx.Response(200, json={'id': 'admin-id', 'role': 'admin', 'token': 'issued-admin-token'})
        if path == '/api/v1/auths/signin':
            role = 'admin' if 'native-admin-' in payload['email'] else 'user'
            return httpx.Response(200, json={'id': role + '-id', 'role': role, 'token': role + '-session-token'})
        if path == '/api/v1/auths/':
            assert token == 'Bearer admin-session-token'
            return httpx.Response(200, json={'id': 'admin-id', 'role': 'admin'})
        if path == '/api/models':
            assert token == 'Bearer admin-session-token'
            return httpx.Response(200, json={'data': [{'id': 'deepseek-v4.1-flash'}]})
        if path == '/api/v1/chats/new':
            assert token == 'Bearer admin-session-token'
            assert not payload.get('access_grants')
            return httpx.Response(200, json={'id': self.chat_id})
        if path == '/api/v1/auths/add':
            assert token == 'Bearer admin-session-token'
            assert payload['role'] == 'user'
            return httpx.Response(200, json={'id': 'user-id', 'role': 'user', 'token': 'issued-user-token'})
        if path == '/api/v1/chats/list':
            assert token == 'Bearer user-session-token'
            return httpx.Response(200, json=[])
        if path == '/api/v1/chats/' + self.chat_id:
            if token == 'Bearer user-session-token':
                return httpx.Response(401, json={'detail': 'denied'})
            assert token == 'Bearer admin-session-token' and method == 'GET'
            messages = {}
            if self.model_payloads:
                turn = self.model_payloads[0]
                messages[turn['user_message']['id']] = {**turn['user_message'], 'user_id': 'admin-id'}
                messages[turn['id']] = {'id': turn['id'], 'parentId': turn['user_message']['id'],
                    'user_id': 'admin-id', 'model': turn['model'], 'role': 'assistant', 'done': True, 'content': 'OK'}
                if self.model_error:
                    messages[turn['id']]['error'] = {'content': 'do-not-log-upstream-secret'}
            return httpx.Response(200, json={'id': self.chat_id, 'user_id': 'admin-id', 'chat': {'history': {'messages': messages}}})
        if path == '/api/chat/completions':
            assert token == 'Bearer admin-session-token'
            self.model_payloads.append(payload)
            run = next(iter(self.ledger.read()[self.ledger.namespace].values()))
            assert run['model_attempted'] is True and run['phase'] == 'model_attempted'
            reference = run['completion_request']
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            assert reference['chat_id'] == self.chat_id and reference['message_id'] == payload['id']
            assert reference['user_message_id'] == payload['user_message']['id'] and reference['model'] == payload['model']
            assert reference['request_sha256'] == hashlib.sha256(canonical).hexdigest()
            if self.completion_transport == 'saved_chat_events':
                # 锁定版原生事件分支消费上游模型流并保存消息，HTTP回包本身不含回复。
                return httpx.Response(200, headers={'content-type': 'application/json'}, content=b'null')
            frame = {'error': {'detail': 'do-not-log-upstream-secret'}} if self.model_error else {
                'choices': [{'delta': {'content': 'OK'}, 'finish_reason': 'stop'}]}
            content = 'data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n'
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=content.encode())
        raise AssertionError('unexpected mock endpoint')


def make_verifier(module, ledger, api, *, run_id='mock-native-run-01', guard=None):
    return module.Verifier(ledger.root, run_id, None, None, ledger=ledger,
        guard=guard or FakeGuard(), transport=httpx.MockTransport(api))


def test_ledger_encrypts_accounts_and_tokens_and_refuses_replay(module, ledger):
    record = ledger.begin('encryption-run-01', 'new-local-runtime')
    password = record['accounts']['admin']['password']
    ledger.update('encryption-run-01', lambda run: run['accounts']['admin'].update(token='private-token-value'))
    encrypted = ledger.path.read_bytes()
    assert password.encode() not in encrypted and b'private-token-value' not in encrypted
    assert b'preserve-original' not in encrypted
    assert ledger.read()['existing'] == 'preserve-original'
    assert ledger.read()[ledger.namespace]['encryption-run-01']['accounts']['admin']['token'] == 'private-token-value'
    with pytest.raises(module.VerificationError, match='actual_run_id_already_attempted'):
        ledger.begin('encryption-run-01', 'new-local-runtime')
    assert ledger.path.read_bytes() == encrypted


def test_ledger_failed_atomic_replace_preserves_original(module, ledger, monkeypatch):
    original = ledger.path.read_bytes()

    def failed_replace(source, target):
        raise PermissionError('not exposed')

    monkeypatch.setattr(Path, 'replace', failed_replace)
    with pytest.raises(PermissionError):
        ledger.begin('replace-failure-01', 'new-local-runtime')
    assert ledger.path.read_bytes() == original
    assert not list(ledger.root.glob('*.enc.pending'))


def test_fixed_workflow_has_two_real_tokens_one_model_attempt_and_no_replay(module, ledger):
    api = NativeAPI(ledger)
    verifier = make_verifier(module, ledger, api)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'succeeded' and result['http_attempts'] == 14
    assert len(api.calls) == 14 and len(api.model_payloads) == result['model_calls'] == 1
    assert result['checks']['real_reply_persisted'] and verifier.guard.stop_result is None
    assert result['checks']['completion_transport'] == 'saved_chat_events'
    assert result['checks']['sse_transport_observed'] is False and result['checks']['sse_completed'] is False
    assert result['frontend_available'] is False and result['full_native_acceptance'] is False
    payload = api.model_payloads[0]
    assert payload['params']['max_tokens'] == 64 and payload['background_tasks'] == {}
    assert not any(payload['features'].values()) and payload['tool_ids'] == [] and payload['files'] == []
    assert 'session_id' not in payload and 'parent_id' not in payload
    record = ledger.read()[ledger.namespace][verifier.run_id]
    assert record['accounts']['admin']['token'] == 'admin-session-token'
    assert record['accounts']['user']['token'] == 'user-session-token'
    assert record['phase'] == 'completed' and record['model_attempted'] is True
    reference = record['completion_request']
    assert reference['chat_id'] == payload['chat_id'] and reference['message_id'] == payload['id']
    assert reference['user_message_id'] == payload['user_message']['id']
    assert payload['id'].encode() not in ledger.path.read_bytes()
    output = json.dumps(result)
    assert 'session-token' not in output and record['accounts']['admin']['email'] not in output
    duplicate = make_verifier(module, ledger, api)
    duplicate_result = asyncio.run(duplicate.execute())
    assert duplicate_result['error_code'] == 'actual_run_id_already_attempted'
    assert len(api.calls) == 14 and duplicate.guard.stop_result is None
    with pytest.raises(module.VerificationError, match='model_attempt_already_recorded'):
        asyncio.run(verifier.completion(None, api.chat_id))
    assert len(api.calls) == 14


def test_http_sse_transport_remains_strict_and_distinct_from_native_events(module, ledger):
    api = NativeAPI(ledger, completion_transport='http_sse')
    result = asyncio.run(make_verifier(module, ledger, api).execute())
    assert result['status'] == 'succeeded' and result['http_attempts'] == 14 and result['model_calls'] == 1
    assert result['checks']['completion_transport'] == 'http_sse'
    assert result['checks']['sse_transport_observed'] is True and result['checks']['sse_completed'] is True
    assert result['checks']['real_reply_persisted'] is True


@pytest.mark.parametrize('body', [
    b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
    b'data: [DONE]\n\n',
    b'data: {"choices":[{"delta":{"reasoning_content":"hidden"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
])
def test_http_sse_requires_both_visible_text_and_a_completion_signal(module, ledger, body):
    api = NativeAPI(ledger, completion_transport='http_sse')

    def handler(request):
        response = api(request)
        if request.url.path == '/api/chat/completions':
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)
        return response

    result = asyncio.run(make_verifier(module, ledger, handler).execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'sse_no_completed_text_reply'
    assert result['model_calls'] == 1 and result['http_attempts'] == 13
    assert result['checks']['sse_transport_observed'] is True and not result['checks'].get('sse_completed')


@pytest.mark.parametrize('body', [b'', b'{}', b'[]', b'false', b'"null"',
    b'{"status":true}', b'{"status":true,"task_ids":["task"],"chat_id":"other-chat"}', b'nullnull'])
def test_native_event_ack_only_accepts_exact_json_null_without_retry(module, ledger, body):
    api = NativeAPI(ledger)

    def handler(request):
        response = api(request)
        if request.url.path == '/api/chat/completions':
            return httpx.Response(200, headers={'content-type': 'application/json'}, content=body)
        return response

    result = asyncio.run(make_verifier(module, ledger, handler).execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'native_completion_ack_not_null'
    assert result['http_attempts'] == 13 and result['model_calls'] == len(api.model_payloads) == 1
    assert result['checks']['sse_transport_observed'] is False
    record = ledger.read()[ledger.namespace]['mock-native-run-01']
    assert record['model_attempted'] is True and not record.get('model_completed')


@pytest.mark.parametrize('status,media,expected', [
    (500, 'application/json', 'invalid_completion_response_status'),
    (302, 'application/json', 'invalid_completion_response_status'),
    (200, 'text/plain', 'invalid_completion_response_type'),
    (200, 'text/event-stream-not-real', 'invalid_completion_response_type'),
])
def test_native_completion_rejects_wrong_status_or_media_without_fallback_call(module, ledger, status, media, expected):
    api = NativeAPI(ledger)

    def handler(request):
        response = api(request)
        if request.url.path == '/api/chat/completions':
            return httpx.Response(status, headers={'content-type': media, 'location': 'https://never-follow.invalid/'}, content=b'null')
        return response

    result = asyncio.run(make_verifier(module, ledger, handler).execute())
    assert result['status'] == 'unknown' and result['error_code'] == expected
    assert result['http_attempts'] == 13 and result['model_calls'] == len(api.model_payloads) == 1


@pytest.mark.parametrize('mutation', ['wrong_chat', 'wrong_owner', 'missing_assistant', 'wrong_parent',
    'wrong_model', 'wrong_message_id', 'wrong_message_user', 'wrong_prompt', 'not_done', 'empty_text', 'reasoning_only', 'error'])
def test_null_ack_cannot_pass_without_the_exact_saved_assistant_reply(module, ledger, mutation):
    api = NativeAPI(ledger)

    def handler(request):
        response = api(request)
        if request.method != 'GET' or request.url.path != '/api/v1/chats/' + api.chat_id or not api.model_payloads:
            return response
        saved, payload = response.json(), api.model_payloads[0]
        messages = saved['chat']['history']['messages']
        message = messages[payload['id']]
        if mutation == 'wrong_chat':
            saved['id'] = 'other-chat'
        elif mutation == 'wrong_owner':
            saved['user_id'] = 'user-id'
        elif mutation == 'missing_assistant':
            messages.pop(payload['id'])
        elif mutation == 'wrong_parent':
            message['parentId'] = 'old-user-message'
        elif mutation == 'wrong_model':
            message['model'] = 'other-model'
        elif mutation == 'wrong_message_id':
            message['id'] = 'old-assistant-message'
        elif mutation == 'wrong_message_user':
            message['user_id'] = 'user-id'
        elif mutation == 'wrong_prompt':
            messages[payload['user_message']['id']]['content'] = 'old-prompt'
        elif mutation == 'not_done':
            message['done'] = False
        elif mutation == 'empty_text':
            message['content'] = '  '
        elif mutation == 'reasoning_only':
            message.update(content='', output=[{'type': 'reasoning', 'content': [{'text': 'private reasoning'}]}])
        elif mutation == 'error':
            message['error'] = {'content': 'do-not-log-upstream-secret'}
        return httpx.Response(200, json=saved)

    result = asyncio.run(make_verifier(module, ledger, handler).execute())
    assert result['status'] == 'unknown' and result['http_attempts'] == 14 and result['model_calls'] == 1
    assert result['checks']['completion_transport'] == 'saved_chat_events'
    assert result['checks']['sse_transport_observed'] is False and not result['checks'].get('real_reply_persisted')
    assert 'do-not-log-upstream-secret' not in json.dumps(result)
    assert not ledger.read()[ledger.namespace]['mock-native-run-01'].get('model_completed')


def test_native_output_message_text_is_accepted_without_content_or_reasoning_leak(module, ledger):
    api = NativeAPI(ledger)

    def handler(request):
        response = api(request)
        if request.method == 'GET' and request.url.path == '/api/v1/chats/' + api.chat_id and api.model_payloads:
            saved = response.json()
            saved['chat']['history']['messages'][api.model_payloads[0]['id']].update(content='', output=[
                {'type': 'reasoning', 'content': [{'text': 'private reasoning'}]},
                {'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]},
            ])
            return httpx.Response(200, json=saved)
        return response

    result = asyncio.run(make_verifier(module, ledger, handler).execute())
    assert result['status'] == 'succeeded' and result['checks']['real_reply_persisted'] is True
    assert result['checks']['sse_transport_observed'] is False
    assert module.visible_reply_text({'output': [{'type': 'reasoning', 'content': [{'text': 'hidden'}]},
        {'type': 'message', 'content': [{'text': 'OK'}]}]}) == 'OK'


def test_historical_unknown_without_chat_reference_is_never_rewritten_or_replayed(module, ledger):
    run_id = 'legacy-native-run-01'
    ledger.begin(run_id, 'new-local-runtime')
    ledger.update(run_id, lambda run: run.update(model_attempted=True, phase='model_attempted', status='unknown', http_attempts=13))
    historical, encrypted = deepcopy(ledger.read()[ledger.namespace][run_id]), ledger.path.read_bytes()
    api = NativeAPI(ledger)
    result = asyncio.run(make_verifier(module, ledger, api, run_id=run_id).execute())
    assert result['status'] == 'failed' and result['error_code'] == 'actual_run_id_already_attempted'
    assert not api.calls and result['model_calls'] == 0
    assert ledger.read()[ledger.namespace][run_id] == historical and ledger.path.read_bytes() == encrypted


def test_sse_failure_retains_unknown_phase_without_retry_or_raw_body(module, ledger):
    api = NativeAPI(ledger, model_error=True, completion_transport='http_sse')
    verifier = make_verifier(module, ledger, api)
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'sse_error_frame'
    assert len(api.model_payloads) == 1 and result['http_attempts'] == 13
    assert result['stop_result']['verified'] is True
    record = ledger.read()[ledger.namespace][verifier.run_id]
    assert record['phase'] == 'model_attempted' and record['model_attempted'] is True
    assert record['status'] == 'unknown' and 'do-not-log' not in json.dumps(result)
    asyncio.run(make_verifier(module, ledger, api).execute())
    assert len(api.model_payloads) == 1 and len(api.calls) == 13


def test_redirect_is_rejected_without_following_or_retry(module, ledger):
    api = NativeAPI(ledger, redirect_health=True)
    result = asyncio.run(make_verifier(module, ledger, api).execute())
    assert result['status'] == 'unknown' and result['http_attempts'] == 1
    assert result['error_code'] == 'unexpected_http_status_307' and len(api.calls) == 1
    assert result['model_calls'] == 0


def test_sse_deadline_preserves_attempt_before_any_repeat(module, ledger, monkeypatch):
    monkeypatch.setattr(module, 'COMPLETION_SECONDS', 0)
    api = NativeAPI(ledger, completion_transport='http_sse')
    result = asyncio.run(make_verifier(module, ledger, api).execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'completion_deadline'
    assert result['model_calls'] == 1 and len(api.model_payloads) == 1
    assert ledger.read()[ledger.namespace]['mock-native-run-01']['model_attempted'] is True


def test_expired_total_budget_sends_no_http(module, ledger):
    api = NativeAPI(ledger)
    verifier = make_verifier(module, ledger, api)
    verifier.work_deadline = time.monotonic() - 1
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'failed' and result['error_code'] == 'total_work_deadline'
    assert not api.calls and verifier.guard.stop_result is None


def test_watcher_failure_cancels_pending_work_and_is_not_reported_success(module, ledger):
    verifier = make_verifier(module, ledger, NativeAPI(ledger))
    started = asyncio.Event()
    cancelled = []

    async def pending_work():
        verifier.http_attempts = 1
        verifier.checkpoint('mock_request_attempted')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def failed_watcher():
        await started.wait()
        raise module.VerificationError('native_launch_changed')

    verifier.http_workflow, verifier.watch = pending_work, failed_watcher
    result = asyncio.run(verifier.execute())
    assert result['status'] == 'unknown' and result['error_code'] == 'native_launch_changed'
    assert cancelled == [True] and result['stop_result']['verified'] is True


class FakeProcess:
    def __init__(self, pid, children=(), *, enumeration_error=False):
        self.pid, self.descendants, self.enumeration_error = pid, list(children), enumeration_error
        self.terminations = 0

    def children(self, recursive=False):
        if self.enumeration_error:
            raise psutil.AccessDenied(self.pid)
        return self.descendants

    def terminate(self):
        self.terminations += 1


def process_guard(module, tmp_path, process):
    info = {'launch_id': 'nonce-A', 'pid': process.pid, 'created_at': 1, 'exe': 'owned-python'}
    data = {'processes': {'openwebui': deepcopy(info)}}
    calls, failures = [], []

    def owned_process(*args):
        calls.append('owned_lookup')
        return process

    runtime = SimpleNamespace(load=lambda root: data,
        same_launch=lambda left, right: left is not None and left == right,
        owned_process=owned_process, update_runtime=lambda root, change: change(data))
    services = SimpleNamespace(process_tree_rss=lambda process: module.RSS_LIMIT + 1,
        record_api_failure=lambda runtime, root, reason, detail: failures.append((reason, deepcopy(detail))))
    guard = module.NativeGuard(tmp_path, runtime, services, time.monotonic() + 5)
    guard.initial = info
    return guard, data, calls, failures


def test_changed_launch_cannot_stop_or_lookup_another_process(module, tmp_path):
    process = FakeProcess(123)
    guard, data, calls, failures = process_guard(module, tmp_path, process)
    data['processes']['openwebui']['launch_id'] = 'nonce-B'
    guard.stop_owned('test_failure')
    assert guard.stop_result['verified'] is False and 'no_process_touched' in guard.stop_result['reason']
    assert not calls and process.terminations == 0 and len(failures) == 1


def test_enumeration_failure_is_recorded_without_unverified_kill_or_retry(module, tmp_path):
    process = FakeProcess(123, enumeration_error=True)
    guard, _, calls, failures = process_guard(module, tmp_path, process)
    guard.stop_owned('test_failure')
    guard.stop_owned('must_not_retry')
    assert guard.stop_result['verified'] is False and guard.stop_result['error_type'] == 'AccessDenied'
    assert process.terminations == 0 and len(calls) == 1 and len(failures) == 1


def test_alive_child_cannot_be_reported_as_stopped(module, tmp_path, monkeypatch):
    child, process = FakeProcess(124), FakeProcess(123)
    process.descendants = [child]
    guard, data, _, failures = process_guard(module, tmp_path, process)
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda processes, timeout: ([process], [child]))
    guard.stop_owned('test_failure')
    assert guard.stop_result['verified'] is False and guard.stop_result['alive_pids'] == [124]
    assert child.terminations == process.terminations == 1
    assert 'openwebui' in data['processes'] and failures


def test_rss_overrun_only_stops_verified_tree_and_keeps_fault_evidence(module, tmp_path, monkeypatch):
    child, process = FakeProcess(124), FakeProcess(123)
    process.descendants = [child]
    guard, data, _, failures = process_guard(module, tmp_path, process)
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda processes, timeout: (processes, []))
    with pytest.raises(module.VerificationError, match='rss_budget_exceeded'):
        guard.sample()
    assert guard.stop_result['verified'] is True and guard.maximum_rss > module.RSS_LIMIT
    assert child.terminations == process.terminations == 1 and not data['processes']
    assert failures[0][0] == 'rss_budget_exceeded'


def inspection_guard(module, tmp_path, *, environment_change=None):
    source = (tmp_path / 'sources/openwebui').resolve()
    data_dir = (tmp_path / 'openwebui/data').resolve()
    sqlite = data_dir / 'webui.db'
    info = {'launch_id': 'nonce-A', 'mode': 'api_only', 'frontend_available': False,
        'ready': True, 'acknowledged_resource_incident': True,
        'sqlite_file': str(sqlite), 'data_directory': str(data_dir)}
    python = (tmp_path / 'venvs/openwebui/Scripts/python.exe').resolve()
    data = {'id': 'isolated-runtime', 'processes': {'openwebui': info},
        'sources': {'openwebui': str(source)}, 'binaries': {'python312': str(python)},
        'databases': {'openwebui': {'runtime_id': 'isolated-runtime', 'sqlite_file': str(sqlite), 'data_directory': str(data_dir)}}}
    env = {'DATABASE_URL': 'sqlite:///' + sqlite.as_posix(), 'DATA_DIR': str(data_dir),
        'PYTHONPATH': str(source / 'backend'), 'PYTHON_DOTENV_DISABLED': '1', 'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1', 'RAG_EMBEDDING_ENGINE': 'openai', 'ENABLE_OLLAMA_API': 'False',
        'ENABLE_AUTOMATIONS': 'False', 'ENABLE_CALENDAR': 'False', 'TIMER_POLL_INTERVAL': '60', 'SCHEDULER_POLL_INTERVAL': '60'}
    env.update(environment_change or {})
    process = SimpleNamespace(pid=123, children=lambda recursive: [],
        net_connections=lambda kind: [SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(port=13000, ip='127.0.0.1'))],
        exe=lambda: str(python), cwd=lambda: str(source / 'backend'),
        cmdline=lambda: [str(python), '-m', 'uvicorn', 'open_webui.main:app', '--host', '127.0.0.1', '--port', '13000', '--workers', '1'],
        environ=lambda: dict(env))
    runtime = SimpleNamespace(load=lambda root: data, owned_process=lambda *args: process,
        same_launch=lambda left, right: left == right)
    services = SimpleNamespace(openwebui_paths=lambda *args, **kwargs: {}, process_tree_rss=lambda process: 1024)
    guard = module.NativeGuard(tmp_path, runtime, services, time.monotonic() + 5)
    return guard, data


@pytest.mark.parametrize('changes,code', [
    ({'DATABASE_HOST': 'remote.invalid'}, 'unexpected_external_database_configuration'),
    ({'REDIS_SENTINEL_HOSTS': 'remote.invalid:26379'}, 'unexpected_external_database_configuration'),
    ({'CHROMA_HTTP_HOST': 'remote.invalid'}, 'unexpected_external_database_configuration'),
    ({'VECTOR_DB': 'pgvector'}, 'unexpected_vector_backend'),
    ({'RAG_EMBEDDING_ENGINE': ''}, 'actual_api_isolation_configuration_mismatch'),
    ({'HF_HUB_OFFLINE': '0'}, 'actual_api_isolation_configuration_mismatch'),
])
def test_actual_worker_environment_rejects_external_storage_and_model_download(module, tmp_path, changes, code):
    guard, _ = inspection_guard(module, tmp_path, environment_change=changes)
    with pytest.raises(module.VerificationError, match=code):
        guard.inspect()


def test_actual_worker_storage_requires_matching_new_runtime_binding(module, tmp_path):
    guard, data = inspection_guard(module, tmp_path)
    assert guard.inspect()['id'] == 'isolated-runtime'
    data['databases']['openwebui']['runtime_id'] = 'unowned-runtime'
    with pytest.raises(module.VerificationError, match='sqlite_binding_mismatch'):
        guard.inspect()


@pytest.mark.parametrize('api_only', [True, False])
def test_official_scheduler_env_override_is_limited_to_api_only(tmp_path, api_only):
    spec = importlib.util.spec_from_file_location('native_services_api_test', ROOT / 'deploy/native/windows_services.py')
    services = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(services)
    root = tmp_path.resolve()
    paths = {'data': root / 'openwebui/data', 'sqlite': root / 'openwebui/data/webui.db',
        'static': root / 'openwebui/static', 'backend': root / 'sources/openwebui/backend',
        'model_cache': root / 'openwebui/model-cache',
        'frontend': root / 'openwebui/api-only-no-frontend' if api_only else root / 'sources/openwebui/build'}
    runtime = SimpleNamespace(base_environment=lambda values: dict(values), openwebui_provider_environment=lambda: {})
    env = services.openwebui_environment(runtime, paths, {'webui_secret': 'temporary-test-secret'})
    assert env['RAG_EMBEDDING_MODEL_AUTO_UPDATE'] == env['WHISPER_MODEL_AUTO_UPDATE'] == 'False'
    if api_only:
        assert env['ENABLE_AUTOMATIONS'] == env['ENABLE_CALENDAR'] == 'False'
        assert env['TIMER_POLL_INTERVAL'] == env['SCHEDULER_POLL_INTERVAL'] == '60'
    else:
        assert not {'ENABLE_AUTOMATIONS', 'ENABLE_CALENDAR', 'TIMER_POLL_INTERVAL', 'SCHEDULER_POLL_INTERVAL'} & env.keys()
