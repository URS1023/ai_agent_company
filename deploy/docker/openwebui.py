"""单次、独立的本机Docker OWUI控制器；无构建、无自动重试、无业务HTTP探针。

prepare只锁定候选平台清单并加密配置；pull只拉取已锁定摘要；start只运行本轮新容器。
停止保留容器、数据和故障证据。所有公开输出均投影安全字段，不读取容器Env或原始日志。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import psutil

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path('D:/Docker/agent-platform/openwebui-20261020-01')
DOCKER_DIRECTORY = Path('D:/Docker/Desktop')
DOCKER = DOCKER_DIRECTORY / 'resources/bin/docker.exe'
ENGINE = 'npipe:////./pipe/dockerDesktopLinuxEngine'
INSTALL_SETTINGS = Path('C:/ProgramData/DockerDesktop/install-settings.json')
WSL_DATA_ROOT = Path('D:/Docker/wsl-data')
IMAGE = 'ghcr.io/open-webui/open-webui:v0.12.3'
IMAGE_REPOSITORY = 'ghcr.io/open-webui/open-webui'
PROJECT = 'manufacturing-agent-platform'
NAME = 'manufacturing-openwebui-20261020-01'
KIND = 'manufacturing-openwebui-docker'
GIB = 1024 ** 3
MEMORY = GIB
NANO_CPUS = 1_000_000_000
PIDS = 256
PORT = 13080
START_SECONDS = 180
PULL_SECONDS = 900
PREPARE_SECONDS = 90
MIN_DISK = 12 * GIB
MANIFEST = 'runtime.json'
LABEL_OWNER = 'io.manufacturing.owner'
LABEL_RUNTIME = 'io.manufacturing.runtime-id'
LABEL_PROJECT = 'io.manufacturing.project'
OWNER = 'agent-1020-native'
DIGEST_RE = re.compile(r'sha256:[0-9a-f]{64}\Z')
CID_RE = re.compile(r'[0-9a-f]{64}\Z')

# 不请求Config.Env；即使第三方把密钥写入容器环境，本控制器也不采集它。
CONTAINER_FORMAT = ('{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},'
    '"Created":{{json .Created}},"Labels":{{json .Config.Labels}},'
    '"Entrypoint":{{json .Config.Entrypoint}},"Cmd":{{json .Config.Cmd}},'
    '"Healthcheck":{{json .Config.Healthcheck}},"Mounts":{{json .Mounts}},'
    '"State":{{json .State}},"HostConfig":{"Memory":{{json .HostConfig.Memory}},'
    '"MemorySwap":{{json .HostConfig.MemorySwap}},"NanoCpus":{{json .HostConfig.NanoCpus}},'
    '"PidsLimit":{{json .HostConfig.PidsLimit}},"RestartPolicy":{{json .HostConfig.RestartPolicy}},'
    '"Privileged":{{json .HostConfig.Privileged}},"CapDrop":{{json .HostConfig.CapDrop}},'
    '"CapAdd":{{json .HostConfig.CapAdd}},"SecurityOpt":{{json .HostConfig.SecurityOpt}},'
    '"PortBindings":{{json .HostConfig.PortBindings}},"NetworkMode":{{json .HostConfig.NetworkMode}}},'
    '"Ports":{{json .NetworkSettings.Ports}}}')
IMAGE_FORMAT = ('{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},'
    '"Architecture":{{json .Architecture}},"Os":{{json .Os}},"Size":{{json .Size}}}')


class RuntimeBoundaryError(RuntimeError):
    """只携带非敏感的固定错误码。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_reparse(path: Path) -> None:
    """逐层lstat检查既有祖先，不沿junction/symlink把新运行目录指向旧数据。"""
    for part in [path, *path.parents]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeBoundaryError('runtime_reparse_path_rejected')


def _root(root: Path) -> Path:
    _reject_reparse(Path(root).absolute())
    root = Path(root).resolve()
    if root != DEFAULT_ROOT.resolve() or str(root).startswith('\\\\'):
        raise RuntimeBoundaryError('runtime_root_mismatch')
    return root


def ensure_inside(root: Path, target: Path) -> Path:
    """排除父目录、符号链接逃逸和目录本身；供加密验收账本复用。"""
    root, target = _root(root), Path(target).resolve()
    if target == root or not target.is_relative_to(root):
        raise RuntimeBoundaryError('runtime_path_escape')
    return target


