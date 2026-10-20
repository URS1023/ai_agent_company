"""本机Dify的有限原生验收：首次初始化、登录和两条无模型代码流程。

prepare只写本机密文，verify不安装或启动服务。一次180秒、120业务HTTP、400静态请求；
运行由真实原生编辑器发起，Python/JavaScript各一次，失败保留尝试记录且不自动重放。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
import httpx
import psutil
import yaml

from verify_native_api import CredentialLedger, VerificationError

REPO = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1:13001'
TOTAL_SECONDS = 180
MAX_HTTP = 120
MAX_STATIC = 400
PROTOCOL_FILES = ['api/controllers/console/setup.py', 'api/controllers/console/init_validate.py',
    'api/controllers/console/auth/login.py', 'api/controllers/console/app/app.py',
    'api/controllers/console/app/workflow.py', 'api/core/app/entities/task_entities.py']


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def modules():
    sys.path.insert(0, str(REPO / 'deploy/native'))
    import windows
    import wsl
    return windows, wsl


def checked_path(path: Path, parent: Path) -> Path:
    """只使用项目内独立目录，不允许重解析路径或回退旧运行目录。"""
    actual = path.resolve()
    if actual != path.absolute() or not actual.is_relative_to(parent.resolve()) or actual == parent.resolve():
        raise VerificationError('verification_path_not_owned')
    return actual


class DifyLedger(CredentialLedger):
    """沿用既有原子Fernet账本，以桌面交付密钥保管新Dify账号，不写明文状态文件。"""
    namespace = 'dify_native_verifications'

    def __init__(self, repo: Path = REPO, runtime=None):
        self.runtime = runtime or modules()[0]
        self.root = checked_path(repo / '.local/dify-native-verification', repo)
        self.root.mkdir(parents=True, exist_ok=True)
        self.key = checked_path(repo / '.local/desktop-access.key', repo)
        self.path = checked_path(self.root / 'credentials.enc', self.root)
        if not self.key.is_file():
            raise VerificationError('desktop_delivery_key_not_ready')
        self.cipher = Fernet(self.key.read_bytes().strip())
        with self.runtime.runtime_lock(self.root):
            if not self.path.exists():
                with self.path.open('xb') as handle:
                    handle.write(self.cipher.encrypt(b'{}'))
                self.path.chmod(0o600)

    def prepare(self, run_id: str, runtime_id: str) -> dict:
        if not re.fullmatch('[A-Za-z0-9._-]{8,64}', run_id) or not re.fullmatch('[a-f0-9]{32}', runtime_id):
            raise VerificationError('invalid_verification_identity')
        suffix = secrets.token_hex(6)
        record = {'actual_run_id': run_id, 'runtime_id': runtime_id, 'status': 'prepared', 'phase': 'prepared',
            'created_at': now(), 'http_attempts': 0, 'model_attempts': 0, 'flows': {},
            'account': {'email': f'dify-native-{suffix}@example.com', 'name': 'Dify本机验收',
                'password': secrets.token_urlsafe(30) + 'Aa1!'}}
        def change(data):
            runs = data.setdefault(self.namespace, {})
            if run_id in runs:
                raise VerificationError('actual_run_id_already_exists')
            runs[run_id] = record
        self.mutate(change)
        return record

    def record(self, run_id: str) -> dict:
        record = self.read().get(self.namespace, {}).get(run_id)
        if not record:
            raise VerificationError('prepared_run_not_found')
        return record

    def claim(self, run_id: str) -> dict:
        def change(record):
            if record['status'] != 'prepared':
                raise VerificationError('actual_run_id_already_attempted')
            record.update(status='running', phase='ownership', attempted_at=now())
        self.update(run_id, change)
        return self.record(run_id)

    def checkpoint(self, run_id: str, language: str, app_id: str, request_sha256: str) -> None:
        """浏览器放行run之前同步落盘；请求中断也不能再次运行同一流程。"""
        def change(record):
            if record['status'] != 'running' or language not in ('python3', 'javascript'):
                raise VerificationError('workflow_dispatch_not_active')
            flow = record['flows'].get(language, {})
            if flow.get('app_id') != app_id or flow.get('dispatch_attempted'):
                raise VerificationError('workflow_dispatch_already_attempted_or_changed')
            if request_sha256 != digest({'inputs': {}, 'files': []}):
                raise VerificationError('unexpected_workflow_run_body')
            flow.update(dispatch_attempted=True, request_sha256=request_sha256, dispatched_at=now())
        self.update(run_id, change)

    def claim_browser_recovery(self, run_id: str, reviewed_failed_run_id: str, runtime_id: str) -> dict:
        """仅恢复已审阅的工具路径AttributeError，账户和两草稿沿用，旧失败完整入密文历史。"""
        def change(record):
            if run_id != reviewed_failed_run_id or record.get('runtime_id') != runtime_id:
                raise VerificationError('browser_recovery_reviewed_identity_required')
            if (record.get('status'), record.get('error_code')) != ('failed', 'AttributeError'):
                raise VerificationError('browser_recovery_requires_tool_path_failure')
            if 'browser_process' in record or record.get('browser_recovery') or record.get('history'):
                raise VerificationError('browser_recovery_already_attempted_or_started')
            if not record.get('setup_succeeded') or record.get('http_attempts') != 10 or record.get('model_attempts') != 0:
                raise VerificationError('browser_recovery_preparation_not_complete')
            if not record.get('ownership', {}).get('verified') or record['ownership'].get('runtime_id') != runtime_id:
                raise VerificationError('browser_recovery_original_ownership_missing')
            if set(record.get('flows', {})) != {'python3', 'javascript'}:
                raise VerificationError('browser_recovery_two_saved_drafts_required')
            ids = []
            for language, flow in record['flows'].items():
                plan = workflow_plan(language, run_id)
                try: app_id = str(UUID(flow['app_id']))
                except (KeyError, ValueError, TypeError): raise VerificationError('browser_recovery_app_identity_invalid') from None
                if not flow.get('create_attempted') or not flow.get('draft_saved') or flow.get('dispatch_attempted'):
                    raise VerificationError('browser_recovery_must_precede_all_dispatches')
                if flow.get('graph_sha256') != digest(plan['graph']):
                    raise VerificationError('browser_recovery_original_graph_changed')
                ids.append(app_id)
            if len(set(ids)) != 2 or record.get('phase') != 'POST:/console/api/apps/' + record['flows']['javascript']['app_id'] + '/workflows/draft':
                raise VerificationError('browser_recovery_original_phase_changed')
            previous_output = checked_path(self.root / run_id, self.root)
            if not previous_output.is_dir() or any(previous_output.iterdir()):
                raise VerificationError('browser_recovery_original_output_not_empty')
            original = deepcopy(record)
            record.update(status='running', error_code=None, finished_at=None, phase='browser-recovery-ownership',
                history=[original], browser_recovery={'id': uuid4().hex, 'status': 'attempted',
                    'reviewed_failed_run_id': reviewed_failed_run_id, 'original_http_attempts': 10, 'attempted_at': now()})
        self.update(run_id, change)
        return self.record(run_id)


def workflow_plan(language: str, run_id: str) -> dict:
    """直接沿用锁定Dify工作流fixture的Start/Code/End结构，不引入自建执行内核。"""
    if language not in ('python3', 'javascript') or not re.fullmatch('[A-Za-z0-9._-]{8,64}', run_id):
        raise VerificationError('invalid_fixed_workflow')
    marker = ('PY_NATIVE_OK_' if language == 'python3' else 'JS_NATIVE_OK_') + run_id
    if language == 'python3':
        code = f'import math\ndef main() -> dict:\n    return {{"result": "{marker}", "value": int(math.sqrt(1764))}}\n'
        comment = '# NATIVE_EDITOR_VERIFIED'
    else:
        code = f'function main() {{\n  return {{result: "{marker}", value: 6 * 7}};\n}}\n'
        comment = '// NATIVE_EDITOR_VERIFIED'
    def node(identifier, kind, title, x, extra):
        return {'id': identifier, 'type': 'custom', 'width': 244, 'height': 90,
            'position': {'x': x, 'y': 200}, 'positionAbsolute': {'x': x, 'y': 200},
            'sourcePosition': 'right', 'targetPosition': 'left', 'selected': False,
            'data': {'type': kind, 'title': title, 'desc': '', 'selected': False, **extra}}
    nodes = [node('start', 'start', 'Start', 30, {'variables': []}),
        node('code', 'code', 'Python Code' if language == 'python3' else 'JavaScript Code', 340,
            {'code_language': language, 'code': code, 'variables': [], 'outputs': {
                'result': {'type': 'string', 'children': None}, 'value': {'type': 'number', 'children': None}}}),
        node('end', 'end', 'End', 650, {'outputs': [
            {'value_selector': ['code', name], 'value_type': kind, 'variable': name}
            for name, kind in [('result', 'string'), ('value', 'number')]]})]
    edges = [{'id': source + '-source-' + target + '-target', 'source': source, 'target': target,
        'sourceHandle': 'source', 'targetHandle': 'target', 'type': 'custom', 'zIndex': 0,
        'data': {'sourceType': kind, 'targetType': target_kind, 'isInIteration': False, 'isInLoop': False}}
        for source, target, kind, target_kind in [('start', 'code', 'start', 'code'), ('code', 'end', 'code', 'end')]]
    return {'language': language, 'marker': marker, 'code': code, 'edited_code': code + '\n' + comment,
        'editor_comment': comment, 'graph': {'nodes': nodes, 'edges': edges, 'viewport': {'x': 30, 'y': 40, 'zoom': 0.8}},
        'features': {'file_upload': {'enabled': False}, 'opening_statement': '', 'suggested_questions': [],
            'suggested_questions_after_answer': {'enabled': False}, 'speech_to_text': {'enabled': False},
            'text_to_speech': {'enabled': False}, 'retriever_resource': {'enabled': False},
            'sensitive_word_avoidance': {'enabled': False}}}


def source_hashes() -> dict:
    source = REPO / '.local/native_windows/sources/dify'
    return {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in PROTOCOL_FILES}


def ownership_script(expected_id: str, hashes: dict) -> str:
    """子进程仅核文件/进程/监听和白名单配置，不读数据库、不把完整环境回传Windows。"""
    return '''import hashlib,json,pathlib,sys
sys.path.insert(0, '/mnt/e/code/Yun_tian/new_agent/ai_agent_company/deploy/native')
import wsl_runtime as runtime
runtime.check_root()
data=runtime.load()
if data['id'] != EXPECTED or data['boot_id'] != runtime.boot_identity() or not data['databases_initialized']:
    raise RuntimeError('dify_runtime_not_initialized_or_boot_changed')
for name, sha in HASHES.items():
    if runtime.file_sha(runtime.ROOT / 'sources/dify' / name) != sha:
        raise RuntimeError('dify_protocol_source_changed')
required=set(runtime.ORDER)
if set(data['processes']) != required:
    raise RuntimeError('dify_native_process_set_incomplete')
proof=[]
for role in runtime.ORDER:
    info=data['processes'][role]
    candidate=runtime.owned_process(info,data)
    if not candidate or candidate.uids().real != info['service_uid']:
        raise RuntimeError('dify_process_ownership_changed')
    if role in ('postgres','gateway','api','redis','plugin','web'):
        expected=runtime.PORTS['web' if role=='gateway' else 'web_node' if role=='web' else role]
        listening=[c for process in [candidate,*candidate.children(recursive=True)] for c in process.net_connections(kind='tcp')
            if c.status=='LISTEN' and c.laddr.port==expected and c.laddr.ip=='127.0.0.1']
        if not listening: raise RuntimeError('dify_owned_listener_missing')
    if role in ('api','plugin','worker','beat'):
        env=candidate.environ()
        expected_env={'DB_HOST':'127.0.0.1','DB_PORT':'25432','DB_DATABASE':'dify_plugin' if role=='plugin' else 'dify',
            'REDIS_HOST':'127.0.0.1','REDIS_PORT':'26379'}
        if any(env.get(k)!=v for k,v in expected_env.items()): raise RuntimeError('dify_database_binding_changed')
        if env.get('DATABASE_URL') or env.get('PGHOST') or env.get('REDIS_URL'): raise RuntimeError('dify_inherited_database_override')
        if role=='api' and (env.get('VECTOR_STORE')!='qdrant' or env.get('QDRANT_URL')!='http://127.0.0.1:26333'):
            raise RuntimeError('dify_vector_binding_changed')
    if role=='postgres':
        if candidate.exe()!='/usr/lib/postgresql/16/bin/postgres' or candidate.cmdline()[1:]!=['-D','/opt/ai-agent-company/data/postgres']:
            raise RuntimeError('dify_postgres_datadir_changed')
    proof.append({'role':role,'pid':info['pid'],'created_at':info['created_at'],'launch_id':info['launch_id']})
if (runtime.ROOT/'data/postgres/PG_VERSION').read_text().strip()!='16': raise RuntimeError('dify_postgres_version_changed')
print(json.dumps({'verified':True,'runtime_id':data['id'],'boot_id':data['boot_id'],'root':str(runtime.ROOT),
    'distro':runtime.DISTRO,'origin':'http://127.0.0.1:13001','postgres_data':'/opt/ai-agent-company/data/postgres',
    'postgres_port':25432,'databases':['dify','dify_plugin'],'processes':proof,'protocol_hashes':HASHES}))
'''.replace('EXPECTED', repr(expected_id)).replace('HASHES', repr(hashes))


def check_ownership(expected_id: str, remaining: float) -> dict:
    windows, wsl = modules()
    wsl.require_registered_distro()
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'
    result = subprocess.run([str(executable), '--distribution', 'ai-agent-company', '--user', 'root', '--exec',
        '/opt/ai-agent-company/tooling/bin/python', '-c', ownership_script(expected_id, source_hashes())],
        check=True, timeout=max(0.1, min(20, remaining)), capture_output=True, env=wsl.clean_environment())
    proof = json.loads(result.stdout)
    if proof.get('verified') is not True or proof.get('runtime_id') != expected_id or proof.get('origin') != ORIGIN:
        raise VerificationError('dify_ownership_proof_invalid')
    return proof


class ConsoleSession:
    """真实Cookie和逐次CSRF；HTTP无代理、重定向或自动重试，错误不输出原响应。"""
    def __init__(self, ledger, run_id, deadline, transport=None, initial_attempts=0):
        self.ledger, self.run_id, self.deadline = ledger, run_id, deadline
        self.attempts = initial_attempts
        self.transport = transport
        self.client = httpx.Client(base_url=ORIGIN, trust_env=False, follow_redirects=False,
            transport=transport or httpx.HTTPTransport(retries=0), headers={'User-Agent': 'AgentPlatform-DifyLocalAcceptance/1.0'})

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise VerificationError('verification_total_deadline')
        return seconds

    def request(self, method, path, *, body=None, expected=(200,)):
        if not path.startswith('/console/api/') or path.startswith('//') or '://' in path:
            raise VerificationError('unexpected_console_endpoint')
        if self.attempts >= MAX_HTTP:
            raise VerificationError('business_http_budget_exceeded')
        headers = {'Origin': ORIGIN}
        csrf = self.client.cookies.get('csrf_token')
        if method != 'GET':
            if path not in ('/console/api/setup', '/console/api/login') and not csrf:
                raise VerificationError('console_csrf_cookie_missing')
        # Dify普通Console GET也检查CSRF，draft白名单不能推广成所有读取均豁免。
        if csrf: headers['X-CSRF-Token'] = csrf
        self.attempts += 1
        self.ledger.update(self.run_id, lambda r: r.update(http_attempts=self.attempts, phase=method + ':' + path))
        async def bounded_request():
            # HTTPX分段读取超时不是总时限；外层wait_for同时限制整次请求和全部响应字节。
            async with httpx.AsyncClient(base_url=ORIGIN, trust_env=False, follow_redirects=False,
                    transport=self.transport or httpx.AsyncHTTPTransport(retries=0), cookies=self.client.cookies,
                    headers=self.client.headers, timeout=min(20, self.remaining())) as client:
                async with client.stream(method, path, json=body, headers=headers) as streamed:
                    content = bytearray()
                    async for chunk in streamed.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2 * 1024 * 1024:
                            raise VerificationError('console_response_budget_exceeded')
                    self.client.cookies.update(client.cookies)
                    return httpx.Response(streamed.status_code, headers=streamed.headers, content=bytes(content), request=streamed.request)
        async def request_with_deadline():
            seconds = min(20, self.remaining())
            return await asyncio.wait_for(bounded_request(), timeout=seconds)
        response = asyncio.run(request_with_deadline())
        if response.status_code not in expected:
            raise VerificationError('console_status_' + str(response.status_code))
        if len(response.content) > 2 * 1024 * 1024:
            raise VerificationError('console_response_budget_exceeded')
        self.ledger.update(self.run_id, lambda r: r.update(cookies=[{'name': c.name, 'value': c.value,
            'domain': c.domain, 'path': c.path} for c in self.client.cookies.jar]))
        return response

    def json(self, method, path, **kwargs):
        return self.request(method, path, **kwargs).json()


def prepare_console(session: ConsoleSession, ledger: DifyLedger, run_id: str) -> list[dict]:
    record = ledger.record(run_id)
    init = session.json('GET', '/console/api/init')
    if init.get('status') != 'finished':
        raise VerificationError('unexpected_native_init_password_required')
    setup = session.json('GET', '/console/api/setup')
    if setup.get('step') != 'not_started':
        raise VerificationError('dify_database_not_fresh_setup')
    account = record['account']
    ledger.update(run_id, lambda r: r.update(setup_attempted=True))
    result = session.json('POST', '/console/api/setup', body={**account, 'language': 'en-US'}, expected=(201,))
    if result.get('result') != 'success':
        raise VerificationError('native_setup_not_successful')
    ledger.update(run_id, lambda r: r.update(setup_succeeded=True))
    login = session.json('POST', '/console/api/login', body={'email': account['email'],
        'password': base64.b64encode(account['password'].encode()).decode(), 'remember_me': False})
    if login.get('result') != 'success' or not session.client.cookies.get('access_token') or not session.client.cookies.get('csrf_token'):
        raise VerificationError('native_cookie_login_not_successful')
    flows = []
    for language in ('python3', 'javascript'):
        plan = workflow_plan(language, run_id)
        ledger.update(run_id, lambda r: r['flows'].update({language: {'create_attempted': True}}))
        app = session.json('POST', '/console/api/apps', body={'name': 'Native ' + language + ' ' + run_id,
            'mode': 'workflow', 'description': '本机原生代码节点验收'}, expected=(201,))
        try: app_id = str(UUID(app['id']))
        except (KeyError, ValueError, TypeError): raise VerificationError('created_app_id_invalid') from None
        ledger.update(run_id, lambda r: r['flows'][language].update(app_id=app_id))
        path = '/console/api/apps/' + app_id + '/workflows/draft'
        previous = session.request('GET', path, expected=(200, 404))
        old_hash = previous.json().get('hash') if previous.status_code == 200 else None
        body = {'graph': plan['graph'], 'features': plan['features'], 'hash': old_hash,
            'force': False, '_is_collaborative': False, 'conversation_variables': []}
        sync = session.json('POST', path, body=body)
        if sync.get('result') != 'success' or not isinstance(sync.get('hash'), str):
            raise VerificationError('native_draft_not_saved')
        ledger.update(run_id, lambda r: r['flows'][language].update(draft_saved=True, graph_sha256=digest(plan['graph'])))
        flows.append({**plan, 'app_id': app_id})
    return flows


def run_browser(ledger: DifyLedger, run_id: str, flows: list[dict], attempts: int, deadline: float) -> dict:
    windows, _ = modules()
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise VerificationError('verification_total_deadline')
    recovery = ledger.record(run_id).get('browser_recovery')
    output_name = run_id + '-browser-recovery-' + recovery['id'] if recovery else run_id
    output = checked_path(ledger.root / output_name, ledger.root)
    output.mkdir(exist_ok=False)
    payload = {'origin': ORIGIN, 'run_id': run_id, 'flows': flows, 'account': ledger.record(run_id)['account'],
        'business_used': attempts, 'max_business': MAX_HTTP, 'max_static': MAX_STATIC,
        'deadline_unix_ms': int((time.time() + remaining) * 1000), 'output': str(output),
        'python': str(REPO / '.venv/Scripts/python.exe'), 'verifier': str(Path(__file__).resolve())}
    # 账号只交本次子进程环境；不写命令参数、明文文件、storageState或浏览器trace。
    env = {**windows.base_environment(), 'DIFY_VERIFY_INPUT': json.dumps(payload, ensure_ascii=False)}
    process = None
    created_at = executable = None
    try:
        process = subprocess.Popen([str(windows.LOCAL_BINARIES['node24']), str(REPO / 'apps/web/tests/dify-wsl.mjs')],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO / 'apps/web')
        identity = psutil.Process(process.pid)
        created_at, executable = identity.create_time(), identity.exe()
        ledger.update(run_id, lambda r: r.update(browser_process={'pid': process.pid,
            'created_at': created_at, 'executable': executable}))
        process.communicate(timeout=max(0.1, remaining))
    except BaseException:
        if process and created_at is not None:
            stop_browser_tree(process, created_at, executable)
        elif process and process.poll() is None:
            # Windows的Popen句柄绑定实际创建的进程；未登记出生时间时只回收该句柄，不猜子进程。
            process.kill()
            process.wait(timeout=3)
        raise
    receipt_path = output / 'receipt.json'
    if not receipt_path.is_file():
        raise VerificationError('native_browser_receipt_missing')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if process.returncode != 0 or receipt.get('passed') is not True:
        raise VerificationError('native_browser_' + str(receipt.get('error_code', 'failed')))
    if receipt.get('model_requests') != 0 or receipt.get('workflow_requests') != len(flows) or receipt.get('business_http', MAX_HTTP + 1) > MAX_HTTP:
        raise VerificationError('native_browser_budget_receipt_invalid')
    return receipt


def recover_browser_flows(session: ConsoleSession, ledger: DifyLedger, run_id: str) -> list[dict]:
    """每app仅一次只读DSL导出，同时核mode和原graph；不登录、创建或写回草稿。"""
    record = ledger.record(run_id)
    flows = []
    for language in ('python3', 'javascript'):
        previous = record['flows'][language]
        app_id = previous['app_id']
        result = session.json('GET', '/console/api/apps/' + app_id + '/export?format=yaml&include_secret=false')
        try:
            exported = yaml.safe_load(result['data'])
        except (KeyError, TypeError, yaml.YAMLError):
            raise VerificationError('browser_recovery_native_export_invalid') from None
        if not isinstance(exported, dict) or exported.get('kind') != 'app' or exported.get('app', {}).get('mode') != 'workflow':
            raise VerificationError('browser_recovery_app_mode_changed')
        workflow = exported.get('workflow', {})
        plan = workflow_plan(language, run_id)
        if not isinstance(workflow, dict) or digest(workflow.get('graph', {})) != previous['graph_sha256']:
            raise VerificationError('browser_recovery_remote_graph_changed')
        if exported.get('dependencies') or workflow.get('environment_variables') or workflow.get('conversation_variables'):
            raise VerificationError('browser_recovery_remote_inputs_changed')
        if any(isinstance(value, dict) and value.get('enabled') is True for value in workflow.get('features', {}).values()):
            raise VerificationError('browser_recovery_remote_features_changed')
        flows.append({**plan, 'app_id': app_id})
    ledger.update(run_id, lambda r: r['browser_recovery'].update(exports_verified=True))
    return flows


def recover_browser(ledger: DifyLedger, run_id: str, reviewed_failed_run_id: str, runtime_id: str) -> dict:
    """一次明确受审的浏览器续作，保留原10请求计数与失败；失败后不能再次续作。"""
    deadline = time.monotonic() + TOTAL_SECONDS
    record = ledger.claim_browser_recovery(run_id, reviewed_failed_run_id, runtime_id)
    session = None
    try:
        proof = check_ownership(runtime_id, deadline - time.monotonic())
        original = deepcopy(record['ownership'])
        current = deepcopy(proof)
        # 协调者已明确审阅修复并重启独占code服务；其余角色和数据/协议边界必须仍是原实例。
        original_code = [value for value in original.pop('processes', []) if value['role'] == 'code']
        current_code = [value for value in current.pop('processes', []) if value['role'] == 'code']
        original_other = [value for value in record['ownership'].get('processes', []) if value['role'] != 'code']
        current_other = [value for value in proof.get('processes', []) if value['role'] != 'code']
        if current != original or current_other != original_other or len(current_code) != 1 or len(original_code) != 1:
            raise VerificationError('browser_recovery_runtime_launch_changed')
        ledger.update(run_id, lambda r: r['browser_recovery'].update(ownership=proof,
            reviewed_code_restart=current_code != original_code))
        session = ConsoleSession(ledger, run_id, deadline, initial_attempts=record['http_attempts'])
        for cookie in record.get('cookies', []):
            session.client.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
        if not session.client.cookies.get('access_token') or not session.client.cookies.get('csrf_token'):
            raise VerificationError('browser_recovery_original_cookies_missing')
        flows = recover_browser_flows(session, ledger, run_id)
        receipt = run_browser(ledger, run_id, flows, session.attempts, deadline)
        if check_ownership(runtime_id, deadline - time.monotonic()) != proof:
            raise VerificationError('native_process_changed_during_verification')
        ledger.update(run_id, lambda r: (r.update(status='passed', phase='complete', finished_at=now(), receipt=receipt),
            r['browser_recovery'].update(status='passed', finished_at=now())))
        return {'passed': True, 'run_id': run_id, 'browser_recovery': True, 'native_ui': True, 'workflow_runs': 2,
            'model_requests': 0, 'business_http': receipt['business_http'], 'static_requests': receipt['static_requests'],
            'screenshots': receipt['screenshots'], 'credential_storage': str(ledger.path), 'full_native_verified': False}
    except BaseException as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        ledger.update(run_id, lambda r: (r.update(status='failed', error_code=code, finished_at=now()),
            r['browser_recovery'].update(status='failed', error_code=code, finished_at=now())))
        raise
    finally:
        if session: session.client.close()


def stop_browser_tree(process, created_at: float, executable: str) -> None:
    """只清理本次Popen仍可核实的浏览器树；不按进程名停止用户Chrome或WSL服务。"""
    try:
        parent = psutil.Process(process.pid)
        if parent.create_time() != created_at or parent.exe() != executable:
            raise VerificationError('browser_cleanup_identity_changed')
        descendants = parent.children(recursive=True)
        for candidate in reversed(descendants):
            if candidate.create_time() >= created_at:
                try: candidate.kill()
                except psutil.NoSuchProcess: pass
        parent.kill()
        _, alive = psutil.wait_procs([*descendants, parent], timeout=3)
        if alive:
            raise VerificationError('browser_cleanup_incomplete')
    except psutil.NoSuchProcess:
        # 正常退出已关闭浏览器；不能在父PID消失后根据进程名猜测归属。
        return


def verify(ledger: DifyLedger, run_id: str) -> dict:
    deadline = time.monotonic() + TOTAL_SECONDS
    record = ledger.claim(run_id)
    session = None
    try:
        proof = check_ownership(record['runtime_id'], deadline - time.monotonic())
        ledger.update(run_id, lambda r: r.update(ownership=proof))
        session = ConsoleSession(ledger, run_id, deadline)
        flows = prepare_console(session, ledger, run_id)
        receipt = run_browser(ledger, run_id, flows, session.attempts, deadline)
        final_proof = check_ownership(record['runtime_id'], deadline - time.monotonic())
        if final_proof != proof:
            raise VerificationError('native_process_changed_during_verification')
        ledger.update(run_id, lambda r: r.update(status='passed', phase='complete', finished_at=now(), receipt=receipt))
        return {'passed': True, 'run_id': run_id, 'native_ui': True, 'workflow_runs': 2, 'model_requests': 0,
            'business_http': receipt['business_http'], 'static_requests': receipt['static_requests'],
            'screenshots': receipt['screenshots'], 'credential_storage': str(ledger.path),
            'full_native_verified': False}
    except BaseException as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        ledger.update(run_id, lambda r: r.update(status='failed', error_code=code, finished_at=now()))
        raise
    finally:
        if session: session.client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'verify', 'checkpoint', 'recover-browser'])
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--expected-runtime-id')
    parser.add_argument('--language', choices=['python3', 'javascript'])
    parser.add_argument('--app-id')
    parser.add_argument('--request-sha256')
    parser.add_argument('--reviewed-failed-run-id')
    args = parser.parse_args(argv)
    try:
        ledger = DifyLedger()
        if args.action == 'prepare':
            if not args.expected_runtime_id: parser.error('prepare必须指定已核实runtime ID')
            ledger.prepare(args.run_id, args.expected_runtime_id)
            result = {'prepared': True, 'run_id': args.run_id, 'services_started': False,
                'http_requests': 0, 'model_requests': 0, 'credential_storage': str(ledger.path)}
        elif args.action == 'checkpoint':
            ledger.checkpoint(args.run_id, args.language, args.app_id, args.request_sha256)
            result = {'checkpoint_saved': True}
        elif args.action == 'recover-browser':
            if not args.expected_runtime_id or not args.reviewed_failed_run_id:
                parser.error('浏览器续作必须指定已审阅failed run ID与同一runtime ID')
            result = recover_browser(ledger, args.run_id, args.reviewed_failed_run_id, args.expected_runtime_id)
        else:
            result = verify(ledger, args.run_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error) if isinstance(error, VerificationError)
            else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
