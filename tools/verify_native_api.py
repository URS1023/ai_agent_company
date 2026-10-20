"""对已启动的本机OWUI API做一次有限验收；不安装、不启动、不重放。

固定14次本机HTTP、最多一次64-token SSE；55秒业务预算预留5秒退出核查。
账号密码先写既有credentials.enc，逐阶段保存令牌和尝试状态；输出不含身份或密钥。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sys
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import httpx
import psutil

REPO = Path(__file__).resolve().parents[1]
MODEL = 'deepseek-v4.1-flash'
MAX_HTTP = 14
TOTAL_SECONDS = 60
WORK_SECONDS = 55
SSE_SECONDS = 30
RSS_LIMIT = 1024 ** 3
PROMPT = '请直接回复OK，不要展开说明。'


class VerificationError(RuntimeError):
    """只携带安全代码，不将上游响应、凭据或环境拼入异常。"""


def native_modules():
    sys.path.insert(0, str(REPO / 'deploy/native'))
    import windows
    import windows_services
    return windows, windows_services


class CredentialLedger:
    """复用本轮已有Fernet密钥/密文，原子保管账号及不可重放的actual run_id。"""
    namespace = 'native_api_verifications'

    def __init__(self, root: Path, runtime):
        self.root, self.runtime = root, runtime
        self.path = runtime.ensure_inside(root, root / 'credentials.enc')
        self.key = runtime.ensure_inside(root, root / 'runtime.key')
        self.cipher = Fernet(self.key.read_bytes().strip())

    def read(self):
        return json.loads(self.cipher.decrypt(self.path.read_bytes()))

    def mutate(self, change):
        with self.runtime.runtime_lock(self.root):
            data = self.read()
            change(data)
            temporary = None
            try:
                with NamedTemporaryFile(dir=self.root, suffix='.enc.pending', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(self.cipher.encrypt(json.dumps(data, ensure_ascii=False).encode()))
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.chmod(0o600)
                temporary.replace(self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def begin(self, run_id: str, runtime_id: str) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9._-]{8,64}', run_id):
            raise VerificationError('invalid_run_id')
        suffix = secrets.token_hex(6)
        record = {'actual_run_id': run_id, 'runtime_id': runtime_id, 'phase': 'prepared', 'status': 'running',
            'model_attempted': False, 'http_attempts': 0, 'steps': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'accounts': {role: {'name': '本机验收' + label, 'email': f'native-{role}-{suffix}@example.com',
                'password': secrets.token_urlsafe(32) + '!Aa1'} for role, label in [('admin', 'A'), ('user', 'B')]}}
        def change(data):
            runs = data.setdefault(self.namespace, {})
            if run_id in runs:
                raise VerificationError('actual_run_id_already_attempted')
            runs[run_id] = record
        self.mutate(change)
        return record

    def update(self, run_id: str, change):
        def mutate(data):
            change(data[self.namespace][run_id])
        self.mutate(mutate)


class NativeGuard:
    """只核本次登记的父进程/监听者/新SQLite；RSS超限仅停止已核实的该树。"""
    def __init__(self, root: Path, runtime, services, deadline: float):
        self.root, self.runtime, self.services, self.deadline = root, runtime, services, deadline
        self.initial = None
        self.maximum_rss = 0
        self.stop_result = None

    def inspect(self):
        data = self.runtime.load(self.root)
        self.services.openwebui_paths(self.runtime, self.root, data, api_only=True)
        info = data.get('processes', {}).get('openwebui', {})
        if info.get('mode') != 'api_only' or info.get('frontend_available') is not False or not info.get('ready'):
            raise VerificationError('not_ready_api_only_process')
        if not info.get('acknowledged_resource_incident') or not info.get('launch_id'):
            raise VerificationError('api_scope_not_acknowledged')
        if self.initial is not None and not self.runtime.same_launch(info, self.initial):
            raise VerificationError('native_launch_changed')
        process = self.runtime.owned_process(self.root, 'openwebui', data)
        if process is None:
            raise VerificationError('native_process_not_running')
        expected_sqlite = str((self.root / 'openwebui/data/webui.db').resolve())
        expected_data = str((self.root / 'openwebui/data').resolve())
        binding = data.get('databases', {}).get('openwebui', {})
        if binding != {'runtime_id': data['id'], 'sqlite_file': expected_sqlite, 'data_directory': expected_data}:
            raise VerificationError('sqlite_binding_mismatch')
        if info.get('sqlite_file') != expected_sqlite or info.get('data_directory') != expected_data:
            raise VerificationError('process_storage_target_mismatch')
        if self.initial is None:
            self.initial = dict(info)
        rss = self.services.process_tree_rss(process)
        self.maximum_rss = max(rss, self.maximum_rss)
        if rss > RSS_LIMIT:
            self.stop_owned('rss_budget_exceeded')
            raise VerificationError('rss_budget_exceeded')
        listeners = []
        for child in [process, *process.children(recursive=True)]:
            for connection in child.net_connections(kind='inet'):
                if connection.status == 'LISTEN' and connection.laddr.port == 13000 and connection.laddr.ip == '127.0.0.1':
                    listeners.append(child)
        if len({child.pid for child in listeners}) != 1:
            raise VerificationError('native_listener_not_uniquely_owned')
        worker = listeners[0]
        source = Path(data['sources']['openwebui']).resolve()
        executables = {(self.root / 'venvs/openwebui/Scripts/python.exe').resolve(),
            Path(data['binaries']['python312']).resolve()}
        if Path(worker.exe()).resolve() not in executables or Path(worker.cwd()).resolve() != source / 'backend':
            raise VerificationError('native_worker_origin_mismatch')
        args = worker.cmdline()
        if 'uvicorn' not in args or 'open_webui.main:app' not in args:
            raise VerificationError('unexpected_native_worker_command')
        for option, value in [('--host', '127.0.0.1'), ('--port', '13000'), ('--workers', '1')]:
            if option not in args or args.index(option) + 1 >= len(args) or args[args.index(option) + 1] != value:
                raise VerificationError('unexpected_native_worker_binding')
        # 只提取存储/隔离字段，立即释放完整env；不返回、打印或持久化其中模型密钥。
        raw = worker.environ()
        names = ['DATABASE_URL', 'DATA_DIR', 'DATABASE_TYPE', 'DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_USER',
            'DATABASE_PASSWORD', 'DATABASE_NAME', 'REDIS_URL', 'REDIS_SENTINEL_HOSTS', 'WEBSOCKET_REDIS_URL',
            'WEBSOCKET_MANAGER', 'VECTOR_DB', 'CHROMA_HTTP_HOST', 'USE_SLIM_DOCKER',
            'PYTHONPATH', 'PYTHON_DOTENV_DISABLED', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE',
            'RAG_EMBEDDING_ENGINE', 'ENABLE_OLLAMA_API', 'ENABLE_AUTOMATIONS', 'ENABLE_CALENDAR',
            'TIMER_POLL_INTERVAL', 'SCHEDULER_POLL_INTERVAL']
        selected = {key: raw.get(key, '') for key in names}
        raw.clear()
        if selected['DATABASE_URL'] != 'sqlite:///' + Path(expected_sqlite).as_posix() or selected['DATA_DIR'] != expected_data:
            raise VerificationError('actual_worker_sqlite_mismatch')
        if any(selected[name] for name in ['DATABASE_TYPE', 'DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_USER',
                'DATABASE_PASSWORD', 'DATABASE_NAME', 'REDIS_URL', 'REDIS_SENTINEL_HOSTS',
                'WEBSOCKET_REDIS_URL', 'WEBSOCKET_MANAGER', 'CHROMA_HTTP_HOST']):
            raise VerificationError('unexpected_external_database_configuration')
        if selected['VECTOR_DB'] not in {'', 'chroma'} or selected['USE_SLIM_DOCKER'].lower() not in {'', 'false'}:
            raise VerificationError('unexpected_vector_backend')
        expected = {'PYTHONPATH': str(source / 'backend'), 'PYTHON_DOTENV_DISABLED': '1', 'HF_HUB_OFFLINE': '1',
            'TRANSFORMERS_OFFLINE': '1', 'RAG_EMBEDDING_ENGINE': 'openai', 'ENABLE_OLLAMA_API': 'False',
            'ENABLE_AUTOMATIONS': 'False', 'ENABLE_CALENDAR': 'False', 'TIMER_POLL_INTERVAL': '60', 'SCHEDULER_POLL_INTERVAL': '60'}
        if any(selected[key] != value for key, value in expected.items()):
            raise VerificationError('actual_api_isolation_configuration_mismatch')
        return data

    def sample(self):
        data = self.runtime.load(self.root)
        info = data.get('processes', {}).get('openwebui')
        if not self.runtime.same_launch(info, self.initial):
            raise VerificationError('native_launch_changed')
        process = self.runtime.owned_process(self.root, 'openwebui', data)
        if process is None:
            raise VerificationError('native_process_exited')
        rss = self.services.process_tree_rss(process)
        self.maximum_rss = max(rss, self.maximum_rss)
        if rss > RSS_LIMIT:
            self.stop_owned('rss_budget_exceeded')
            raise VerificationError('rss_budget_exceeded')

    def stop_owned(self, reason: str):
        # 先占用停止状态；失败后不自动再杀一次，也不把未核实状态写成已停止。
        if self.stop_result is not None:
            return
        self.stop_result = {'verified': False, 'reason': 'stop_not_completed'}
        try:
            data = self.runtime.load(self.root)
            info = data.get('processes', {}).get('openwebui')
            if self.initial is None or not self.runtime.same_launch(info, self.initial):
                self.stop_result = {'verified': False, 'reason': 'launch_changed_no_process_touched'}
            else:
                process = self.runtime.owned_process(self.root, 'openwebui', data)
                if process is None:
                    self.stop_result = {'verified': True, 'alive_pids': []}
                else:
                    # 枚举失败时不贸然终止父进程，避免留下无法核实的子进程。
                    processes = [*process.children(recursive=True), process]
                    errors = []
                    for child in processes:
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                        except psutil.Error as error:
                            errors.append(type(error).__name__)
                    try:
                        _, alive = psutil.wait_procs(processes, timeout=max(0, min(3, self.deadline - time.monotonic())))
                    except psutil.Error as error:
                        errors.append(type(error).__name__)
                        alive = processes
                    self.stop_result = {'verified': not alive and not errors, 'alive_pids': [child.pid for child in alive], 'errors': errors}
                    if self.stop_result['verified']:
                        def remove(state):
                            if self.runtime.same_launch(state.get('processes', {}).get('openwebui'), self.initial):
                                state['processes'].pop('openwebui', None)
                        self.runtime.update_runtime(self.root, remove)
        except Exception as error:
            self.stop_result.update(verified=False, error_type=type(error).__name__)
        # 先处理已核实的本次进程，再记故障；不能因记录成功而假报已停止。
        try:
            self.services.record_api_failure(self.runtime, self.root, reason,
                {'verifier_stop': self.stop_result, 'maximum_rss_bytes': self.maximum_rss})
        except Exception as error:
            self.stop_result['incident_record_error_type'] = type(error).__name__


class Verifier:
    def __init__(self, root: Path, run_id: str, runtime, services, *, guard=None, ledger=None, transport=None):
        self.root, self.run_id, self.runtime, self.services = root, run_id, runtime, services
        self.started_at = time.monotonic()
        self.deadline = self.started_at + TOTAL_SECONDS
        self.work_deadline = self.started_at + WORK_SECONDS
        self.guard = guard or NativeGuard(root, runtime, services, self.deadline)
        self.ledger = ledger or CredentialLedger(root, runtime)
        self.transport = transport
        self.record = None
        self.http_attempts, self.model_calls, self.phase = 0, 0, 'preflight'
        self.checks = {}

    def remaining(self):
        value = self.work_deadline - time.monotonic()
        if value <= 0:
            raise VerificationError('total_work_deadline')
        return value

    def checkpoint(self, phase: str, **values):
        self.phase = phase
        if self.record is not None:
            self.ledger.update(self.run_id, lambda record: record.update(phase=phase, **values))

    def token(self, role: str) -> str:
        return self.record['accounts'][role].get('token', '')

    def save_auth(self, role: str, data: dict, *, signin=False):
        if data.get('role') != role or not isinstance(data.get('token'), str) or not data['token']:
            raise VerificationError('unexpected_native_auth_response')
        key = 'token' if signin else 'issued_token'
        changes = {'id': data.get('id'), key: data['token']}
        self.ledger.update(self.run_id, lambda record: record['accounts'][role].update(changes))
        self.record['accounts'][role].update(changes)

    async def request(self, client, step: str, method: str, path: str, *, role=None, payload=None, expected=(200,)):
        self.guard.sample()
        self.remaining()
        if self.http_attempts >= MAX_HTTP:
            raise VerificationError('http_budget_exceeded')
        self.http_attempts += 1
        self.checkpoint(step + '_attempted', http_attempts=self.http_attempts)
        headers = {'Authorization': 'Bearer ' + self.token(role)} if role else {}
        async with asyncio.timeout(min(6, self.remaining())):
            async with client.stream(method, path, json=payload, headers=headers) as response:
                if response.status_code not in expected:
                    raise VerificationError('unexpected_http_status_' + str(response.status_code))
                body = bytearray()
                async for part in response.aiter_bytes():
                    self.guard.sample()
                    self.remaining()
                    body.extend(part)
                    if len(body) > 512 * 1024:
                        raise VerificationError('response_body_budget')
                value = json.loads(body) if body else None
        self.guard.sample()
        self.ledger.update(self.run_id, lambda record: record['steps'].append({'step': step, 'status': response.status_code}))
        return value

    async def sse(self, client, chat_id: str):
        if self.model_calls or self.record.get('model_attempted'):
            raise VerificationError('model_attempt_already_recorded')
        self.guard.sample()
        self.remaining()
        self.model_calls = 1
        self.http_attempts += 1
        if self.http_attempts > MAX_HTTP:
            raise VerificationError('http_budget_exceeded')
        self.checkpoint('model_attempted', model_attempted=True, http_attempts=self.http_attempts)
        self.record['model_attempted'] = True
        user_id, assistant_id = str(uuid4()), str(uuid4())
        payload = {'model': MODEL, 'chat_id': chat_id, 'id': assistant_id,
            'user_message': {'id': user_id, 'parentId': None, 'childrenIds': [assistant_id], 'role': 'user',
                'content': PROMPT, 'models': [MODEL], 'timestamp': int(time.time())},
            'messages': [{'role': 'user', 'content': PROMPT}], 'stream': True, 'params': {'max_tokens': 64},
            'background_tasks': {}, 'features': {'memory': False, 'web_search': False, 'image_generation': False, 'code_interpreter': False},
            'tool_ids': [], 'files': []}
        deadline = min(self.work_deadline, time.monotonic() + SSE_SECONDS)
        content, frames, wire_bytes, done = [], 0, 0, False
        async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
            async with client.stream('POST', '/api/chat/completions', json=payload,
                    headers={'Authorization': 'Bearer ' + self.token('admin')}) as response:
                if response.status_code != 200 or 'text/event-stream' not in response.headers.get('content-type', ''):
                    raise VerificationError('invalid_sse_response')
                async for line in response.aiter_lines():
                    self.guard.sample()
                    if time.monotonic() >= deadline:
                        raise VerificationError('sse_deadline')
                    wire_bytes += len(line.encode())
                    if wire_bytes > 128 * 1024:
                        raise VerificationError('sse_body_budget')
                    if not line.startswith('data:'):
                        continue
                    raw = line[5:].strip()
                    if raw == '[DONE]':
                        done = True
                        continue
                    if not raw:
                        continue
                    frame = json.loads(raw)
                    if frame.get('error'):
                        raise VerificationError('sse_error_frame')
                    frames += 1
                    for choice in frame.get('choices', []):
                        delta = choice.get('delta', {})
                        if isinstance(delta.get('content'), str):
                            content.append(delta['content'])
                        if choice.get('finish_reason') is not None:
                            done = True
        self.guard.sample()
        if not done or not ''.join(content).strip() or frames == 0:
            raise VerificationError('sse_no_completed_text_reply')
        self.checks.update(sse_completed=True, sse_text_present=True)
        self.checkpoint('model_completed', model_completed=True)
        return assistant_id

    async def workflow(self, a, b):
        health = await self.request(a, 'health', 'GET', '/health')
        if health != {'status': True}:
            raise VerificationError('health_payload_mismatch')
        admin, user = self.record['accounts']['admin'], self.record['accounts']['user']
        result = await self.request(a, 'admin_signup', 'POST', '/api/v1/auths/signup', payload={key: admin[key] for key in ['name', 'email', 'password']})
        self.save_auth('admin', result)
        result = await self.request(a, 'admin_signin', 'POST', '/api/v1/auths/signin', payload={key: admin[key] for key in ['email', 'password']})
        self.save_auth('admin', result, signin=True)
        own = await self.request(a, 'admin_identity', 'GET', '/api/v1/auths/', role='admin')
        if own.get('id') != admin['id'] or own.get('role') != 'admin':
            raise VerificationError('admin_identity_mismatch')
        models = await self.request(a, 'models', 'GET', '/api/models', role='admin')
        if MODEL not in {item.get('id') for item in models.get('data', [])}:
            raise VerificationError('configured_model_not_visible')
        chat = await self.request(a, 'new_private_chat', 'POST', '/api/v1/chats/new', role='admin',
            payload={'chat': {'title': '本机验收', 'models': [MODEL], 'history': {'currentId': None, 'messages': {}}}})
        chat_id = chat.get('id')
        if not isinstance(chat_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', chat_id):
            raise VerificationError('invalid_native_chat_id')
        own_chat = await self.request(a, 'owner_reads_chat', 'GET', '/api/v1/chats/' + chat_id, role='admin')
        if own_chat.get('user_id') != admin['id']:
            raise VerificationError('chat_owner_mismatch')
        result = await self.request(a, 'add_ordinary_user', 'POST', '/api/v1/auths/add', role='admin',
            payload={**{key: user[key] for key in ['name', 'email', 'password']}, 'role': 'user'})
        self.save_auth('user', result)
        result = await self.request(b, 'user_signin', 'POST', '/api/v1/auths/signin', payload={key: user[key] for key in ['email', 'password']})
        self.save_auth('user', result, signin=True)
        await self.request(b, 'user_cannot_read_admin_chat', 'GET', '/api/v1/chats/' + chat_id, role='user', expected=(401,))
        await self.request(b, 'user_cannot_write_admin_chat', 'POST', '/api/v1/chats/' + chat_id, role='user',
            payload={'chat': {'title': '不能保存'}}, expected=(401,))
        listing = await self.request(b, 'user_list_isolated', 'GET', '/api/v1/chats/list', role='user')
        if not isinstance(listing, list) or any(item.get('id') == chat_id for item in listing):
            raise VerificationError('private_chat_leaked_in_user_list')
        self.checks.update(authenticated_admin=True, authenticated_ordinary_user=True, private_chat_read_denied=True,
            private_chat_write_denied=True, private_chat_not_listed=True, configured_model_visible=True)
        assistant_id = await self.sse(a, chat_id)
        saved = await self.request(a, 'saved_model_reply', 'GET', '/api/v1/chats/' + chat_id, role='admin')
        message = (saved.get('chat', {}).get('history', {}).get('messages', {}) or {}).get(assistant_id, {})
        if message.get('role') != 'assistant' or message.get('done') is not True or not str(message.get('content', '')).strip():
            raise VerificationError('native_reply_not_persisted')
        self.checks['real_reply_persisted'] = True

    async def watch(self):
        while True:
            self.guard.sample()
            self.remaining()
            await asyncio.sleep(0.25)

    async def http_workflow(self):
        """将HTTP关闭也放入受监控任务，防止关闭阶段丢失资源/身份异常。"""
        transport_a = self.transport or httpx.AsyncHTTPTransport(retries=0)
        transport_b = self.transport or httpx.AsyncHTTPTransport(retries=0)
        options = {'base_url': 'http://127.0.0.1:13000', 'trust_env': False, 'follow_redirects': False,
            'timeout': httpx.Timeout(5, connect=3), 'limits': httpx.Limits(max_connections=1)}
        async with httpx.AsyncClient(transport=transport_a, **options) as a, httpx.AsyncClient(transport=transport_b, **options) as b:
            await self.workflow(a, b)

    async def execute(self) -> dict:
        work = watcher = None
        status, error_type, error_code = 'failed', None, None
        recording_error = None
        try:
            data = self.guard.inspect()
            self.record = self.ledger.begin(self.run_id, data['id'])
            self.phase = 'prepared'
            work, watcher = asyncio.create_task(self.http_workflow()), asyncio.create_task(self.watch())
            finished, _ = await asyncio.wait({work, watcher}, timeout=self.remaining(), return_when=asyncio.FIRST_COMPLETED)
            if watcher in finished:
                watcher.result()
                raise VerificationError('unexpected_watch_exit')
            if work not in finished:
                raise VerificationError('total_work_deadline')
            work.result()
            status = 'succeeded'
        except Exception as error:
            error_type = type(error).__name__
            error_code = str(error) if isinstance(error, VerificationError) else None
            status = 'unknown' if self.http_attempts else 'failed'
        finally:
            tasks = [task for task in [work, watcher] if task is not None]
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=max(0, self.deadline - time.monotonic()))
                for task in done:
                    if not task.cancelled() and task.exception() is not None and status == 'succeeded':
                        error = task.exception()
                        status, error_type = 'unknown', type(error).__name__
                        error_code = str(error) if isinstance(error, VerificationError) else None
                if pending:
                    status, error_type, error_code = 'unknown', 'VerificationError', 'cancellation_not_confirmed_within_budget'
        if status == 'succeeded' and self.guard.stop_result is not None:
            status, error_code = 'unknown', 'resource_failure_during_finalization'
        if status != 'succeeded' and self.record is not None and self.http_attempts and self.guard.initial is not None and self.guard.stop_result is None:
            self.guard.stop_owned('native_verification_failed')
        if self.record is not None:
            try:
                self.checkpoint('completed' if status == 'succeeded' else self.phase, status=status,
                    error_type=error_type, error_code=error_code,
                    http_attempts=self.http_attempts, model_calls=self.model_calls, checks=self.checks)
            except Exception as error:
                recording_error = type(error).__name__
                status = 'unknown'
        return {'actual_run_id': self.run_id, 'status': status, 'phase': self.phase, 'error_type': error_type, 'error_code': error_code,
            'checkpoint_error_type': recording_error,
            'http_attempts': self.http_attempts, 'http_budget': MAX_HTTP, 'model_calls': self.model_calls,
            'maximum_rss_bytes': self.guard.maximum_rss, 'stop_result': self.guard.stop_result,
            'checks': self.checks, 'duration_seconds': round(time.monotonic() - self.started_at, 3),
            'frontend_available': False, 'full_native_acceptance': False}


def main():
    parser = argparse.ArgumentParser(description='只验已启动本机OWUI API；固定14 HTTP/1 SSE，不重放')
    parser.add_argument('--root', type=Path, default=REPO / '.local/native_windows')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    verifier = None
    try:
        runtime, services = native_modules()
        verifier = Verifier(args.root.resolve(), args.run_id, runtime, services)
        result = asyncio.run(verifier.execute())
    except Exception as error:
        result = {'actual_run_id': args.run_id, 'status': 'failed', 'phase': verifier.phase if verifier else 'preflight', 'error_type': type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'succeeded' else 1)


if __name__ == '__main__':
    main()