@contextmanager
def _file_lock(path: Path):
    """跨进程非阻塞锁；故障后由操作系统释放，不自行等待或重试。"""
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeBoundaryError('runtime_operation_locked') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def runtime_lock(root: Path):
    with _file_lock(ensure_inside(root, Path(root) / 'runtime.lock')):
        yield


def _atomic_json(path: Path, value: dict) -> None:
    pending = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix='.pending', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(json.dumps(value, ensure_ascii=False, indent=2).encode())
            handle.flush()
            os.fsync(handle.fileno())
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def load(root: Path = DEFAULT_ROOT) -> dict:
    """只接受本轮标记及固定存储目标，不自动接管旧Windows数据库或同名目录。"""
    root = _root(root)
    data = json.loads(ensure_inside(root, root / MANIFEST).read_text('utf-8'))
    if (data.get('kind') != KIND or data.get('root') != str(root)
            or data.get('engine') != ENGINE or data.get('project') != PROJECT):
        raise RuntimeBoundaryError('runtime_manifest_mismatch')
    if not re.fullmatch(r'[0-9a-f]{32}', data.get('id', '')):
        raise RuntimeBoundaryError('invalid_runtime_id')
    if data.get('data_directory') != str(root / 'data') or data.get('sqlite_file') != str(root / 'data/webui.db'):
        raise RuntimeBoundaryError('sqlite_binding_mismatch')
    for relative in ['data', 'credentials.enc', 'runtime.key', 'entry/provider_entrypoint.py', 'client']:
        expected = root / relative
        if ensure_inside(root, expected) != expected:
            raise RuntimeBoundaryError('runtime_path_redirected')
    image = data.get('image', {})
    if image.get('reference') != IMAGE or image.get('architecture') != 'amd64' or image.get('os') != 'linux':
        raise RuntimeBoundaryError('candidate_image_mismatch')
    if image.get('repo_digest') != IMAGE_REPOSITORY + '@' + image.get('digest', '') or not DIGEST_RE.fullmatch(image.get('digest', '')):
        raise RuntimeBoundaryError('candidate_digest_mismatch')
    if data.get('container', {}).get('name') != NAME:
        raise RuntimeBoundaryError('container_name_mismatch')
    return data


def _update(root: Path, change) -> dict:
    with runtime_lock(root):
        data = load(root)
        change(data)
        _atomic_json(Path(root) / MANIFEST, data)
        return data


def _failure(root: Path, phase: str, error: BaseException, *, expected_runtime_id=None, **details) -> None:
    """失败状态保留；不抹去全局资源故障，也不把未知执行改成可自动重试。"""
    def change(data):
        if expected_runtime_id is not None and data['id'] != expected_runtime_id:
            raise RuntimeBoundaryError('failure_record_runtime_changed')
        data['phase'] = phase + '_failed'
        data['ready'] = False
        data.setdefault('resource_incident', {})['active'] = True
        data.setdefault('failures', []).append({'phase': phase, 'error_type': type(error).__name__,
            'at': now(), **details})
    _update(root, change)


def _host_checks() -> None:
    """核实固定本机管道、D盘程序和安装数据根；不读取进程环境或任何数据库。"""
    if os.name != 'nt' or any(os.environ.get(key) for key in ['DOCKER_HOST', 'DOCKER_CONTEXT']):
        raise RuntimeBoundaryError('docker_host_override_or_non_windows')
    if not DOCKER.is_file() or DOCKER.resolve() != DOCKER:
        raise RuntimeBoundaryError('docker_binary_mismatch')
    settings = json.loads(INSTALL_SETTINGS.read_text('utf-8-sig'))
    if Path(settings.get('wslDefaultDataRoot', '')).resolve() != WSL_DATA_ROOT.resolve():
        raise RuntimeBoundaryError('docker_data_root_not_d_drive')
    if WSL_DATA_ROOT.resolve() != WSL_DATA_ROOT or str(WSL_DATA_ROOT.resolve().drive).upper() != 'D:':
        raise RuntimeBoundaryError('docker_data_root_redirected')
    backend_found = False
    names = {'docker desktop.exe', 'com.docker.backend.exe', 'com.docker.service', 'com.docker.service.exe'}
    for process in psutil.process_iter(['name', 'exe']):
        try:
            name = (process.info['name'] or '').lower()
            if name not in names:
                continue
            executable = Path(process.info['exe'] or '').resolve()
            if not executable.is_relative_to(DOCKER_DIRECTORY.resolve()):
                raise RuntimeBoundaryError('docker_process_not_owned_installation')
            backend_found |= name == 'com.docker.backend.exe'
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            raise RuntimeBoundaryError('docker_process_identity_unavailable') from None
    if not backend_found:
        raise RuntimeBoundaryError('docker_backend_not_running')
    import ctypes
    for target in [DEFAULT_ROOT.resolve(), DOCKER_DIRECTORY.resolve(), WSL_DATA_ROOT.resolve()]:
        if target.drive.upper() != 'D:' or ctypes.windll.kernel32.GetDriveTypeW(target.anchor) != 3:
            raise RuntimeBoundaryError('docker_requires_local_d_drive')


