"""仅验本次D盘Docker OWUI；复用14 HTTP/唯一模型/密文不可重放检查点。"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import sys
from tempfile import NamedTemporaryFile
import time

import httpx


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.verify_native_api import CredentialLedger, MAX_HTTP, RSS_LIMIT, TOTAL_SECONDS, Verifier, VerificationError


DEFAULT_ROOT = Path('D:/Docker/agent-platform/openwebui-20261020-01')
RUN_ID = 'docker-owui-20261020-01'
ENGINE = 'npipe:////./pipe/dockerDesktopLinuxEngine'
BASE_URL = 'http://127.0.0.1:13080'
TOTAL_BUSINESS_HTTP = 20
INSPECT_INTERVAL = 1.0
CLEANUP_SECONDS = 5.0


def windows_path(value):
    """比较固定Windows路径，不把当前工作目录或相对路径解释为获准D盘根。"""
    return PureWindowsPath(str(value)).as_posix().rstrip('/').casefold()


def validate_root(root):
    if windows_path(root) != windows_path(DEFAULT_ROOT):
        raise VerificationError('docker_runtime_root_not_authorized')


def docker_module():
    """延迟加载同任务部署守卫；导入本身不执行Docker或读取凭据。"""
    from deploy.docker import openwebui
    return openwebui


class DockerGuard:
    """只接受部署模块核验的当前容器，按硬cgroup上限和OOM状态保护本次验收。"""

    def __init__(self, root, runtime, deadline):
        validate_root(root)
        self.root, self.runtime, self.deadline = root, runtime, deadline
        self.initial = None
        self.maximum_rss = None
        self.stop_result = None
        self.frontend_available = None
        self.frontend_expected = False
        self.last_inspected_at = None
        self.inspection = None
        self.checked_at = None
        self.host_resource_failure = False

    def inspect(self):
        """初始化和周期复查共用真实inspect；部署模块负责标签/挂载/镜像原始归属校验。"""
        if self.host_resource_failure:
            raise VerificationError('docker_host_resource_check_failed')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError('docker_guard_deadline')
        try:
            manifest = self.runtime.load(self.root)
            self._validate_manifest(manifest)
        except Exception as error:
            raise VerificationError('docker_ownership_inspection_failed') from error
        candidate = {'runtime_id': manifest['id'], 'container_id': manifest['container']['id'], 'image_id': manifest['image']['id']}
        try:
            resources = self.runtime.check_host_resources()
            # 系统读取失败和缺失快照同样不能继续，不能把未知余量当作达到门限。
            if not isinstance(resources, dict) or any(type(resources.get(key)) is not int or resources[key] < minimum for key, minimum in (('available_bytes', 2 * RSS_LIMIT), ('commit_free_bytes', 3 * RSS_LIMIT))):
                raise VerificationError('docker_host_resource_snapshot_invalid')
        except Exception as error:
            self.host_resource_failure = True
            self.stop_owned('docker_host_resource_check_failed', _candidate=candidate)
            raise VerificationError('docker_host_resource_check_failed') from error
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise VerificationError('docker_guard_deadline')
            inspected = self.runtime.inspect_owned(self.root, timeout_seconds=min(3, remaining))
        except Exception as error:
            # 严格inspect也可能因资源漂移失败；独立CAS止损仍会重新核完整身份，不把候选当已验证。
            self.stop_owned('docker_ownership_inspection_failed', _candidate=candidate)
            raise VerificationError('docker_ownership_inspection_failed') from error
        self._validate(manifest, inspected)
        self.inspection = deepcopy(inspected)
        self.last_inspected_at = time.monotonic()
        self.checked_at = datetime.now(timezone.utc).isoformat()
        return {'id': inspected['runtime_id']}

    def _validate_manifest(self, manifest):
        container, image = manifest.get('container') or {}, manifest.get('image') or {}
        if manifest.get('kind') != 'manufacturing-openwebui-docker' or manifest.get('engine') != ENGINE or windows_path(manifest.get('root', '')) != windows_path(self.root) or not manifest.get('id') or not container.get('id') or not container.get('name') or not image.get('id') or not image.get('repo_digest'):
            raise VerificationError('docker_runtime_manifest_mismatch')

    def _validate(self, manifest, inspected):
        container = manifest.get('container') or {}
        image = manifest.get('image') or {}
        if not inspected.get('ownership_verified') or inspected.get('engine') != ENGINE or inspected.get('runtime_id') != manifest.get('id') or windows_path(inspected.get('root', '')) != windows_path(self.root):
            raise VerificationError('docker_ownership_not_verified')
        if not container.get('id') or inspected.get('container_id') != container.get('id') or inspected.get('container_name') != container.get('name') or not image.get('id') or inspected.get('image_id') != image.get('id') or not image.get('repo_digest') or inspected.get('image_digest') != image.get('repo_digest'):
            raise VerificationError('docker_container_image_mismatch')
        if inspected.get('host') != '127.0.0.1' or inspected.get('port') != 13080 or inspected.get('internal_port') != 8080:
            raise VerificationError('docker_loopback_binding_mismatch')
        data = self.root / 'data'
        if windows_path(inspected.get('data_directory', '')) != windows_path(data) or windows_path(inspected.get('sqlite_file', '')) != windows_path(data / 'webui.db'):
            raise VerificationError('docker_storage_binding_mismatch')
        identity = {key: inspected.get(key) for key in ('runtime_id', 'container_id', 'container_name', 'image_id', 'image_digest', 'data_directory', 'sqlite_file')}
        if self.initial is not None and identity != self.initial:
            raise VerificationError('docker_container_changed')
        if self.initial is None:
            self.initial = identity
        limits = inspected.get('limits') or {}
        if limits.get('memory_bytes') != RSS_LIMIT or limits.get('memory_swap_bytes') != RSS_LIMIT or limits.get('nano_cpus') != 1_000_000_000 or limits.get('pids_limit') != 256 or limits.get('restart_policy') != 'no' or limits.get('healthcheck_disabled') is not True or limits.get('privileged') is not False or limits.get('cap_drop') != ['ALL']:
            self.stop_owned('docker_resource_limit_mismatch')
            raise VerificationError('docker_resource_limit_mismatch')
        state = inspected.get('state') or {}
        if state.get('oom_killed'):
            self.stop_owned('docker_oom_killed')
            raise VerificationError('docker_oom_killed')
        if state.get('oom_killed') is not False or state.get('running') is not True or state.get('status') != 'running' or state.get('dead') is not False or state.get('error_present') is not False or inspected.get('ready') is not True:
            self.stop_owned('docker_container_not_running')
            raise VerificationError('docker_container_not_running')
        # 静态前端是否包含在锁定镜像中由部署契约给出；不能据此声称浏览器已验收。
        self.frontend_expected = manifest.get('frontend_expected') is True

    def sample(self):
        """watch每250ms调用时最多每秒inspect一次，不运行docker stats或额外健康HTTP。"""
        if self.host_resource_failure:
            raise VerificationError('docker_host_resource_check_failed')
        if self.initial is None:
            raise VerificationError('docker_guard_not_initialized')
        if time.monotonic() >= self.deadline:
            raise VerificationError('docker_guard_deadline')
        if self.last_inspected_at is None or time.monotonic() - self.last_inspected_at >= INSPECT_INTERVAL:
            self.inspect()

    def stop_owned(self, reason, *, _candidate=None):
        """独立最多5秒核归属并停止一次；验证截止不取消清理，也不增加业务请求预算。"""
        if self.stop_result is not None:
            return
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        self.stop_result = {'verified': False, 'reason': 'stop_not_completed'}
        identity = self.initial or _candidate
        if identity is None:
            self.stop_result = {'verified': False, 'reason': 'no_verified_container_no_container_touched'}
            return
        try:
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                self.stop_result = {'verified': False, 'reason': 'docker_cleanup_deadline_elapsed'}
                return
            manifest = self.runtime.load(self.root)
            container = manifest.get('container') or {}
            if manifest.get('id') != identity['runtime_id'] or container.get('id') != identity['container_id'] or (manifest.get('image') or {}).get('id') != identity['image_id']:
                self.stop_result = {'verified': False, 'reason': 'container_changed_no_container_touched'}
                return
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                self.stop_result = {'verified': False, 'reason': 'docker_cleanup_deadline_elapsed'}
                return
            result = self.runtime.stop_owned(self.root, reason, expected_container_id=identity['container_id'], expected_runtime_id=identity['runtime_id'], timeout_seconds=remaining)
            if not isinstance(result, dict):
                raise VerificationError('docker_stop_result_invalid')
            # 只保留安全状态字段，不能把Docker环境或工具原始错误拼入输出。
            self.stop_result = {key: deepcopy(result[key]) for key in ('verified', 'stopped', 'reason', 'container_id', 'running', 'status', 'error_type') if key in result}
            if self.stop_result.get('verified') is not True:
                self.stop_result['verified'] = False
        except Exception as error:
            self.stop_result = {'verified': False, 'reason': 'docker_stop_failed', 'error_type': type(error).__name__}


class DockerVerifier(Verifier):
    """协议和检查点完整继承；只替换已核实Docker容器的回环URL。"""

    async def http_workflow(self):
        transport_a = self.transport or httpx.AsyncHTTPTransport(retries=0)
        transport_b = self.transport or httpx.AsyncHTTPTransport(retries=0)
        options = {'base_url': BASE_URL, 'trust_env': False, 'follow_redirects': False,
                   'timeout': httpx.Timeout(5, connect=3), 'limits': httpx.Limits(max_connections=1)}
        async with httpx.AsyncClient(transport=transport_a, **options) as admin, httpx.AsyncClient(transport=transport_b, **options) as user:
            await self.workflow(admin, user)


def acceptance_result(verifier, result):
    """结果只暴露预算和归属摘要；实际账号、令牌和消息引用继续仅存密文。"""
    guard = verifier.guard
    output = deepcopy(result)
    if result.get('error_code') == 'actual_run_id_already_attempted':
        # 已尝试运行只读其预算，不能用本次0次派发覆盖原先已消耗的额度或改写旧检查点。
        previous = verifier.ledger.read().get(verifier.ledger.namespace, {}).get(verifier.run_id, {})
        output.update(http_attempts=previous.get('http_attempts', 0),
                      model_calls=previous.get('model_calls', int(bool(previous.get('model_attempted')))),
                      previous_attempt_exists=True)
    identity = guard.initial or {}
    output.update({key: identity.get(key) for key in ('runtime_id', 'container_id', 'image_id', 'image_digest')})
    output.update(transport_scope='docker_loopback', base_url=BASE_URL, origin=BASE_URL,
                  business_http_budget=TOTAL_BUSINESS_HTTP, http_budget=MAX_HTTP,
                  remaining_business_http=max(0, TOTAL_BUSINESS_HTTP - output['http_attempts']),
                  memory_limit_bytes=RSS_LIMIT, nano_cpus=1_000_000_000,
                  maximum_rss_bytes=guard.maximum_rss, rss_measurement='not_measured',
                  ownership_verified=guard.initial is not None,
                  frontend_available=guard.frontend_available,
                  frontend_expected=guard.frontend_expected,
                  frontend_available_evidence='image_static_contract_expected' if guard.frontend_expected else 'unconfirmed',
                  native_ui_observed=False, full_native_acceptance=False,
                  source_equivalence_verified=(guard.inspection or {}).get('source_equivalence_verified') is True,
                  checked_at=guard.checked_at,
                  verification_started_at=getattr(verifier, 'verification_started_at', None),
                  captured_at=datetime.now(timezone.utc).isoformat())
    started = output['verification_started_at']
    try:
        start_time = datetime.fromisoformat(started) if isinstance(started, str) else None
        within_budget = start_time is not None and start_time.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - start_time).total_seconds() < TOTAL_SECONDS
    except ValueError:
        within_budget = False
    output['ui_eligible'] = result['status'] == 'succeeded' and result.get('checkpoint_error_type') is None and result.get('stop_result') is None and output['ownership_verified'] and output['frontend_expected'] and within_budget
    return output


def save_acceptance(root, runtime, result):
    """首次结果原子落盘，禁止重复运行把实际已用HTTP次数覆盖成0。"""
    path = runtime.ensure_inside(root, root / 'acceptance.json')
    temporary = None
    with runtime.runtime_lock(root):
        if path.exists():
            raise VerificationError('acceptance_result_already_exists')
        try:
            with NamedTemporaryFile(dir=root, suffix='.acceptance.pending', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode())
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='本次D盘Docker OWUI有限验收；14 HTTP/唯一64token/60秒，无重试')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = {'actual_run_id': args.run_id, 'status': 'failed', 'phase': 'preflight', 'http_attempts': 0, 'model_calls': 0}
    verifier = None
    try:
        validate_root(args.root)
        if args.run_id != RUN_ID:
            raise VerificationError('docker_run_id_not_authorized')
        runtime = docker_module()
        runtime.load(args.root)
        if runtime.ensure_inside(args.root, args.root / 'acceptance.json').exists():
            raise VerificationError('acceptance_result_already_exists')
        guard = DockerGuard(args.root, runtime, time.monotonic() + TOTAL_SECONDS)
        ledger = CredentialLedger(args.root, runtime)
        verifier = DockerVerifier(args.root, args.run_id, runtime, None, guard=guard, ledger=ledger)
        verifier.verification_started_at = datetime.now(timezone.utc).isoformat()
        guard.deadline = verifier.deadline
        result = asyncio.run(verifier.execute())
        result = acceptance_result(verifier, result)
        save_acceptance(args.root, runtime, result)
    except Exception as error:
        if verifier is not None and result.get('status') == 'succeeded':
            verifier.guard.stop_owned('docker_acceptance_record_failed')
            result.update(status='unknown', stop_result=verifier.guard.stop_result, ui_eligible=False)
        if verifier is not None and verifier.http_attempts:
            result['acceptance_error_type'] = type(error).__name__
        code = str(error) if isinstance(error, VerificationError) else 'docker_acceptance_record_failed' if verifier is not None and verifier.http_attempts else 'docker_verifier_preflight_failed'
        result.update(error_type=type(error).__name__, error_code=result.get('error_code') or code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'succeeded' else 1)


if __name__ == '__main__':
    main()