def _resources() -> dict:
    path = REPO / 'deploy/native/windows_services.py'
    spec = importlib.util.spec_from_file_location('_docker_native_resource_guard', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.require_api_resources()


def check_host_resources() -> dict:
    """公开只读宿主门限检查，供验收守卫复用；失败原样抛RuntimeError，不发网络请求。"""
    return _resources()


def _disk(root: Path, compressed_bytes: int = 0) -> int:
    """首次父目录尚不存在时，从最近既有祖先读同盘容量，不创建目录或跟随重定向。"""
    target = Path(root).absolute()
    _reject_reparse(target)
    ancestor = target
    while not ancestor.exists():
        if ancestor == ancestor.parent:
            raise RuntimeBoundaryError('docker_disk_target_unavailable')
        ancestor = ancestor.parent
    if ancestor.anchor != target.anchor:
        raise RuntimeBoundaryError('docker_disk_target_changed')
    required = max(MIN_DISK, 2 * compressed_bytes + 2 * GIB)
    if shutil.disk_usage(ancestor).free < required:
        raise RuntimeBoundaryError('docker_disk_budget_insufficient')
    return required


def _command(root: Path, args: list[str]) -> list[str]:
    return [str(DOCKER), '--host', ENGINE, '--config', str(Path(root) / 'client'), *args]


def _process_options() -> dict:
    env = {key: os.environ[key] for key in ['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'COMSPEC'] if key in os.environ}
    return {'env': env, 'shell': False, 'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


def _docker(root: Path, args: list[str], *, seconds: float = 15) -> str:
    """固定可执行文件/管道/独立客户端配置，不继承Docker上下文、代理或业务凭据。"""
    if seconds <= 0:
        raise RuntimeBoundaryError('docker_operation_deadline')
    result = subprocess.run(_command(root, args), capture_output=True, timeout=seconds, **_process_options())
    if result.returncode:
        raise RuntimeBoundaryError('docker_command_failed')
    # Uvicorn原生日志写stderr；只在logs分支合并用于内存内标记判断，其他命令不读取错误正文。
    output = result.stdout + (result.stderr if args[:2] == ['container', 'logs'] else b'')
    if len(output) > 4 * 1024 * 1024:
        raise RuntimeBoundaryError('docker_output_budget_exceeded')
    return output.decode('utf-8', errors='strict').strip()


def _stop_cli(process) -> dict:
    """只回收本次Popen持有的CLI句柄，不杀Docker后端或不认识的PID。"""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    return {'pid': process.pid, 'stopped': process.poll() is not None}


def _pull_image(root: Path, data: dict, deadline: float) -> None:
    """拉层期间每半秒核主机/磁盘；异常只停本次CLI，守护进程侧进度保留为未知。"""
    process = None
    try:
        if time.monotonic() >= deadline:
            raise RuntimeBoundaryError('pull_900_seconds_exceeded')
        process = subprocess.Popen(_command(root, ['pull', '--platform', 'linux/amd64', data['image']['repo_digest']]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_process_options())
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise RuntimeBoundaryError('pull_900_seconds_exceeded')
            _resources()
            _disk(root, data['image']['compressed_size_bytes'])
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        if process.returncode:
            raise RuntimeBoundaryError('docker_pull_command_failed')
    except BaseException as error:
        cleanup = {'stopped': True, 'spawned': process is not None}
        if process is not None:
            try:
                cleanup.update(_stop_cli(process))
            except Exception as cleanup_error:
                cleanup.update(stopped=False, error_type=type(cleanup_error).__name__)
        # 固定安全结构供失败记录保存；不把输出或上游异常正文加入manifest。
        error.cli_cleanup = cleanup
        raise


def select_candidate(value) -> dict:
    """从官方候选标签中选唯一linux/amd64清单，锁定摘要及压缩层预算。"""
    values = value if isinstance(value, list) else [value]
    selected = []
    for item in values:
        if not isinstance(item, dict):
            raise RuntimeBoundaryError('invalid_image_manifest')
        descriptor = item.get('Descriptor', {})
        platform = descriptor.get('platform', {})
        if platform.get('os') != 'linux' or platform.get('architecture') != 'amd64' or platform.get('variant'):
            continue
        manifest = item.get('SchemaV2Manifest') or item.get('OCIManifest')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('layers'), list):
            raise RuntimeBoundaryError('missing_platform_layer_manifest')
        digest = descriptor.get('digest', '')
        if not DIGEST_RE.fullmatch(digest):
            raise RuntimeBoundaryError('invalid_platform_digest')
        parts = [manifest.get('config', {}), *manifest['layers']]
        if any(not isinstance(p.get('size'), int) or p['size'] <= 0 or not DIGEST_RE.fullmatch(p.get('digest', '')) for p in parts):
            raise RuntimeBoundaryError('invalid_platform_layer_budget')
        selected.append({'reference': IMAGE, 'digest': digest, 'repo_digest': IMAGE_REPOSITORY + '@' + digest,
            'architecture': 'amd64', 'os': 'linux', 'compressed_size_bytes': sum(p['size'] for p in parts),
            'id': None, 'size_bytes': None, 'candidate_only': True})
    if len(selected) != 1:
        raise RuntimeBoundaryError('ambiguous_or_missing_linux_amd64_image')
    return selected[0]


def _provider_payload() -> dict:
    sys.path.insert(0, str(REPO / 'services/platform'))
    from agent_platform.core.provider_settings import LocalProviderStore
    return LocalProviderStore().read()


def _labels(data: dict) -> dict:
    return {LABEL_PROJECT: PROJECT, LABEL_RUNTIME: data['id'], LABEL_OWNER: OWNER}


def _assert_no_name(root: Path) -> None:
    if _docker(root, ['container', 'ls', '--all', '--filter', f'name=^/{NAME}$', '--format', '{{.ID}}']):
        raise RuntimeBoundaryError('existing_container_name_not_adopted')


def prepare(root: Path = DEFAULT_ROOT) -> dict:
    """全新目录一次性认领；仅远端清单元数据，不拉镜像层、不创建容器。"""
    root = _root(root)
    _host_checks()
    _resources()
    _disk(root)
    root.mkdir(parents=True, exist_ok=False)
    for folder in ['client', 'entry', 'data']:
        (root / folder).mkdir()
    # mkdir(exist_ok=False)是跨进程新目录认领；中途失败也保留目录，禁止自动接管。
    deadline = time.monotonic() + PREPARE_SECONDS
    with _file_lock(root / 'operation.lock'):
        _assert_no_name(root)
        image = select_candidate(json.loads(_docker(root, ['manifest', 'inspect', '--verbose', IMAGE],
            seconds=max(0, deadline - time.monotonic()))))
        required = _disk(root, image['compressed_size_bytes'])
        payload = {'webui_secret': secrets.token_urlsafe(48), 'provider_settings': _provider_payload()}
        entry = Path(__file__).with_name('provider_entrypoint.py')
        spec = importlib.util.spec_from_file_location('_docker_provider_validation', entry)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_credentials(payload)
        key = Fernet.generate_key()
        for name, content in [('runtime.key', key), ('credentials.enc', Fernet(key).encrypt(json.dumps(payload).encode()))]:
            with (root / name).open('xb') as handle:
                handle.write(content)
            (root / name).chmod(0o600)
        entry_bytes = entry.read_bytes()
        (root / 'entry/provider_entrypoint.py').write_bytes(entry_bytes)
        data = {'kind': KIND, 'id': uuid4().hex, 'root': str(root), 'engine': ENGINE, 'project': PROJECT,
            'created_at': now(), 'phase': 'prepared', 'ready': False, 'image': image,
            'container': {'name': NAME, 'id': None, 'created_at': None},
            'data_directory': str(root / 'data'), 'sqlite_file': str(root / 'data/webui.db'),
            'entrypoint_sha256': hashlib.sha256(entry_bytes).hexdigest(), 'disk_required_bytes': required,
            'frontend_expected': True, 'frontend_available': None, 'source_equivalence_verified': False,
            'startup_seconds': START_SECONDS, 'pull_seconds': PULL_SECONDS,
            'pull_attempted': False, 'start_attempted': False, 'failures': []}
        _atomic_json(root / MANIFEST, data)
        return data


def _image_inspect(root: Path, data: dict, *, seconds=15) -> dict:
    value = json.loads(_docker(root, ['image', 'inspect', '--format', IMAGE_FORMAT, data['image']['repo_digest']], seconds=seconds))
    if value.get('Os') != 'linux' or value.get('Architecture') != 'amd64' or not DIGEST_RE.fullmatch(value.get('Id', '')):
        raise RuntimeBoundaryError('pulled_image_platform_mismatch')
    if data['image']['repo_digest'] not in value.get('RepoDigests', []) or not isinstance(value.get('Size'), int) or value['Size'] <= 0:
        raise RuntimeBoundaryError('pulled_image_digest_mismatch')
    if data['image'].get('id') and value['Id'] != data['image']['id']:
        raise RuntimeBoundaryError('local_image_id_changed')
    return value


def pull(root: Path = DEFAULT_ROOT) -> dict:
    """只允许一次固定摘要pull；总计900秒，失败保留unknown且不自动续拉。"""
    root = _root(root)
    _host_checks()
    _resources()
    with _file_lock(root / 'operation.lock'):
        data = load(root)
        if data['phase'] != 'prepared' or data.get('pull_attempted'):
            raise RuntimeBoundaryError('pull_already_attempted_or_not_prepared')
        _disk(root, data['image']['compressed_size_bytes'])
        _update(root, lambda d: d.update(pull_attempted=True, phase='pull_attempted'))
        deadline = time.monotonic() + PULL_SECONDS
        try:
            _pull_image(root, data, deadline)
            image = _image_inspect(root, data, seconds=max(0, deadline - time.monotonic()))
            def change(d):
                d['image'].update(id=image['Id'], size_bytes=image['Size'])
                d.update(phase='pulled', pulled_at=now())
            return _update(root, change)
        except BaseException as error:
            _failure(root, 'pull', error, expected_runtime_id=data['id'], cli_cleanup=getattr(error, 'cli_cleanup', None))
            raise


def _source_path(value: str) -> str:
    """归一Docker Desktop Linux挂载表示；只接受本轮D盘路径，不把任意Linux目录当成本机盘。"""
    value = value.replace('\\', '/').rstrip('/')
    for prefix in ['/run/desktop/mnt/host/d/', '/host_mnt/d/']:
        if value.lower().startswith(prefix):
            value = 'D:/' + value[len(prefix):]
            break
    return value.casefold()


def _mounts(root: Path) -> dict[str, tuple[str, bool]]:
    return {'/app/backend/data': (str(root / 'data'), False),
        '/run/agent-runtime/runtime.key': (str(root / 'runtime.key'), True),
        '/run/agent-runtime/credentials.enc': (str(root / 'credentials.enc'), True),
        '/run/agent-runtime/provider_entrypoint.py': (str(root / 'entry/provider_entrypoint.py'), True)}


def _raw_container(root: Path, data: dict, *, seconds=10) -> dict:
    cid = data['container'].get('id')
    if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
        raise RuntimeBoundaryError('no_registered_container')
    return json.loads(_docker(root, ['container', 'inspect', '--format', CONTAINER_FORMAT, cid], seconds=seconds))


def _verify_identity(root: Path, data: dict, raw: dict) -> None:
    """停止也必须重核的归属条件；资源/状态漂移与身份归属分别判断。"""
    if raw.get('Id') != data['container']['id'] or raw.get('Name') != '/' + NAME or raw.get('Image') != data['image']['id']:
        raise RuntimeBoundaryError('container_identity_changed')
    if data['container'].get('created_at') and raw.get('Created') != data['container']['created_at']:
        raise RuntimeBoundaryError('container_creation_changed')
    labels = raw.get('Labels') or {}
    if any(labels.get(key) != val for key, val in _labels(data).items()):
        raise RuntimeBoundaryError('container_labels_changed')
    mounts = raw.get('Mounts', [])
    expected = _mounts(root)
    if len(mounts) != len(expected):
        raise RuntimeBoundaryError('container_mounts_changed')
    for mount in mounts:
        target = mount.get('Destination')
        if target not in expected:
            raise RuntimeBoundaryError('container_mounts_changed')
        source, readonly = expected[target]
        if mount.get('Type') != 'bind' or _source_path(mount.get('Source', '')) != _source_path(source) or bool(mount.get('RW')) == readonly:
            raise RuntimeBoundaryError('container_mounts_changed')
    if len({mount['Destination'] for mount in mounts}) != len(expected):
        raise RuntimeBoundaryError('container_mounts_changed')
    entry = ensure_inside(root, root / 'entry/provider_entrypoint.py')
    if hashlib.sha256(entry.read_bytes()).hexdigest() != data['entrypoint_sha256']:
        raise RuntimeBoundaryError('runtime_entrypoint_changed')


def _limits(raw: dict, *, strict=True) -> dict:
    host = raw.get('HostConfig', {})
    limits = {'memory_bytes': host.get('Memory'), 'memory_swap_bytes': host.get('MemorySwap'),
        'nano_cpus': host.get('NanoCpus'), 'pids_limit': host.get('PidsLimit'),
        'restart_policy': (host.get('RestartPolicy') or {}).get('Name'),
        'healthcheck_disabled': (raw.get('Healthcheck') or {}).get('Test') == ['NONE'],
        'privileged': host.get('Privileged'), 'cap_drop': host.get('CapDrop') or [],
        'security_options': host.get('SecurityOpt') or []}
    if strict:
        expected = {'memory_bytes': MEMORY, 'memory_swap_bytes': MEMORY, 'nano_cpus': NANO_CPUS,
            'pids_limit': PIDS, 'restart_policy': 'no', 'healthcheck_disabled': True, 'privileged': False}
        if any(limits.get(key) != val for key, val in expected.items()) or {x.upper() for x in limits['cap_drop']} != {'ALL'} or host.get('CapAdd'):
            raise RuntimeBoundaryError('container_resource_or_security_drift')
        if not any(x in {'no-new-privileges', 'no-new-privileges:true'} for x in limits['security_options']):
            raise RuntimeBoundaryError('container_privilege_policy_drift')
        if host.get('NetworkMode') != 'bridge' or raw.get('Entrypoint') != ['python3'] or raw.get('Cmd') != ['/run/agent-runtime/provider_entrypoint.py']:
            raise RuntimeBoundaryError('container_execution_policy_drift')
        expected_ports = {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}]}
        if host.get('PortBindings') != expected_ports:
            raise RuntimeBoundaryError('container_port_binding_drift')
        if (raw.get('State') or {}).get('Running') and raw.get('Ports') != expected_ports:
            raise RuntimeBoundaryError('container_listening_binding_drift')
    return limits


def _snapshot(root: Path, data: dict, raw: dict) -> dict:
    _verify_identity(root, data, raw)
    limits = _limits(raw)
    state = raw.get('State') or {}
    return {'ownership_verified': True, 'runtime_id': data['id'], 'root': str(root), 'engine': ENGINE,
        'project': PROJECT, 'container_id': raw['Id'], 'container_name': NAME, 'image_id': raw['Image'],
        'image_digest': data['image']['repo_digest'], 'data_directory': data['data_directory'],
        'sqlite_file': data['sqlite_file'], 'host': '127.0.0.1', 'port': PORT, 'internal_port': 8080,
        'labels': _labels(data), 'mounts': [{'source': item['Source'], 'destination': item['Destination'],
            'read_only': not item['RW'], 'type': item['Type']} for item in raw['Mounts']], 'limits': limits,
        'state': {'running': bool(state.get('Running')), 'status': state.get('Status'),
            'oom_killed': bool(state.get('OOMKilled')), 'dead': bool(state.get('Dead')), 'exit_code': state.get('ExitCode'),
            'pid': state.get('Pid'), 'started_at': state.get('StartedAt'), 'finished_at': state.get('FinishedAt'),
            'error_present': bool(state.get('Error'))},
        'ready': bool(data.get('ready') and state.get('Running') and not state.get('OOMKilled')),
        'frontend_expected': True, 'frontend_available': data.get('frontend_available'),
        'startup_seconds': START_SECONDS, 'source_equivalence_verified': False}


def inspect_owned(root: Path = DEFAULT_ROOT, *, timeout_seconds: float = 3) -> dict:
    """返回已筛选归属和状态；OOM/退出如实返回，未知或被替换的容器直接拒绝。"""
    deadline = time.monotonic() + timeout_seconds
    if timeout_seconds <= 0:
        raise RuntimeBoundaryError('docker_operation_deadline')
    root = _root(root)
    _host_checks()
    data = load(root)
    return _snapshot(root, data, _raw_container(root, data, seconds=min(3, deadline - time.monotonic())))


def stop_owned(root: Path = DEFAULT_ROOT, reason: str = 'operator_requested', *,
        expected_container_id: str | None = None, expected_runtime_id: str | None = None,
        timeout_seconds: float = 5) -> dict:
    """CAS重核后仅停止原CID；资源配置漂移仍可止损，归属漂移绝不停止。"""
    deadline = time.monotonic() + timeout_seconds
    if timeout_seconds <= 0:
        raise RuntimeBoundaryError('docker_operation_deadline')
    root = _root(root)
    _host_checks()
    data = load(root)
    cid = data['container'].get('id')
    if ((expected_container_id is not None and expected_container_id != cid)
            or (expected_runtime_id is not None and expected_runtime_id != data['id'])):
        raise RuntimeBoundaryError('stop_compare_and_swap_failed')
    raw = _raw_container(root, data, seconds=min(1, deadline - time.monotonic()))
    _verify_identity(root, data, raw)
    if raw.get('State', {}).get('Running'):
        remaining = deadline - time.monotonic()
        grace = max(0, min(2, int(remaining - 1)))
        _docker(root, ['container', 'stop', '--time', str(grace), cid], seconds=max(0, remaining - 0.5))
    after = _raw_container(root, data, seconds=min(1, deadline - time.monotonic()))
    _verify_identity(root, data, after)
    stopped = not after.get('State', {}).get('Running')
    result = {'verified': True, 'stopped': stopped, 'container_id': cid, 'runtime_id': data['id'],
        'data_preserved': True, 'container_preserved': True, 'at': now()}
    def change(d):
        if d['id'] != data['id'] or d['container'].get('id') != cid:
            raise RuntimeBoundaryError('stop_compare_and_swap_failed')
        d['ready'] = False
        d.setdefault('stops', []).append({**result, 'reason': reason if re.fullmatch(r'[a-z0-9_]{1,80}', reason) else 'operator_requested'})
    _update(root, change)
    if not stopped:
        raise RuntimeBoundaryError('owned_container_still_running')
    return result


def _port_free() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            connection.bind(('127.0.0.1', PORT))
        except OSError:
            raise RuntimeBoundaryError('loopback_port_already_in_use') from None


def _create_arguments(root: Path, data: dict) -> list[str]:
    args = ['container', 'create', '--pull', 'never', '--platform', 'linux/amd64', '--name', NAME,
        '--cidfile', str(root / 'created.cid'), '--memory', str(MEMORY), '--memory-swap', str(MEMORY),
        '--cpus', '1', '--pids-limit', str(PIDS), '--restart', 'no', '--no-healthcheck',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--network', 'bridge',
        '--publish', f'127.0.0.1:{PORT}:8080', '--entrypoint', 'python3']
    for key, val in _labels(data).items():
        args += ['--label', f'{key}={val}']
    for target, (source, readonly) in _mounts(root).items():
        args += ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if readonly else '')]
    return [*args, data['image']['repo_digest'], '/run/agent-runtime/provider_entrypoint.py']


def _register_created(root: Path, data: dict, cid: str, *, timeout_seconds=3) -> dict:
    """先凭本次cidfile和创建回包登记，再核标签/镜像/挂载；不按同名容器接管。"""
    cid_path = ensure_inside(root, root / 'created.cid')
    if cid_path != root / 'created.cid' or not CID_RE.fullmatch(cid) or cid_path.read_text('ascii').strip() != cid:
        raise RuntimeBoundaryError('created_container_receipt_mismatch')
    def change(d):
        if d['id'] != data['id'] or d['container'].get('id'):
            raise RuntimeBoundaryError('container_registration_conflict')
        d['container']['id'] = cid
    current = _update(root, change)
    raw = _raw_container(root, current, seconds=timeout_seconds)
    _snapshot(root, current, raw)
    return _update(root, lambda d: d['container'].update(created_at=raw['Created']))


def start(root: Path = DEFAULT_ROOT) -> dict:
    """一次180秒监听验收；只看归属/状态和有限日志，不发HTTP、不自动重启。"""
    root = _root(root)
    _host_checks()
    _resources()
    with _file_lock(root / 'operation.lock'):
        data = load(root)
        if data['phase'] != 'pulled' or data.get('start_attempted') or data['container'].get('id'):
            raise RuntimeBoundaryError('start_already_attempted_or_not_pulled')
        _disk(root, data['image']['compressed_size_bytes'])
        _image_inspect(root, data)
        _assert_no_name(root)
        _port_free()
        if any((root / 'data').iterdir()) or (root / 'created.cid').exists():
            raise RuntimeBoundaryError('existing_data_not_adopted')
        _update(root, lambda d: d.update(start_attempted=True, phase='start_attempted', startup_started_at=now()))
        deadline = time.monotonic() + START_SECONDS
        try:
            cid = _docker(root, _create_arguments(root, data), seconds=min(30, deadline - time.monotonic()))
            data = _register_created(root, data, cid, timeout_seconds=min(3, deadline - time.monotonic()))
            _docker(root, ['container', 'start', cid], seconds=min(20, deadline - time.monotonic()))
            while time.monotonic() < deadline:
                _resources()
                raw = _raw_container(root, data, seconds=min(8, deadline - time.monotonic()))
                result = _snapshot(root, data, raw)
                if not result['state']['running'] or result['state']['oom_killed'] or result['state']['dead']:
                    raise RuntimeBoundaryError('owned_container_exited_or_oom')
                _resources()
                logs = _docker(root, ['container', 'logs', '--tail', '80', cid], seconds=min(8, deadline - time.monotonic()))
                _resources()
                # 原始日志仅在内存中判断原生监听标记，绝不打印、写入manifest或异常。
                if re.search(r'Uvicorn running on http://0\.0\.0\.0:8080(?:\s|$)', logs):
                    _update(root, lambda d: d.update(ready=True, phase='listening',
                        logs_readiness_seen=True, startup_finished_at=now()))
                    return _snapshot(root, load(root), raw)
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            raise RuntimeBoundaryError('startup_180_seconds_exceeded')
        except BaseException as error:
            # 创建回包或登记中断时只认本次新cidfile，不按名字查找并认领其他容器。
            cleanup = {'verified': False, 'stopped': False}
            try:
                current = load(root)
                cid_path = ensure_inside(root, root / 'created.cid')
                if current['id'] != data['id'] or current['image']['id'] != data['image']['id']:
                    raise RuntimeBoundaryError('cleanup_runtime_changed')
                if cid_path != root / 'created.cid':
                    raise RuntimeBoundaryError('created_container_receipt_redirected')
                if cid_path.is_file():
                    candidate = cid_path.read_text('ascii').strip()
                    if not CID_RE.fullmatch(candidate):
                        raise RuntimeBoundaryError('invalid_created_container_receipt')
                    if not current['container'].get('id'):
                        # 登记中断后仅预约本次cidfile；stop内部仍须重核标签/镜像/全部挂载。
                        def reserve(d):
                            if d['id'] != data['id'] or d['container'].get('id'):
                                raise RuntimeBoundaryError('cleanup_registration_conflict')
                            d['container']['id'] = candidate
                        current = _update(root, reserve)
                    if current['container']['id'] != candidate:
                        raise RuntimeBoundaryError('cleanup_container_changed')
                    cleanup = stop_owned(root, 'startup_failed', expected_container_id=candidate, expected_runtime_id=data['id'])
            except Exception as cleanup_error:
                cleanup['error_type'] = type(cleanup_error).__name__
            _failure(root, 'start', error, expected_runtime_id=data['id'], cleanup=cleanup)
            raise


def main(argv=None) -> int:
    """CLI不输出原始上游异常、容器环境或提供方配置。"""
    parser = argparse.ArgumentParser(description='本轮独立Docker OpenWebUI控制器')
    parser.add_argument('action', choices=['prepare', 'pull', 'start', 'stop', 'inspect'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    actions = {'prepare': prepare, 'pull': pull, 'start': start, 'stop': stop_owned, 'inspect': inspect_owned}
    try:
        result = actions[args.action](args.root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'action': args.action, 'error_type': type(error).__name__,
            'code': str(error) if isinstance(error, RuntimeBoundaryError) else 'operation_failed'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
