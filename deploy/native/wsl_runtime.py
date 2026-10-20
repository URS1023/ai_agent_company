"""独立WSL中的Dify原生进程装配；复用上游入口，不实现工作流/Agent/知识内核。

prepare只生成隔离配置和随机密文；init-data显式初始化本次新PG并执行上游迁移；
start按依赖顺序启动、仅核进程和监听，失败回收本次launch。status不发HTTP或SQL。
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import signal
import socket
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import psutil

ROOT = Path('/opt/ai-agent-company')
DISTRO = 'ai-agent-company'
KIND = 'ai-agent-company-dify-wsl-v1'
PORTS = {'postgres': 25432, 'redis': 26379, 'qdrant': 26333, 'qdrant_grpc': 26334,
    'api': 15001, 'web_node': 23001, 'web': 13001, 'plugin': 15002, 'plugin_debug': 15003,
    'agent': 15050, 'shellctl': 15004, 'code': 18194, 'proxy': 13128, 'agent_proxy': 13129}
ORDER = ['postgres', 'redis', 'qdrant', 'proxy', 'agent_proxy', 'shellctl', 'code',
    'plugin', 'agent', 'api', 'worker', 'beat', 'web', 'gateway']
SECRETS = ['pg_admin', 'pg_app', 'redis', 'qdrant', 'plugin', 'plugin_inner', 'agent_api',
    'agent_root', 'shellctl', 'code', 'flask']


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def check_root() -> None:
    if os.name != 'posix' or os.environ.get('WSL_DISTRO_NAME') != DISTRO or os.getuid() != 0:
        raise RuntimeError('owned_wsl_root_required')
    if ROOT.resolve() != ROOT:
        raise RuntimeError('runtime_root_redirected')
    bootstrap = json.loads((ROOT / 'bootstrap.json').read_text())
    if bootstrap.get('kind') != 'ai-agent-company-wsl-bootstrap-v1' or bootstrap.get('distro') != DISTRO or bootstrap.get('status') != 'ready':
        raise RuntimeError('bootstrap_not_ready')


def inside(path: Path) -> Path:
    original = path.absolute()
    actual = path.resolve()
    if actual == ROOT or not actual.is_relative_to(ROOT) or original != actual:
        raise RuntimeError('private_runtime_path_escape')
    return actual


def atomic_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False, suffix='.pending') as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(value, ensure_ascii=False, indent=2).encode())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@contextmanager
def lock():
    import fcntl
    with (ROOT / 'runtime.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load() -> dict:
    data = json.loads((ROOT / 'runtime.json').read_text())
    if data.get('kind') != KIND or data.get('root') != str(ROOT) or data.get('distro') != DISTRO or data.get('ports') != PORTS:
        raise RuntimeError('wsl_runtime_manifest_changed')
    if data.get('source_version') != '1.17.1' or len(data.get('id', '')) != 32:
        raise RuntimeError('wsl_runtime_version_changed')
    for folder in ['data', 'secrets', 'logs', 'sources', 'jobs', 'bin']:
        inside(ROOT / folder)
    return data


def update(change) -> dict:
    with lock():
        data = load()
        change(data)
        atomic_json(ROOT / 'runtime.json', data)
        return data


def vault() -> dict:
    key_path = inside(ROOT / 'secrets/runtime.key')
    secret_path = inside(ROOT / 'secrets/credentials.enc')
    return json.loads(Fernet(key_path.read_bytes().strip()).decrypt(secret_path.read_bytes()))


def account(name: str):
    import pwd
    return pwd.getpwnam(name)


def ensure_account(name: str) -> None:
    try:
        value = account(name)
        if value.pw_uid == 0:
            raise RuntimeError('service_account_must_not_be_root')
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', name],
            check=True, timeout=15, capture_output=True)


def ensure_sandbox_account() -> None:
    """预先创建上游固定UID，避免沙箱私有/var中隐式执行useradd。"""
    import pwd
    try:
        value = pwd.getpwnam('sandbox')
    except KeyError:
        try:
            pwd.getpwuid(65537)
        except KeyError:
            subprocess.run(['/usr/sbin/useradd', '--no-create-home', '--no-log-init',
                '--uid', '65537', '--shell', '/usr/sbin/nologin', 'sandbox'],
                check=True, timeout=15, capture_output=True)
            value = pwd.getpwnam('sandbox')
        else:
            raise RuntimeError('sandbox_fixed_uid_already_used') from None
    if value.pw_uid != 65537:
        raise RuntimeError('sandbox_existing_uid_mismatch')


def directory(path: Path, owner='root', mode=0o700) -> None:
    inside(path)
    path.mkdir(parents=True, exist_ok=True)
    identity = account(owner)
    os.chown(path, identity.pw_uid, identity.pw_gid)
    path.chmod(mode)


def freeze_programs() -> None:
    """安装后代码和解释器改由root维护，应用/任务只读；业务缓存另存可写data目录。"""
    for name in ['tooling', 'sources', 'venvs', 'bin', 'web']:
        for current, folders, files in os.walk(ROOT / name, followlinks=False):
            path = Path(current)
            os.chown(path, 0, 0)
            path.chmod(0o755)
            for filename in files:
                child = path / filename
                if not child.is_symlink():
                    mode = child.stat().st_mode
                    os.chown(child, 0, 0)
                    child.chmod(0o755 if mode & 0o111 else 0o644)
    directory(ROOT / 'control', 'root', 0o700)
    for name in ['wsl_process.py', 'wsl_cgroups.py', 'wsl_network.py', 'wsl_sandbox_entry.py']:
        source = Path(__file__).with_name(name)
        destination = ROOT / 'control' / name
        if destination.exists():
            if destination.read_bytes() != source.read_bytes():
                raise RuntimeError('existing_control_script_not_overwritten')
        else:
            with destination.open('xb') as handle:
                handle.write(source.read_bytes())
        destination.chmod(0o600)


def landlock_abi() -> int:
    """x86_64 syscall444仅查询Landlock ABI，不创建任务或读取任何数据库。"""
    library = ctypes.CDLL(None, use_errno=True)
    abi = library.syscall(444, None, 0, 1)
    if abi < 1:
        raise RuntimeError('landlock_not_available')
    return abi


def boot_identity() -> str:
    """内核boot和本发行版PID1出生时间共同识别重启，避免接管旧PID/临时namespace。"""
    value = {'kernel': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'init_started_at': psutil.Process(1).create_time(), 'pid_namespace': Path('/proc/1/ns/pid').stat().st_ino}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def dependency_checks() -> list[str]:
    """只核文件与收据，不导入Dify、不访问任何数据库或发健康请求。"""
    missing = []
    for path in ['venvs/api/bin/gunicorn', 'venvs/api/bin/celery', 'venvs/agent/bin/uvicorn',
            'bin/shellctl', 'bin/shellctl-runner', 'bin/shellctl-sanitize-pty', 'bin/shellctl-runner-exit',
            'bin/dify-agent', 'bin/plugin-daemon', 'bin/qdrant', 'bin/code-sandbox', 'bin/code-sandbox-env', 'web/web/server.js']:
        if not (ROOT / path).is_file(): missing.append(path)
    try:
        records = json.loads((ROOT / 'go-binaries-receipt.json').read_text())['binaries']
        for name in ['shellctl', 'shellctl-runner', 'shellctl-sanitize-pty', 'shellctl-runner-exit', 'dify-agent', 'plugin-daemon']:
            path = ROOT / 'bin' / name
            if not path.is_file() or file_sha(path) != records[name]['sha256']:
                missing.append('binary_digest:' + name)
        for receipt_name, name, key in [('qdrant-receipt.json', 'bin/qdrant', 'binary_sha256'),
                ('sandbox-receipt.json', 'bin/code-sandbox', 'binary_sha256'),
                ('web/artifact-receipt.json', 'web/web/server.js', 'next_server_sha256')]:
            receipt = json.loads((ROOT / receipt_name).read_text())
            if file_sha(ROOT / name) != receipt[key]:
                missing.append('binary_digest:' + name)
        stages = json.loads((ROOT / 'setup-stages.json').read_text())
        for name in ['tools', 'python', 'web', 'sandbox']:
            if stages.get(name, {}).get('status') != 'ready': missing.append('setup_stage:' + name)
        source = json.loads((ROOT / 'source-receipt.json').read_text())
        if source.get('filter_revision') != 2:
            missing.append('source_filter_revision')
        python_revision = stages.get('python', {}).get('result', {}).get('source_filter_revision')
        repaired = stages.get('python-package-repair', {}).get('status') == 'ready'
        if python_revision != 2 and not repaired:
            missing.append('installed_agent_source_revision')
    except (OSError, KeyError, ValueError):
        missing.append('dependency_receipt_missing_or_invalid')
    try:
        plugin_cli_receipt()
    except (OSError, KeyError, ValueError, RuntimeError):
        missing.append('plugin_cli_receipt_missing_or_invalid')
    return missing


def plugin_cli_receipt() -> dict:
    """与安装器复用同一来源及摘要校验，不把文件存在当作官方迁移入口可用。"""
    from wsl_setup import verify_plugin_cli
    return verify_plugin_cli(ROOT)


def prepare() -> dict:
    check_root()
    if (ROOT / 'runtime.json').exists():
        receipt = json.loads((ROOT / 'prepare-attempt.json').read_text())
        if receipt.get('status') != 'ready':
            raise RuntimeError('previous_prepare_requires_review')
        return status()
    source = json.loads((ROOT / 'source-receipt.json').read_text())
    if source.get('version') != '1.17.1':
        raise RuntimeError('locked_dify_sources_missing')
    if dependency_checks():
        raise RuntimeError('native_dependencies_not_prepared')
    attempt = ROOT / 'prepare-attempt.json'
    if attempt.exists():
        raise RuntimeError('previous_prepare_requires_review')
    with attempt.open('x') as handle:
        json.dump({'status': 'attempted', 'at': timestamp()}, handle)
    for name in ['agentpg', 'agentcache', 'agentjob']:
        ensure_account(name)
    ensure_sandbox_account()
    # 应用根仅可穿越；各数据/密钥目录分别限制，任务用户无法读PG、应用密钥或Windows挂载。
    os.chown(ROOT, 0, 0)
    ROOT.chmod(0o711)
    for folder, owner, mode in [('data', 'root', 0o711), ('secrets', 'root', 0o700), ('logs', 'root', 0o700),
            ('config', 'root', 0o755), ('jobs', 'agentjob', 0o700), ('jobs/home', 'agentjob', 0o700),
            ('jobs/workspace', 'agentjob', 0o700), ('data/postgres', 'agentpg', 0o700),
            ('jobs/snapshots', 'agentjob', 0o700),
            ('data/redis', 'agentcache', 0o700), ('data/dify', 'agentapp', 0o700),
            ('data/plugin', 'agentapp', 0o700),
            ('data/qdrant', 'agentapp', 0o700), ('data/squid', 'agentapp', 0o700), ('data/code', 'root', 0o700),
            ('process-launches', 'root', 0o700), ('data/dify/nginx-body', 'agentapp', 0o700),
            ('data/dify/nginx-proxy', 'agentapp', 0o700)]:
        directory(ROOT / folder, owner, mode)
    freeze_programs()
    directory(ROOT / 'web/web/.next/cache', 'agentapp', 0o700)
    key = Fernet.generate_key()
    values = {name: secrets.token_urlsafe(32) for name in SECRETS}
    for path, content in [(ROOT / 'secrets/runtime.key', key),
            (ROOT / 'secrets/credentials.enc', Fernet(key).encrypt(json.dumps(values).encode()))]:
        with path.open('xb') as handle:
            handle.write(content)
        path.chmod(0o600)
    data = {'kind': KIND, 'id': uuid4().hex, 'distro': DISTRO, 'root': str(ROOT), 'ports': PORTS,
        'source_version': '1.17.1', 'landlock_abi': landlock_abi(), 'prepared_at': timestamp(),
        'databases_initialized': False, 'database_initialization_attempted': False,
        'plugin_database_initialized': False, 'processes': {},
        'launches': [], 'failures': [], 'full_native_verified': False, 'boot_id': boot_identity(), 'boot_history': []}
    atomic_json(ROOT / 'runtime.json', data)
    write_configs(data)
    atomic_json(attempt, {'status': 'ready', 'at': timestamp(), 'runtime_id': data['id']})
    return {'prepared': True, 'runtime_id': data['id'], 'landlock_abi': data['landlock_abi'],
        'database_initialized': False, 'services_started': False}


def safe_environment(data: dict) -> dict:
    return {'HOME': '/home/agentapp', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'WSL_DISTRO_NAME': DISTRO,
        'PATH': f'{ROOT}/bin:{ROOT}/tooling/node/bin:{ROOT}/tooling/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHON_DOTENV_DISABLED': '1',
        'AGENT_WSL_RUNTIME_ID': data['id'], 'UV_HTTP_RETRIES': '0', 'PIP_RETRIES': '0',
        'UV_CACHE_DIR': str(ROOT / 'cache/uv'), 'UV_PYTHON_INSTALL_DIR': str(ROOT / 'tooling/python'),
        'NEXT_TELEMETRY_DISABLED': '1'}


def api_environment(data: dict, secret: dict) -> dict:
    redis = f"redis://:{secret['redis']}@127.0.0.1:{PORTS['redis']}"
    public = f"http://127.0.0.1:{PORTS['web']}"
    return {**safe_environment(data), 'FLASK_APP': 'app.py', 'DEPLOY_ENV': 'PRODUCTION', 'DEBUG': 'false',
        'LOG_LEVEL': 'WARNING', 'SECRET_KEY': secret['flask'], 'MODE': 'api', 'MIGRATION_ENABLED': 'false',
        'DIFY_BIND_ADDRESS': '127.0.0.1', 'DIFY_PORT': str(PORTS['api']), 'SERVER_WORKER_AMOUNT': '1',
        'SERVER_WORKER_CONNECTIONS': '30', 'SERVER_WORKER_CLASS': 'geventwebsocket.gunicorn.workers.GeventWebSocketWorker',
        'DB_TYPE': 'postgresql', 'DB_HOST': '127.0.0.1', 'DB_PORT': str(PORTS['postgres']),
        'DB_USERNAME': 'dify_app', 'DB_PASSWORD': secret['pg_app'], 'DB_DATABASE': 'dify', 'DB_SSL_MODE': 'disable',
        'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(PORTS['redis']), 'REDIS_PASSWORD': secret['redis'], 'REDIS_DB': '0',
        'CELERY_BROKER_URL': redis + '/1', 'CELERY_WORKER_AMOUNT': '1', 'CELERY_AUTO_SCALE': 'false',
        'CELERY_QUEUES': '', 'DB_SQLALCHEMY_POOL_SIZE': '5', 'DB_SQLALCHEMY_MAX_OVERFLOW': '5',
        'VECTOR_STORE': 'qdrant', 'QDRANT_URL': f"http://127.0.0.1:{PORTS['qdrant']}",
        'QDRANT_API_KEY': secret['qdrant'], 'QDRANT_GRPC_ENABLED': 'false',
        'STORAGE_TYPE': 'opendal', 'OPENDAL_SCHEME': 'fs', 'OPENDAL_FS_ROOT': str(ROOT / 'data/dify/storage'),
        'STORAGE_LOCAL_PATH': str(ROOT / 'data/dify/storage'), 'CONSOLE_API_URL': public,
        'CONSOLE_WEB_URL': public, 'APP_WEB_URL': public, 'SERVICE_API_URL': public, 'FILES_URL': public,
        'INTERNAL_FILES_URL': f"http://127.0.0.1:{PORTS['api']}",
        'PLUGIN_DAEMON_URL': f"http://127.0.0.1:{PORTS['plugin']}", 'PLUGIN_DAEMON_KEY': secret['plugin'],
        'INNER_API_KEY_FOR_PLUGIN': secret['plugin_inner'],
        'CODE_EXECUTION_ENDPOINT': f"http://10.203.18.2:{PORTS['code']}", 'CODE_EXECUTION_API_KEY': secret['code'],
        'SSRF_PROXY_HTTP_URL': f"http://127.0.0.1:{PORTS['proxy']}",
        'SSRF_PROXY_HTTPS_URL': f"http://127.0.0.1:{PORTS['proxy']}",
        'AGENT_BACKEND_BASE_URL': f"http://127.0.0.1:{PORTS['agent']}", 'AGENT_BACKEND_API_TOKEN': secret['agent_api'],
        'ENABLE_AGENT_V2': 'true', 'ENABLE_AGENT_V2_IN_CHATFLOW': 'true', 'CHECK_UPDATE_URL': '',
        'DISABLE_TELEMETRY': 'true', 'DO_NOT_TRACK': 'true'}


def agent_environment(data: dict, secret: dict) -> dict:
    return {**safe_environment(data), 'DIFY_AGENT_REDIS_URL': f"redis://:{secret['redis']}@127.0.0.1:{PORTS['redis']}/2",
        'DIFY_AGENT_REDIS_PREFIX': 'aic-' + data['id'], 'DIFY_AGENT_PLUGIN_DAEMON_URL': f"http://127.0.0.1:{PORTS['plugin']}",
        'DIFY_AGENT_PLUGIN_DAEMON_API_KEY': secret['plugin'], 'DIFY_AGENT_INNER_API_URL': f"http://127.0.0.1:{PORTS['api']}",
        'DIFY_AGENT_INNER_API_KEY': secret['plugin_inner'], 'DIFY_AGENT_RUNTIME_BACKEND': 'local',
        'DIFY_AGENT_LOCAL_SANDBOX_ENDPOINT': f"http://10.203.17.2:{PORTS['shellctl']}",
        'DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN': secret['shellctl'],
        'DIFY_AGENT_LOCAL_SANDBOX_MATERIALIZED_HOME_ROOT': str(ROOT / 'jobs/home'),
        'DIFY_AGENT_LOCAL_SANDBOX_WORKSPACE_ROOT': str(ROOT / 'jobs/workspace'),
        # 上游local backend经shellctl复制快照；三类路径放同一任务根，控制操作无需放宽到应用根。
        'DIFY_AGENT_LOCAL_SANDBOX_HOME_SNAPSHOT_ROOT': str(ROOT / 'jobs/snapshots'),
        'DIFY_AGENT_STUB_API_BASE_URL': f"http://10.203.17.1:{PORTS['agent']}/agent-stub",
        'DIFY_AGENT_SANDBOX_FILES_BASE_URL': f"http://10.203.17.1:{PORTS['api']}",
        'DIFY_AGENT_SERVER_SECRET_KEY': secret['agent_root'], 'DIFY_AGENT_API_TOKEN': secret['agent_api']}


def plugin_environment(data: dict, secret: dict) -> dict:
    return {**safe_environment(data), 'SERVER_HOST': '127.0.0.1', 'SERVER_PORT': str(PORTS['plugin']),
        'SERVER_KEY': secret['plugin'], 'DB_TYPE': 'postgresql', 'DB_HOST': '127.0.0.1',
        'DB_PORT': str(PORTS['postgres']), 'DB_USERNAME': 'dify_app', 'DB_PASSWORD': secret['pg_app'],
        'DB_DATABASE': 'dify_plugin', 'DB_SSL_MODE': 'disable', 'REDIS_HOST': '127.0.0.1',
        'REDIS_PORT': str(PORTS['redis']), 'REDIS_PASSWORD': secret['redis'], 'REDIS_DB': '3',
        'REDIS_KEY_PREFIX': 'aic-plugin-' + data['id'], 'DIFY_INNER_API_URL': f"http://127.0.0.1:{PORTS['api']}",
        'DIFY_INNER_API_KEY': secret['plugin_inner'], 'PLUGIN_REMOTE_INSTALLING_HOST': '127.0.0.1',
        'PLUGIN_REMOTE_INSTALLING_PORT': str(PORTS['plugin_debug']), 'PLUGIN_WORKING_PATH': str(ROOT / 'data/plugin/cwd'),
        'PLUGIN_STORAGE_TYPE': 'local', 'PLUGIN_STORAGE_LOCAL_ROOT': str(ROOT / 'data/plugin'),
        'PLUGIN_INSTALLED_PATH': 'plugin', 'PLUGIN_PACKAGE_CACHE_PATH': 'plugin_packages', 'PLUGIN_MEDIA_CACHE_PATH': 'assets',
        'PYTHON_INTERPRETER_PATH': python313(), 'UV_PATH': str(ROOT / 'tooling/bin/uv'),
        'PIP_MIRROR_AUTO_DETECT': 'false', 'FORCE_VERIFYING_SIGNATURE': 'true',
        'PLATFORM': 'local', 'LOG_LEVEL': 'WARN'}


def python313() -> str:
    paths = list((ROOT / 'tooling/python').glob('cpython-3.13.14-linux-*/bin/python3.13'))
    if len(paths) != 1:
        raise RuntimeError('python313_runtime_missing')
    return str(paths[0])


def web_environment(data: dict) -> dict:
    public = f"http://127.0.0.1:{PORTS['web']}"
    # 字段沿用web/docker/entrypoint.sh，唯一适配是原生Node路径和回环监听。
    return {**safe_environment(data), 'NODE_ENV': 'production', 'DEPLOY_ENV': 'PRODUCTION',
        'PORT': str(PORTS['web_node']), 'HOSTNAME': '127.0.0.1', 'NEXT_PUBLIC_DEPLOY_ENV': 'PRODUCTION',
        'NEXT_PUBLIC_API_PREFIX': public + '/console/api', 'NEXT_PUBLIC_PUBLIC_API_PREFIX': public + '/api',
        'NEXT_PUBLIC_WEB_PREFIX': public, 'NEXT_PUBLIC_MARKETPLACE_API_PREFIX': 'https://marketplace.dify.ai/api/v1',
        'NEXT_PUBLIC_MARKETPLACE_URL_PREFIX': 'https://marketplace.dify.ai', 'NEXT_PUBLIC_SOCKET_URL': public,
        'NEXT_PUBLIC_ENABLE_AGENT_V2': 'true', 'NEXT_PUBLIC_ENABLE_AGENT_V2_IN_CHATFLOW': 'true',
        'NEXT_PUBLIC_ENABLE_FEATURE_PREVIEW': 'true', 'NEXT_PUBLIC_ENABLE_ACCESS_TOKEN': 'false',
        'NEXT_PUBLIC_BASE_PATH': '', 'NEXT_PUBLIC_COOKIE_DOMAIN': ''}


def tool_bridge_paths() -> tuple[str, ...]:
    """只读取已有工具登记常量，不导入平台、ORM或业务查询。"""
    source = Path(__file__).resolve().parents[2] / 'services/platform/agent_platform/modules/integrations/business_tools.py'
    tree = ast.parse(source.read_text('utf-8'))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'READ_TOOLS' for target in node.targets)]
    if len(assignments) != 1:
        raise RuntimeError('tool_bridge_read_contract_missing')
    paths = tuple(ast.literal_eval(assignments[0].value))
    if len(paths) != 15 or any(not re.fullmatch(r'/api/v1/[a-z/-]+(?:/\{id\})?(?:/[a-z-]+)?', path) for path in paths):
        raise RuntimeError('tool_bridge_read_contract_changed')
    return (*paths, '/api/v1/business-tools/openapi.json')


def tool_bridge_context() -> dict:
    """只读当前keeper和内核NAT地址；无HTTP、数据库或网络配置修改。"""
    import wsl_lifetime
    import wsl_network
    keeper = wsl_lifetime.linux_status()
    addresses = json.loads(wsl_network.run(['/usr/sbin/ip', '-j', '-4', 'address', 'show', 'dev', 'eth0']))
    routes = json.loads(wsl_network.run(['/usr/sbin/ip', '-j', '-4', 'route', 'show', 'default']))
    if (len(addresses) != 1 or addresses[0].get('ifname') != 'eth0' or 'UP' not in addresses[0].get('flags', [])
            or len(routes) != 1 or routes[0].get('dev') != 'eth0'):
        raise RuntimeError('tool_bridge_current_nat_ambiguous')
    entries = [item for item in addresses[0].get('addr_info', [])
        if item.get('family') == 'inet' and item.get('scope') == 'global']
    if len(entries) != 1:
        raise RuntimeError('tool_bridge_current_nat_ambiguous')
    return {**keeper, 'source_ip': entries[0]['local'], 'prefix_length': entries[0]['prefixlen'],
        'bind_ip': routes[0]['gateway']}


def validate_tool_bridge_network(data: dict, network: dict, current: dict) -> None:
    """同一运行实例、boot、活keeper和当前默认网关缺一不可，不能把任意私网当本机。"""
    source, host = ipaddress.IPv4Address(network['source_ip']), ipaddress.IPv4Address(network['bind_ip'])
    prefix = network['prefix_length']
    if (type(prefix) is not int or not 8 <= prefix <= 30 or source == host
            or host not in ipaddress.IPv4Network(f'{source}/{prefix}', strict=False)
            or any(not address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_unspecified for address in (source, host))):
        raise RuntimeError('tool_bridge_private_nat_invalid')
    if (network.get('distro') != DISTRO or network.get('root') != str(ROOT)
            or network.get('runtime_id') != data['id'] or network.get('boot_id') != data['boot_id']
            or data['boot_id'] != current.get('boot_id') or not current.get('running')
            or current.get('requires_boot_reconciliation') or not current.get('keeper_id')
            or any(network.get(key) != current.get(key) for key in
                ('runtime_id', 'keeper_id', 'source_ip', 'bind_ip', 'prefix_length'))):
        raise RuntimeError('tool_bridge_runtime_or_nat_changed')


def mounted_windows_path(value: str) -> Path:
    """仅转换本机盘符绝对路径；UNC、相对路径和上级跳转均不进入白名单。"""
    path = PureWindowsPath(value)
    if not re.fullmatch('[A-Za-z]:', path.drive) or not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('tool_bridge_windows_path_invalid')
    return Path('/mnt') / path.drive[0].lower() / Path(*path.parts[1:])


def verify_tool_bridge_snapshot(data: dict, path: Path) -> dict:
    """核Windows本项目桥预约及其SHA，再以Linux当前网络交叉核验；不读取任何令牌。"""
    project = Path(__file__).resolve().parents[2]
    folder = project / '.local/native_windows/wsl-http-bridge'
    if (not path.is_absolute() or path.resolve() != path.absolute() or path.parent != folder
            or not re.fullmatch(r'[a-f0-9]{32}\.json', path.name) or path.stat().st_size > 65536):
        raise RuntimeError('tool_bridge_snapshot_path_invalid')
    config = json.loads(path.read_text('utf-8'))
    native = json.loads((folder.parent / 'runtime.json').read_text('utf-8'))
    info = native.get('processes', {}).get('wsl-http-bridge', {})
    if (config.get('kind') != 'ai-agent-company-wsl-http-bridge-v1' or config.get('port') != 13002
            or mounted_windows_path(config['native_root']) != folder.parent
            or config.get('native_runtime_id') != native.get('id') or not info.get('ready')
            or mounted_windows_path(info.get('config_file', '')) != path
            or info.get('config_sha256') != file_sha(path) or info.get('network') != config.get('network')
            or info.get('upstreams') != config.get('upstreams')
            or not info.get('listener') or info.get('bridge_port') != 13002):
        raise RuntimeError('tool_bridge_windows_reservation_changed')
    validate_tool_bridge_network(data, config['network'], tool_bridge_context())
    return {'snapshot': str(path), 'snapshot_sha256': file_sha(path), 'network': config['network'],
        'windows_runtime_id': native['id'], 'bridge_launch_id': info['launch_id']}


def tool_bridge_acl(data: dict, binding: dict | None = None) -> str:
    """失效收据默认不产生例外；只放固定目的地址、端口、GET和现有工具路径。"""
    if binding is None:
        receipt = ROOT / 'tool-bridge.json'
        if not receipt.exists():
            return ''
        try:
            info = receipt.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('tool_bridge_receipt_owner_changed')
            binding = json.loads(receipt.read_text('utf-8'))
            if verify_tool_bridge_snapshot(data, Path(binding['snapshot'])) != binding['proof']:
                raise RuntimeError('tool_bridge_receipt_snapshot_changed')
        except (OSError, ValueError, KeyError, RuntimeError):
            return ''
    host = binding['proof']['network']['bind_ip']
    patterns = []
    for path in tool_bridge_paths():
        pattern = re.escape('/platform' + path).replace(r'\{id\}', '[A-Za-z0-9_-]{1,80}')
        patterns.append('^' + pattern + '([?].*)?$')
    return (f'acl aic_tools_dst dst {host}/32\nacl aic_tools_port port 13002\nacl aic_tools_method method GET\n'
        + 'acl aic_tools_path urlpath_regex ' + ' '.join(patterns) + '\n'
        + 'http_access allow aic_tools_dst aic_tools_port aic_tools_method aic_tools_path\n')


def squid_configuration(data: dict, *, special: bool, tool_acl: str = '') -> str:
    host, proxy_port = ('10.203.17.1', PORTS['agent_proxy']) if special else ('127.0.0.1', PORTS['proxy'])
    listens = f'http_port {host}:{proxy_port}\n'
    if not special:
        listens += f'http_port 10.203.18.1:{proxy_port}\n'
    controls = ('acl aic_control dst 10.203.17.1/32\n'
        f"acl aic_control_ports port {PORTS['api']} {PORTS['agent']}\nhttp_access allow aic_control aic_control_ports\n") if special else tool_acl
    content = listens + ('visible_hostname ai-agent-company\npid_filename none\ncache deny all\n'
        'access_log none\ncache_log /dev/stderr\ncache_store_log none\ncache_effective_user agentapp\n'
        'acl Safe_ports port 80 443\nacl SSL_ports port 443\nacl CONNECT method CONNECT\n')
    # 精确控制入口先允许，其他私有地址、metadata、回环和非常用端口全部拒绝。
    return content + controls + ('acl private_dst dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 '
        '169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4 ::1/128 fc00::/7 fe80::/10\n'
        'http_access deny private_dst\nhttp_access deny !Safe_ports\nhttp_access deny CONNECT !SSL_ports\n'
        'http_access allow all\n')


def configure_tool_bridge(snapshot: Path) -> dict:
    """显式更新本项目主代理文件；不重载进程、不接触Agent代理、数据库或模型。"""
    check_root()
    data = load()
    proof = verify_tool_bridge_snapshot(data, snapshot)
    binding = {'snapshot': str(snapshot), 'proof': proof, 'at': timestamp()}
    acl = tool_bridge_acl(data, binding)
    target = inside(ROOT / 'config/squid.conf')
    temporary = target.with_suffix('.conf.pending')
    if temporary.exists():
        raise RuntimeError('tool_bridge_pending_config_requires_review')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            handle.write(squid_configuration(data, special=False, tool_acl=acl))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        atomic_json(ROOT / 'tool-bridge.json', binding)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    update(lambda value: value.setdefault('tool_bridge_history', []).append({
        'at': binding['at'], 'bridge_launch_id': proof['bridge_launch_id'],
        'snapshot_sha256': proof['snapshot_sha256'], 'config_sha256': file_sha(target)}))
    return {'configured': True, 'requires_proxy_reload': True, 'agent_proxy_changed': False,
        'bind_ip': proof['network']['bind_ip'], 'port': 13002, 'methods': ['GET'],
        'config_sha256': file_sha(target)}


def write_configs(data: dict) -> None:
    config = ROOT / 'config'
    tool_acl = tool_bridge_acl(data)
    for name, special in [('squid', False), ('agent-squid', True)]:
        (config / (name + '.conf')).write_text(squid_configuration(data, special=special, tool_acl=tool_acl))
    proxy = f"proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; proxy_read_timeout 3600s; proxy_buffering off;"
    locations = ''.join(f'location {path} {{ {proxy} proxy_pass http://127.0.0.1:{PORTS["api"]}; }}\n'
        for path in ['/console/api', '/api', '/v1', '/files', '/mcp', '/triggers'])
    gateway = (f'user agentapp; worker_processes 1; pid {ROOT}/data/dify/nginx.pid; error_log stderr warn;\n'
        f'events {{ worker_connections 256; }}\nhttp {{ include /etc/nginx/mime.types; access_log off; client_max_body_size 100m;\n'
        f'client_body_temp_path {ROOT}/data/dify/nginx-body; proxy_temp_path {ROOT}/data/dify/nginx-proxy;\n'
        f'server {{ listen 127.0.0.1:{PORTS["web"]}; {locations}'
        f'location /socket.io/ {{ {proxy} proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_pass http://127.0.0.1:{PORTS["api"]}; }}'
        f'location / {{ {proxy} proxy_pass http://127.0.0.1:{PORTS["web_node"]}; }} }}\n'
        f'server {{ listen 10.203.17.1:{PORTS["agent"]}; location /agent-stub {{ {proxy} proxy_pass http://127.0.0.1:{PORTS["agent"]}; }} location / {{ return 403; }} }}\n'
        f'server {{ listen 10.203.17.1:{PORTS["api"]}; location / {{ {proxy} proxy_pass http://127.0.0.1:{PORTS["api"]}; }} }} }}\n')
    (config / 'nginx.conf').write_text(gateway)
    for path in config.iterdir():
        path.chmod(0o644)


def specs(data: dict, secret: dict) -> dict:
    source = ROOT / 'sources/dify'
    base = safe_environment(data)
    api = api_environment(data, secret)
    api['PATH'] = str(ROOT / 'venvs/api/bin') + ':' + api['PATH']
    import wsl_network
    networks = {item['kind']: item for item in wsl_network.plans(data['id'])}
    def spec(argv, *, env=base, user='agentapp', cwd=ROOT, port=None, namespace=None):
        return {'argv': [str(item) for item in argv], 'env': env, 'user': user, 'cwd': Path(cwd), 'port': port, 'namespace': namespace}
    code_env = {**base, 'API_KEY': secret['code'], 'SANDBOX_PORT': str(PORTS['code']), 'ENABLE_NETWORK': 'true',
        'PYTHON_PATH': python313(), 'NODEJS_PATH': str(ROOT / 'tooling/node-sandbox-20.11.1/bin/node'),
        'HTTP_PROXY': f"http://10.203.18.1:{PORTS['proxy']}", 'HTTPS_PROXY': f"http://10.203.18.1:{PORTS['proxy']}"}
    # 上游默认依赖路径指向Python3.14；使用已有3.13时显式复制匹配stdlib，不混入其他环境。
    python_lib = str(Path(python313()).parent.parent / 'lib/python3.13')
    code_env['PYTHON_LIB_PATH'] = ','.join([python_lib, '/usr/lib/x86_64-linux-gnu',
        '/etc/ssl/certs/ca-certificates.crt', '/etc/nsswitch.conf', '/etc/hosts', '/etc/resolv.conf',
        '/run/systemd/resolve/stub-resolv.conf', '/etc/localtime', '/usr/share/zoneinfo', '/etc/timezone', '/etc/passwd'])
    shell_env = {**base, 'HOME': str(ROOT / 'jobs/home'), 'SHELLCTL_AUTH_TOKEN': secret['shellctl'],
        'SHELLCTL_ENABLE_PATH_ISOLATION': 'true', 'SHELLCTL_LANDLOCK_RO_PATHS': f'/usr,/bin,/sbin,/lib,/lib64,/etc,/proc,{ROOT}/bin,{ROOT}/tooling',
        'HTTP_PROXY': f"http://10.203.17.1:{PORTS['agent_proxy']}", 'HTTPS_PROXY': f"http://10.203.17.1:{PORTS['agent_proxy']}", 'NO_PROXY': ''}
    # Redis默认改写进程标题会覆盖/proc原始环境区域，导致启动标签无法再被精确核验。
    redis_config = f"bind 127.0.0.1\nport {PORTS['redis']}\nprotected-mode yes\nrequirepass {secret['redis']}\ndir {ROOT}/data/redis\nappendonly yes\ndaemonize no\nset-proc-title no\n"
    values = {
        'postgres': spec(['/usr/lib/postgresql/16/bin/postgres', '-D', ROOT / 'data/postgres'], user='agentpg', port=PORTS['postgres']),
        'redis': {**spec(['/usr/bin/redis-server', '-'], user='agentcache', port=PORTS['redis']), 'stdin': redis_config.encode()},
        'qdrant': spec([ROOT / 'bin/qdrant'], cwd=ROOT / 'data/qdrant', port=PORTS['qdrant'], env={**base,
            'QDRANT__SERVICE__HOST': '127.0.0.1', 'QDRANT__SERVICE__HTTP_PORT': str(PORTS['qdrant']),
            'QDRANT__SERVICE__GRPC_PORT': str(PORTS['qdrant_grpc']), 'QDRANT__SERVICE__API_KEY': secret['qdrant'],
            'QDRANT__STORAGE__STORAGE_PATH': str(ROOT / 'data/qdrant'),
            'QDRANT__STORAGE__SNAPSHOTS_PATH': str(ROOT / 'data/qdrant/snapshots'),
            'QDRANT__TELEMETRY_DISABLED': 'true'}),
        'proxy': spec(['/usr/sbin/squid', '-N', '-f', ROOT / 'config/squid.conf'], port=PORTS['proxy']),
        'agent_proxy': spec(['/usr/sbin/squid', '-N', '-f', ROOT / 'config/agent-squid.conf'], port=PORTS['agent_proxy']),
        'shellctl': spec([ROOT / 'bin/shellctl', 'serve', '--listen', f"0.0.0.0:{PORTS['shellctl']}", '--require-auth-token'],
            user='agentjob', env=shell_env, cwd=ROOT / 'jobs/home', port=PORTS['shellctl'], namespace=networks['agent']['name']),
        'code': spec(['/usr/bin/python3', ROOT / 'control/wsl_sandbox_entry.py', str(ROOT)], env=code_env,
            user='root', port=PORTS['code'], namespace=networks['code']['name']),
        'plugin': spec([ROOT / 'bin/plugin-daemon'], env=plugin_environment(data, secret), cwd=ROOT / 'data/plugin', port=PORTS['plugin']),
        'agent': spec([ROOT / 'venvs/agent/bin/uvicorn', 'dify_agent.server.app:app', '--host', '127.0.0.1', '--port', str(PORTS['agent'])],
            env=agent_environment(data, secret), cwd=source / 'dify-agent', port=PORTS['agent']),
        'api': spec(['/bin/bash', source / 'api/docker/entrypoint.sh'], env=api, cwd=source / 'api', port=PORTS['api']),
        'worker': spec(['/bin/bash', source / 'api/docker/entrypoint.sh'], env={**api, 'MODE': 'worker'}, cwd=source / 'api'),
        # 沿用原生beat命令，只把必须写入的schedule文件移出只读源码目录。
        'beat': spec([ROOT / 'venvs/api/bin/celery', '-A', 'app.celery', 'beat', '--loglevel', 'WARNING',
            '--schedule', ROOT / 'data/dify/celerybeat-schedule'], env={**api, 'MODE': 'beat'}, cwd=source / 'api'),
        'web': {**spec([ROOT / 'tooling/node/bin/node', ROOT / 'web/web/server.js'], env=web_environment(data), cwd=ROOT / 'web/web', port=PORTS['web_node']), 'supervise': True},
        'gateway': {**spec(['/usr/sbin/nginx', '-c', ROOT / 'config/nginx.conf', '-g', 'daemon off;'], user='root', port=PORTS['web']), 'supervise': True},
    }
    return values


def owned_process(info: dict, data: dict):
    """PID/创建时间/会话/UID/可执行文件/运行标签均一致才认领，绝不凭端口猜归属。"""
    try:
        process = psutil.Process(info['pid'])
        if abs(process.create_time() - info['created_at']) > 0.01 or process.uids().real not in info['allowed_uids']:
            return None
        if process.exe() not in info['allowed_executables'] or os.getpgid(process.pid) != info['process_group']:
            return None
        environment = process.environ()
        if any(environment.get(key) != expected for key, expected in [('AGENT_WSL_RUNTIME_ID', data['id']),
                ('AGENT_WSL_LAUNCH_ID', info['launch_id']), ('AGENT_WSL_ROLE', info['role'])]):
            return None
        import wsl_cgroups
        if not wsl_cgroups.contains(info['cgroup'], process.pid):
            return None
        if info.get('namespace') and Path('/proc', str(process.pid), 'ns/net').stat().st_ino != info['namespace_inode']:
            return None
        return process
    except (psutil.Error, ProcessLookupError, FileNotFoundError):
        return None


def stop_role(name: str, *, expected_launch=None, seconds=10) -> dict:
    data = load()
    info = data['processes'].get(name)
    if not info:
        return {'role': name, 'verified': True, 'stopped': True, 'not_started': True}
    if expected_launch and info.get('launch_id') != expected_launch:
        raise RuntimeError('stop_launch_compare_and_swap_failed')
    import wsl_cgroups
    group = info['cgroup']
    if group['runtime_id'] != data['id'] or group['launch_id'] != info['launch_id'] or group['role'] != name:
        raise RuntimeError('stop_cgroup_compare_and_swap_failed')
    # 组长退出不等于整个服务退出；只用已登记且inode一致的专属cgroup清理子孙。
    return {'role': name, **wsl_cgroups.stop(group, seconds=seconds), 'data_preserved': True}


def spawn(name: str, spec: dict, data: dict, launch: str) -> dict:
    if spec.get('port') and not spec.get('namespace'):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', spec['port']))
    import wsl_cgroups
    current = load()
    if not current.get('cgroup_root_inode'):
        inode = wsl_cgroups.prepare_root(data['id'])
        update(lambda d: d.update(cgroup_root_inode=inode))
    else:
        inode = wsl_cgroups.prepare_root(data['id'], current['cgroup_root_inode'])
    group = wsl_cgroups.create(data['id'], launch, name, inode)
    folder = ROOT / 'process-launches' / launch
    folder.mkdir(mode=0o700, exist_ok=True)
    atomic_json(folder / (name + '.json'), group)
    identity = account(spec['user'])
    argv = [str(ROOT / 'tooling/bin/python'), str(ROOT / 'control/wsl_process.py'),
        '--role', name, '--launch', launch, '--user', spec['user'], '--cwd', str(spec['cwd'])]
    if spec.get('namespace'):
        argv.extend(['--namespace', spec['namespace']])
    if spec.get('supervise'):
        argv.append('--supervise')
    argv.extend(['--', *spec['argv']])
    allowed = {str(Path(argv[0]).resolve()), str(Path(spec['argv'][0]).resolve())}
    if name in {'api', 'worker', 'beat'}:
        allowed.add(str((ROOT / 'venvs/api/bin/python').resolve()))
    if name == 'agent':
        allowed.add(str((ROOT / 'venvs/agent/bin/python').resolve()))
    if name == 'code':
        allowed.update({str(Path('/usr/bin/unshare').resolve()), str((ROOT / 'bin/code-sandbox').resolve())})
    process = None
    with (ROOT / 'logs' / (name + '.log')).open('ab') as logfile:
        try:
            process = subprocess.Popen(argv, cwd=spec['cwd'], env={**spec['env'],
                'AGENT_WSL_LAUNCH_ID': launch, 'AGENT_WSL_ROLE': name}, user=0,
                group=0, extra_groups=[], start_new_session=True,
                stdin=subprocess.PIPE if spec.get('stdin') else subprocess.DEVNULL, stdout=logfile, stderr=logfile)
            observed = psutil.Process(process.pid)
            info = {'pid': process.pid, 'created_at': observed.create_time(), 'executable': observed.exe(),
                'uid': observed.uids().real, 'process_group': os.getpgid(process.pid), 'launch_id': launch,
                'role': name, 'namespace': spec.get('namespace'), 'allowed_executables': sorted(allowed),
                'allowed_uids': sorted({0, identity.pw_uid}), 'service_uid': identity.pw_uid, 'cgroup': group,
                'argv_sha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest()}
            if spec.get('namespace'):
                info['namespace_inode'] = Path('/run/netns', spec['namespace']).stat().st_ino
            update(lambda d: d['processes'].update({name: info}))
            if spec.get('stdin'):
                process.stdin.write(spec['stdin'])
                process.stdin.close()
            # 包装器在exec前先加入独占cgroup；启动过渡只接受预先固定的解释器和服务身份。
            ready_deadline = time.monotonic() + 5
            while time.monotonic() < ready_deadline:
                if process.poll() is not None:
                    raise RuntimeError('native_process_exited_before_ready')
                if owned_process(info, data):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError('native_process_ownership_not_ready')
            observed = psutil.Process(process.pid)
            info.update(executable=observed.exe(), uid=observed.uids().real)
            if spec.get('namespace'):
                info['namespace_inode'] = Path('/proc', str(process.pid), 'ns/net').stat().st_ino
            update(lambda d: d['processes'].update({name: info}))
            return info
        except BaseException as error:
            try:
                cleanup = wsl_cgroups.stop(group, seconds=5)
            except Exception as stop_error:
                cleanup = {'stopped': False, 'verified': False, 'error_type': type(stop_error).__name__}
            # 进入cgroup前失败的包装器仍由本次Popen句柄/出生时间/标签精确回收。
            if process and process.poll() is None:
                candidate = psutil.Process(process.pid)
                env = candidate.environ()
                if candidate.exe() == str(Path(argv[0]).resolve()) and all(env.get(key) == value for key, value in
                        [('AGENT_WSL_RUNTIME_ID', data['id']), ('AGENT_WSL_LAUNCH_ID', launch), ('AGENT_WSL_ROLE', name)]):
                    process.terminate()
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                else:
                    cleanup.update(stopped=False, wrapper_identity_verified=False)
            if process and process.poll() is None:
                cleanup['stopped'] = False
            update(lambda d: d['failures'].append({'phase': 'spawn', 'role': name, 'launch_id': launch,
                'error_type': type(error).__name__, 'cleanup': cleanup, 'at': timestamp()}))
            if not cleanup.get('stopped'):
                raise RuntimeError('spawn_failure_cleanup_incomplete') from error
            raise


def namespace_listening_ports(pid: int, proc_root=Path('/proc')) -> set[int]:
    """跨netns读目标PID连接表，必须与该进程实际持有的socket inode相交。"""
    folder = proc_root / str(pid)
    sockets = set()
    for descriptor in (folder / 'fd').iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        if target.startswith('socket:[') and target.endswith(']'):
            sockets.add(target[8:-1])
    ports = set()
    for protocol in ('tcp', 'tcp6'):
        table = folder / 'net' / protocol
        if not table.exists():
            continue
        for line in table.read_text().splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 10 and columns[3] == '0A' and columns[9] in sockets:
                ports.add(int(columns[1].rsplit(':', 1)[1], 16))
    return ports


def wait_listening(name: str, *, seconds=45) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data = load()
        info = data['processes'][name]
        process = owned_process(info, data)
        if not process:
            raise RuntimeError('owned_service_exited_or_changed')
        expected = PORTS.get(name)
        if name == 'web': expected = PORTS['web_node']
        if name == 'gateway': expected = PORTS['web']
        if name in {'worker', 'beat'}:
            return
        processes = [process, *process.children(recursive=True)]
        for candidate in processes:
            if info.get('namespace'):
                import wsl_cgroups
                if wsl_cgroups.contains(info['cgroup'], candidate.pid) and expected in namespace_listening_ports(candidate.pid):
                    return
                continue
            for connection in candidate.net_connections(kind='tcp'):
                if connection.status == psutil.CONN_LISTEN and connection.laddr.port == expected:
                    desired_host = '10.203.17.1' if name == 'agent_proxy' else '127.0.0.1'
                    if not info.get('namespace') and connection.laddr.ip != desired_host:
                        if name == 'proxy' and connection.laddr.ip == '10.203.18.1':
                            continue
                        raise RuntimeError('unexpected_non_loopback_listener')
                    return
        time.sleep(0.2)
    raise TimeoutError('service_listening_deadline')


def init_data() -> dict:
    check_root()
    data = load()
    if data['boot_id'] != boot_identity():
        raise RuntimeError('wsl_boot_changed_requires_reconcile')
    if data['databases_initialized']:
        if not data.get('plugin_database_initialized'):
            raise RuntimeError('plugin_database_requires_explicit_init_plugin_data')
        return {'already_initialized': True, 'plugin_database_initialized': True}
    if data['database_initialization_attempted']:
        raise RuntimeError('previous_database_initialization_requires_review')
    pgdata = ROOT / 'data/postgres'
    if any(pgdata.iterdir()):
        raise RuntimeError('existing_postgres_data_not_adopted')
    update(lambda d: d.update(database_initialization_attempted=True))
    secret = vault()
    launch = uuid4().hex
    password = ROOT / 'secrets/initdb-password'
    identity = account('agentpg')
    try:
        password.write_text(secret['pg_admin'])
        os.chown(password, identity.pw_uid, identity.pw_gid)
        password.chmod(0o600)
        # 密码文件父目录只给PG身份临时遍历，不把其他根密文交给应用或任务用户。
        directory(ROOT / 'init', 'agentpg', 0o700)
        pg_password = ROOT / 'init/password'
        password.replace(pg_password)
        subprocess.run(['/usr/lib/postgresql/16/bin/initdb', '-D', str(pgdata), '-U', 'agent_admin',
            '--auth-local=scram-sha-256', '--auth-host=scram-sha-256', '--pwfile', str(pg_password)],
            user=identity.pw_uid, group=identity.pw_gid, extra_groups=[], check=True, timeout=60,
            capture_output=True, env=safe_environment(data))
        pg_password.unlink()
        with (pgdata / 'postgresql.conf').open('a') as handle:
            handle.write(f"\nlisten_addresses='127.0.0.1'\nport={PORTS['postgres']}\nunix_socket_directories='{pgdata}'\n")
        spawn('postgres', specs(data, secret)['postgres'], data, launch)
        wait_listening('postgres')
        # 使用已核本机新PG进程、固定端口和全新库；只做部署初始化，不查询旧/外部数据库。
        db_script = """import os, psycopg2\nfrom psycopg2 import sql\nc=psycopg2.connect(host='127.0.0.1',port=25432,user='agent_admin',password=os.environ['AIC_PG_ADMIN'],dbname='postgres',connect_timeout=3)\nc.autocommit=True\nwith c.cursor() as q:\n q.execute(sql.SQL('CREATE ROLE dify_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(os.environ['AIC_PG_APP'])))\n for name in ('dify','dify_plugin'):\n  q.execute(sql.SQL('CREATE DATABASE {} OWNER dify_app').format(sql.Identifier(name)))\nc.close()\n"""
        subprocess.run([str(ROOT / 'venvs/api/bin/python'), '-c', db_script], check=True, timeout=15,
            capture_output=True, env={**safe_environment(data), 'AIC_PG_ADMIN': secret['pg_admin'], 'AIC_PG_APP': secret['pg_app']})
        # 上游upgrade-db先获取Redis分布式锁，不能只启动PG便开始迁移。
        spawn('redis', specs(data, secret)['redis'], data, launch)
        wait_listening('redis')
        result = migrate_databases(data, secret)
        return {**result, **initialize_plugin_database(load(), secret, launch)}
    except BaseException as error:
        update(lambda d: d['failures'].append({'phase': 'init-data', 'error_type': type(error).__name__, 'at': timestamp()}))
        raise
    finally:
        password.unlink(missing_ok=True)
        (ROOT / 'init/password').unlink(missing_ok=True)
        cleanup_initialization(launch)


def migrate_databases(data: dict, secret: dict) -> dict:
    """fresh和受控空库恢复共用上游迁移，300秒内单次调用，不捕获后自动重放。"""
    environment = api_environment(data, secret)
    environment['PATH'] = str(ROOT / 'venvs/api/bin') + ':' + environment['PATH']
    environment['PGOPTIONS'] = '-c statement_timeout=120000 -c lock_timeout=10000'
    app = account('agentapp')
    try:
        migration = subprocess.run([str(ROOT / 'venvs/api/bin/flask'), 'upgrade-db'],
            cwd=ROOT / 'sources/dify/api', user=app.pw_uid, group=app.pw_gid, extra_groups=[],
            env=environment, check=True, timeout=300, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        save_migration_log(error.stdout, error.stderr, secret)
        raise
    save_migration_log(migration.stdout, migration.stderr, secret)
    update(lambda d: d.update(databases_initialized=True, initialized_at=timestamp(), pg_major=16))
    return {'databases_initialized': True, 'postgres_major': 16, 'databases': ['dify', 'dify_plugin'],
        'migration': 'upstream_flask_upgrade_db', 'external_database_connections': 0}


def validate_plugin_cluster_files(data: dict) -> bool:
    """核同一prepare的新PG目录；可复用精确所属的运行实例，不接管陌生监听或旧PID。"""
    receipt = json.loads(inside(ROOT / 'prepare-attempt.json').read_text())
    if receipt.get('status') != 'ready' or receipt.get('runtime_id') != data['id']:
        raise RuntimeError('plugin_initialization_prepare_receipt_changed')
    pgdata = inside(ROOT / 'data/postgres')
    owner = account('agentpg').pw_uid
    for path in (pgdata, pgdata / 'PG_VERSION', pgdata / 'postgresql.conf', pgdata / 'global/pg_control'):
        if inside(path).stat().st_uid != owner:
            raise RuntimeError('plugin_initialization_postgres_owner_changed')
    if (pgdata / 'PG_VERSION').read_text().strip() != '16':
        raise RuntimeError('plugin_initialization_postgres_version_changed')
    import wsl_cgroups
    plugin = data['processes'].get('plugin')
    if plugin and (wsl_cgroups.live_members(plugin['cgroup']) or owned_process(plugin, data)):
        raise RuntimeError('plugin_initialization_requires_stopped_plugin')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORTS['plugin']))
    info = data['processes'].get('postgres')
    process = owned_process(info, data) if info else None
    if process:
        if process.uids().real != owner:
            raise RuntimeError('plugin_initialization_postgres_identity_changed')
        return True
    if info and wsl_cgroups.live_members(info['cgroup']):
        raise RuntimeError('plugin_initialization_postgres_identity_changed')
    if (pgdata / 'postmaster.pid').exists():
        raise RuntimeError('plugin_initialization_existing_postmaster_not_adopted')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORTS['postgres']))
    return False


def plugin_database_probe_script() -> str:
    """插件空库初始化预检：两连接、八条系统目录SELECT，每条先EXPLAIN，不读主业务表。"""
    return r'''
import json, os, psycopg2

def check(condition, code):
    if not condition:
        raise RuntimeError(code)

queries = 0
def rows(cursor, query):
    global queries
    cursor.execute('EXPLAIN ' + query)
    cursor.fetchall()
    cursor.execute(query)
    queries += 2
    return cursor.fetchall()

def connect(database):
    return psycopg2.connect(host='127.0.0.1', port=25432, user='agent_admin',
        password=os.environ['AIC_PG_ADMIN'], dbname=database, connect_timeout=3,
        options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000')

def identity(cursor, database):
    value = rows(cursor, "SELECT current_setting('data_directory'), current_setting('port'), current_user, current_database(), host(inet_server_addr()), current_setting('server_version_num')::int")
    check(len(value) == 1 and value[0][:5] == (os.environ['AIC_EXPECTED_PGDATA'], '25432', 'agent_admin', database, '127.0.0.1')
        and 160000 <= value[0][5] < 170000, 'plugin_initialization_server_identity_changed')

with connect('postgres') as connection:
    with connection.cursor() as cursor:
        identity(cursor, 'postgres')
        databases = rows(cursor, "SELECT datname, pg_get_userbyid(datdba), datistemplate FROM pg_database ORDER BY datname")
        check(databases == [('dify', 'dify_app', False), ('dify_plugin', 'dify_app', False),
            ('postgres', 'agent_admin', False), ('template0', 'agent_admin', True), ('template1', 'agent_admin', True)],
            'plugin_initialization_databases_or_owners_changed')
        roles = rows(cursor, "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname")
        check(len(roles) == 2 and roles[0][:3] == ('agent_admin', True, True)
            and roles[1] == ('dify_app', True, False, False, False, False, False), 'plugin_initialization_roles_changed')

with connect('dify_plugin') as connection:
    with connection.cursor() as cursor:
        identity(cursor, 'dify_plugin')
        schemas = rows(cursor, "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' ORDER BY nspname")
        check(schemas == [('public', 'pg_database_owner')], 'plugin_initialization_schema_owner_changed')
        relations = rows(cursor, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'")
        functions = rows(cursor, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'")
        extensions = rows(cursor, "SELECT extname FROM pg_extension ORDER BY extname")
        check(relations == [(0,)] and functions == [(0,)] and extensions == [('plpgsql',)], 'plugin_initialization_schema_not_empty')
print(json.dumps({'verified': True, 'connections': 2, 'metadata_statements': queries,
    'database': 'dify_plugin', 'empty_business_schema': True}))
'''


def verify_plugin_database(data: dict, secret: dict) -> dict:
    """仅在文件、进程及UID核验后连接固定新PG；失败不另查其他库或重试。"""
    current = load()
    info = current['processes'].get('postgres')
    candidate = owned_process(info, current) if info else None
    if not candidate or candidate.uids().real != account('agentpg').pw_uid:
        raise RuntimeError('plugin_initialization_postgres_identity_changed')
    try:
        result = subprocess.run([str(ROOT / 'venvs/api/bin/python'), '-c', plugin_database_probe_script()],
            check=True, timeout=25, capture_output=True, env={**safe_environment(data),
                'AIC_PG_ADMIN': secret['pg_admin'], 'AIC_EXPECTED_PGDATA': str(ROOT / 'data/postgres')})
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stderr or b'').decode('utf-8', errors='replace')
        for suffix in ('server_identity_changed', 'databases_or_owners_changed', 'roles_changed',
                'schema_owner_changed', 'schema_not_empty'):
            code = 'plugin_initialization_' + suffix
            if code in diagnostic:
                raise RuntimeError(code) from error
        raise
    evidence = json.loads(result.stdout)
    if evidence != {'verified': True, 'connections': 2, 'metadata_statements': 16,
            'database': 'dify_plugin', 'empty_business_schema': True}:
        raise RuntimeError('plugin_initialization_preflight_receipt_invalid')
    return evidence


def initialize_plugin_database(data: dict, secret: dict, launch: str) -> dict:
    """fresh与单独补初始化共用官方CLI；每个runtime只预约一次，失败历史不得自动覆盖。"""
    if data.get('plugin_database_initialized'):
        return {'plugin_database_initialized': True, 'already_initialized': True}
    previous = data.get('plugin_database_initialization')
    if previous and (previous.get('status'), previous.get('phase'), previous.get('launch_id'), previous.get('runtime_id')) != (
            'attempted', 'dependencies', launch, data['id']):
        raise RuntimeError('previous_plugin_database_initialization_requires_review')
    if not data.get('databases_initialized'):
        raise RuntimeError('plugin_initialization_requires_main_database')
    receipt = plugin_cli_receipt()
    if not validate_plugin_cluster_files(data):
        raise RuntimeError('plugin_initialization_postgres_not_running')
    record = {'status': 'attempted', 'runtime_id': data['id'], 'launch_id': launch,
        'phase': 'preflight-attempted', 'at': timestamp(), 'cli_sha256': receipt['binary_sha256']}
    update(lambda d: d.update(plugin_database_initialization=record))
    log_name = 'init-plugin-data-' + launch + '.log'
    try:
        evidence = verify_plugin_database(data, secret)
        cwd = inside(ROOT / 'config/plugin-migration')
        directory(cwd, 'root', 0o755)
        # 上游CLI会读取cwd/.env和HOME/.dify.yaml；迁移目录只许root写且不含这两种文件。
        if any(cwd.iterdir()):
            raise RuntimeError('plugin_initialization_unexpected_configuration_file')
        environment = {**plugin_environment(data, secret), 'HOME': str(cwd),
            'DB_DEFAULT_DATABASE': 'dify_plugin', 'DB_MAX_OPEN_CONNS': '1', 'DB_MAX_IDLE_CONNS': '1',
            'DB_EXTRAS': '-c statement_timeout=120000 -c lock_timeout=10000'}
        update(lambda d: d['plugin_database_initialization'].update(phase='migration-attempted', preflight=evidence))
        identity = account('agentapp')
        try:
            result = subprocess.run([str(ROOT / 'bin/plugin-commandline'), 'migrate'], cwd=cwd,
                env=environment, user=identity.pw_uid, group=identity.pw_gid, extra_groups=[],
                check=True, timeout=120, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            save_migration_log(error.stdout, error.stderr, secret, filename=log_name)
            raise
        save_migration_log(result.stdout, result.stderr, secret, filename=log_name)
        update(lambda d: (d.update(plugin_database_initialized=True),
            d['plugin_database_initialization'].update(status='ready', finished_at=timestamp(), log=log_name,
                log_sha256=file_sha(ROOT / 'logs' / log_name))))
        return {'plugin_database_initialized': True, 'plugin_migration': 'upstream_commandline_migrate',
            'plugin_database': 'dify_plugin', 'preflight': evidence, 'external_database_connections': 0}
    except BaseException as error:
        update(lambda d: (d['plugin_database_initialization'].update(status='failed', error_type=type(error).__name__),
            d['failures'].append({'phase': 'init-plugin-data', 'launch_id': launch,
                'error_type': type(error).__name__, 'at': timestamp()})))
        raise


def init_plugin_data() -> dict:
    """给已迁移主库补插件新空库；复用运行PG，其余服务和所有已有数据保持原归属。"""
    check_root()
    data = load()
    if data['boot_id'] != boot_identity():
        raise RuntimeError('wsl_boot_changed_requires_reconcile')
    if data.get('plugin_database_initialized'):
        return {'plugin_database_initialized': True, 'already_initialized': True}
    if not data.get('databases_initialized') or data.get('plugin_database_initialization'):
        raise RuntimeError('main_database_not_ready_or_previous_plugin_initialization_requires_review')
    receipt = plugin_cli_receipt()
    running = validate_plugin_cluster_files(data)
    launch = uuid4().hex
    # 依赖启动前也先预约；中断或spawn失败都不能在下次命令中被当成从未尝试。
    update(lambda d: d.update(plugin_database_initialization={'status': 'attempted', 'phase': 'dependencies',
        'runtime_id': data['id'], 'launch_id': launch, 'cli_sha256': receipt['binary_sha256'], 'at': timestamp()}))
    try:
        secret = vault()
        if not running:
            spawn('postgres', specs(data, secret)['postgres'], data, launch)
            wait_listening('postgres')
        return initialize_plugin_database(load(), secret, launch)
    except BaseException as error:
        if load()['plugin_database_initialization']['status'] == 'attempted':
            update(lambda d: (d['plugin_database_initialization'].update(status='failed', error_type=type(error).__name__),
                d['failures'].append({'phase': 'init-plugin-data', 'launch_id': launch,
                    'error_type': type(error).__name__, 'at': timestamp()})))
        raise
    finally:
        # 既有PG可能还服务其他已启动角色，只回收本次launch实际创建的进程。
        if not running:
            cleanup_initialization(launch)


def cleanup_initialization(launch: str) -> None:
    """两项依赖独立回收；Redis停止失败不能阻止PG停止，归属改变时绝不接管。"""
    failures = []
    for role in ('redis', 'postgres'):
        try:
            if load()['processes'].get(role, {}).get('launch_id') != launch:
                continue
            cleanup = stop_role(role, expected_launch=launch)
            if not cleanup.get('stopped'):
                failures.append({'role': role, 'cleanup': cleanup})
        except Exception as error:
            failures.append({'role': role, 'error_type': type(error).__name__})
    if failures:
        update(lambda d: d['failures'].append({'phase': 'init-data-cleanup', 'launch_id': launch,
            'cleanup': failures, 'at': timestamp()}))
        raise RuntimeError('database_initialization_cleanup_incomplete')


def reviewed_redis_spawn_failure(data: dict, reviewed_failed_launch: str) -> dict:
    """只允许人工指定的一次Redis标题改写故障续作，拒绝预检/迁移已尝试或二次续作。"""
    previous = data.get('empty_migration_recovery', {})
    if not reviewed_failed_launch or previous.get('launch_id') != reviewed_failed_launch or previous.get('status') != 'failed':
        raise RuntimeError('reviewed_redis_spawn_failure_identity_required')
    if previous.get('preflight') or previous.get('history') or previous.get('phase', 'dependencies') != 'dependencies':
        raise RuntimeError('reviewed_redis_spawn_failure_must_precede_sql')
    events = [event for event in data.get('failures', []) if event.get('launch_id') == reviewed_failed_launch]
    if len(events) != 2:
        raise RuntimeError('reviewed_redis_spawn_failure_evidence_incomplete')
    spawn_event, failure = events
    cleanup = spawn_event.get('cleanup', {})
    if (spawn_event.get('phase'), spawn_event.get('role'), spawn_event.get('error_type')) != ('spawn', 'redis', 'RuntimeError'):
        raise RuntimeError('reviewed_redis_spawn_failure_evidence_incomplete')
    if not cleanup.get('verified') or not cleanup.get('stopped') or cleanup.get('alive_pids') != []:
        raise RuntimeError('reviewed_redis_spawn_failure_cleanup_not_verified')
    if (failure.get('phase'), failure.get('error_type')) != ('recover-empty-migration', 'RuntimeError'):
        raise RuntimeError('reviewed_redis_spawn_failure_evidence_incomplete')
    return previous


def missing_redis_failure(data: dict, expected_runtime_id: str, log_sha256: str,
        reviewed_failed_launch: str | None = None) -> bytes:
    """仅接受人工审阅并固定摘要的本机迁移锁缺Redis堆栈，不接受一般SQL/超时失败。"""
    if data['id'] != expected_runtime_id or len(log_sha256) != 64 or any(c not in '0123456789abcdef' for c in log_sha256):
        raise RuntimeError('empty_recovery_identity_or_log_digest_required')
    if data['databases_initialized'] or not data['database_initialization_attempted']:
        raise RuntimeError('empty_recovery_requires_failed_initialization')
    if data.get('empty_migration_recovery') and not reviewed_failed_launch:
        raise RuntimeError('previous_empty_migration_recovery_requires_review')
    failures = data.get('failures', [])
    if reviewed_failed_launch:
        previous = reviewed_redis_spawn_failure(data, reviewed_failed_launch)
        if previous.get('runtime_id') != expected_runtime_id or previous.get('failure_log_sha256') != log_sha256:
            raise RuntimeError('reviewed_redis_spawn_failure_original_identity_changed')
        originals = [event for event in failures if event.get('phase') == 'init-data']
    else:
        originals = failures[-1:]
    if len(originals) != 1 or originals[0].get('phase') != 'init-data' or originals[0].get('error_type') != 'CalledProcessError':
        raise RuntimeError('empty_recovery_requires_missing_redis_failure')
    path = inside(ROOT / 'logs/init-data-migration.log')
    if path.stat().st_size > 256 * 1024:
        raise RuntimeError('migration_log_budget_exceeded')
    content = path.read_bytes()
    text = content.decode('utf-8', errors='replace')
    required = ['db_migration_lock.py', 'lock.acquire(', '127.0.0.1:26379', 'Connection refused']
    if hashlib.sha256(content).hexdigest() != log_sha256 or not all(value in text for value in required):
        raise RuntimeError('empty_recovery_requires_missing_redis_failure')
    if any(value in text for value in ['Running upgrade ', 'statement timeout', 'lock timeout', 'OperationalError', 'psycopg2.errors.']):
        raise RuntimeError('empty_recovery_migration_or_sql_failure_not_allowed')
    return content


def validate_empty_cluster_files(data: dict) -> None:
    """在连接前核专属prepare收据、PG目录所有者和无存活旧进程，不凭端口名认领数据库。"""
    receipt = json.loads(inside(ROOT / 'prepare-attempt.json').read_text())
    if receipt.get('status') != 'ready' or receipt.get('runtime_id') != data['id']:
        raise RuntimeError('empty_recovery_prepare_receipt_changed')
    pgdata = inside(ROOT / 'data/postgres')
    owner = account('agentpg').pw_uid
    for path in (pgdata, pgdata / 'PG_VERSION', pgdata / 'postgresql.conf', pgdata / 'global/pg_control'):
        if inside(path).stat().st_uid != owner:
            raise RuntimeError('empty_recovery_postgres_owner_changed')
    if (pgdata / 'PG_VERSION').read_text().strip() != '16' or (pgdata / 'postmaster.pid').exists():
        raise RuntimeError('empty_recovery_cluster_not_stopped_pg16')
    import wsl_cgroups
    for info in data['processes'].values():
        if wsl_cgroups.live_members(info['cgroup']) or owned_process(info, data):
            raise RuntimeError('empty_recovery_existing_process_not_adopted')
    for port in (PORTS['postgres'], PORTS['redis']):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))


def empty_database_probe_script() -> str:
    """生成只查系统目录的有界预检；三个本机连接，逐SELECT先EXPLAIN，不读业务表。"""
    return r'''
import json, os, psycopg2

def check(condition, code):
    if not condition:
        raise RuntimeError(code)

queries = 0
def rows(cursor, query):
    global queries
    cursor.execute('EXPLAIN ' + query)
    cursor.fetchall()
    cursor.execute(query)
    queries += 2
    return cursor.fetchall()

def connect(database):
    return psycopg2.connect(host='127.0.0.1', port=25432, user='agent_admin',
        password=os.environ['AIC_PG_ADMIN'], dbname=database, connect_timeout=3,
        options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000')

with connect('postgres') as connection:
    with connection.cursor() as cursor:
        # inet显式转text会附带/32；host仅提取地址，仍严格比较固定loopback。
        identity = rows(cursor, "SELECT current_setting('data_directory'), current_setting('port'), current_user, current_database(), host(inet_server_addr()), current_setting('server_version_num')::int")
        check(identity == [(os.environ['AIC_EXPECTED_PGDATA'], '25432', 'agent_admin', 'postgres', '127.0.0.1', identity[0][5])]
            and 160000 <= identity[0][5] < 170000, 'empty_recovery_server_identity_changed')
        databases = rows(cursor, "SELECT datname, pg_get_userbyid(datdba), datistemplate FROM pg_database ORDER BY datname")
        check(databases == [('dify', 'dify_app', False), ('dify_plugin', 'dify_app', False),
            ('postgres', 'agent_admin', False), ('template0', 'agent_admin', True), ('template1', 'agent_admin', True)],
            'empty_recovery_databases_or_owners_changed')
        roles = rows(cursor, "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname")
        check(len(roles) == 2 and roles[0][0:3] == ('agent_admin', True, True)
            and roles[1] == ('dify_app', True, False, False, False, False, False), 'empty_recovery_roles_changed')

for database in ('dify', 'dify_plugin'):
    with connect(database) as connection:
        with connection.cursor() as cursor:
            schemas = rows(cursor, "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' ORDER BY nspname")
            check(schemas == [('public', 'pg_database_owner')], 'empty_recovery_schema_owner_changed')
            relations = rows(cursor, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'")
            functions = rows(cursor, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'")
            extensions = rows(cursor, "SELECT extname FROM pg_extension ORDER BY extname")
            check(relations == [(0,)] and functions == [(0,)] and extensions == [('plpgsql',)], 'empty_recovery_business_schema_not_empty')
print(json.dumps({'verified': True, 'connections': 3, 'metadata_statements': queries,
    'databases': ['dify', 'dify_plugin'], 'empty_business_schemas': True}))
'''


def verify_empty_databases(data: dict, secret: dict, launch: str) -> dict:
    """只在精确所属的PG/Redis均就绪后启动元数据子进程，禁止回落任何已有连接配置。"""
    current = load()
    for role in ('postgres', 'redis'):
        info = current['processes'][role]
        candidate = owned_process(info, current)
        if info['launch_id'] != launch or not candidate or candidate.uids().real != account('agentpg' if role == 'postgres' else 'agentcache').pw_uid:
            raise RuntimeError('empty_recovery_dependency_identity_changed')
    try:
        result = subprocess.run([str(ROOT / 'venvs/api/bin/python'), '-c', empty_database_probe_script()],
            check=True, timeout=25, capture_output=True, env={**safe_environment(data),
                'AIC_PG_ADMIN': secret['pg_admin'], 'AIC_EXPECTED_PGDATA': str(ROOT / 'data/postgres')})
    except subprocess.CalledProcessError as error:
        # 仅投影本脚本固定的拒绝码，不把驱动错误、连接字符串或原始stderr送到终端。
        diagnostic = (error.stderr or b'').decode('utf-8', errors='replace')
        for suffix in ('server_identity_changed', 'databases_or_owners_changed', 'roles_changed',
                'schema_owner_changed', 'business_schema_not_empty'):
            code = 'empty_recovery_' + suffix
            if code in diagnostic:
                raise RuntimeError(code) from error
        raise
    evidence = json.loads(result.stdout)
    if evidence != {'verified': True, 'connections': 3, 'metadata_statements': 22,
            'databases': ['dify', 'dify_plugin'], 'empty_business_schemas': True}:
        raise RuntimeError('empty_recovery_preflight_receipt_invalid')
    return evidence


def recover_empty_migration(expected_runtime_id: str, log_sha256: str,
        reviewed_failed_launch: str | None = None) -> dict:
    """人工审阅后的单次恢复：复用本轮空库，只执行原迁移，不初始化或重建任何对象。"""
    check_root()
    data = load()
    if data['boot_id'] != boot_identity():
        raise RuntimeError('wsl_boot_changed_requires_reconcile')
    content = missing_redis_failure(data, expected_runtime_id, log_sha256, reviewed_failed_launch)
    validate_empty_cluster_files(data)
    launch = uuid4().hex
    archive = inside(ROOT / 'logs' / ('init-data-before-recovery-' + launch + '.log'))
    with archive.open('xb') as handle:
        handle.write(content)
    archive.chmod(0o600)
    previous = data.get('empty_migration_recovery')
    update(lambda d: d.update(empty_migration_recovery={'status': 'attempted', 'phase': 'dependencies', 'at': timestamp(),
        'runtime_id': expected_runtime_id, 'failure_log_sha256': log_sha256,
        'failure_log_archive': archive.name, 'launch_id': launch, 'history': [previous] if previous else []}))
    try:
        secret = vault()
        definitions = specs(data, secret)
        for role in ('postgres', 'redis'):
            spawn(role, definitions[role], data, launch)
            wait_listening(role)
        # 发送任何SQL前持久化尝试点；没有成功收据也不能把中断预检当作零SQL重新执行。
        update(lambda d: d['empty_migration_recovery'].update(phase='empty-preflight-attempted'))
        evidence = verify_empty_databases(data, secret, launch)
        update(lambda d: d['empty_migration_recovery'].update(preflight=evidence, phase='migration-attempted'))
        result = migrate_databases(data, secret)
        update(lambda d: d['empty_migration_recovery'].update(status='ready', finished_at=timestamp()))
        return {**result, 'recovered_empty_migration': True, 'preflight': evidence}
    except BaseException as error:
        update(lambda d: (d['empty_migration_recovery'].update(status='failed', error_type=type(error).__name__),
            d['failures'].append({'phase': 'recover-empty-migration', 'error_type': type(error).__name__,
                'launch_id': launch, 'at': timestamp()})))
        raise
    finally:
        cleanup_initialization(launch)


def save_migration_log(stdout, stderr, secret: dict, *, filename='init-data-migration.log') -> None:
    """仅保存新库迁移尾部诊断，去除实际凭据，日志只供本机root维护。"""
    parts = [
        value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
        for value in (stdout, stderr)
    ]
    text = '\n'.join(parts)
    for value in secret.values():
        if isinstance(value, str) and value:
            text = text.replace(value, '[REDACTED]')
    path = inside(ROOT / 'logs' / filename)
    path.write_text(text[-256 * 1024:], encoding='utf-8')
    path.chmod(0o600)


def doctor() -> dict:
    check_root()
    missing = dependency_checks()
    if not Path('/sys/fs/cgroup/cgroup.controllers').is_file(): missing.append('cgroup_v2_process_ownership')
    if not (ROOT / 'runtime.json').exists():
        return {'prepared': False, 'missing': missing, 'landlock_abi': landlock_abi(),
            'databases_initialized': False, 'plugin_database_initialized': False,
            'ready_to_prepare': not missing, 'ready_to_start': False,
            'full_native_verified': False}
    data = load()
    if data['boot_id'] != boot_identity(): missing.append('wsl_boot_changed_requires_reconcile')
    return {'runtime_id': data['id'], 'missing': missing, 'landlock_abi': landlock_abi(),
        'databases_initialized': data['databases_initialized'],
        'plugin_database_initialized': data.get('plugin_database_initialized', False), 'full_native_verified': False,
        'ready_to_start': not missing and data['databases_initialized'] and data.get('plugin_database_initialized', False)}


def start() -> dict:
    check_root()
    checked = doctor()
    if not checked['ready_to_start']:
        raise RuntimeError('native_dependencies_or_database_not_ready')
    data = load()
    import wsl_cgroups
    for name, info in data['processes'].items():
        if wsl_cgroups.live_members(info['cgroup']) or owned_process(info, data):
            raise RuntimeError('existing_process_not_adopted')
    # 所有服务均停止后重写；旧boot/NAT/keeper的工具例外自动回到原拒绝规则。
    write_configs(data)
    import wsl_network
    wsl_network.create(ROOT, data['id'])
    secret = vault()
    launch = uuid4().hex
    update(lambda d: d['launches'].append({'id': launch, 'status': 'attempted', 'at': timestamp()}))
    started = []
    try:
        definitions = specs(data, secret)
        for name in ORDER:
            spawn(name, definitions[name], data, launch)
            started.append(name)
            wait_listening(name, seconds=180 if name in {'api', 'agent', 'web', 'code'} else 45)
        # worker/beat没有TCP入口；最终仍逐角色复核存活和降权结果，不能把包装器存活算服务就绪。
        final = load()
        for name in ORDER:
            info = final['processes'][name]
            candidate = owned_process(info, final)
            if not candidate or candidate.uids().real != info['service_uid']:
                raise RuntimeError('native_service_identity_not_ready:' + name)
        update(lambda d: d['launches'][-1].update(status='listening', finished_at=timestamp()))
        return status()
    except BaseException as error:
        cleanup = []
        for name in reversed(started):
            try: cleanup.append(stop_role(name, expected_launch=launch))
            except Exception as failed: cleanup.append({'role': name, 'verified': False, 'error_type': type(failed).__name__})
        update(lambda d: d['failures'].append({'phase': 'start', 'launch_id': launch,
            'error_type': type(error).__name__, 'cleanup': cleanup, 'at': timestamp()}))
        raise


def stop() -> dict:
    check_root()
    results = []
    for name in reversed(ORDER):
        try: results.append(stop_role(name))
        except Exception as error: results.append({'role': name, 'verified': False, 'stopped': False, 'error_type': type(error).__name__})
    return {'stopped': all(item.get('stopped') and item.get('verified') for item in results), 'results': results,
        'data_preserved': True, 'namespace_preserved': True}


def status() -> dict:
    check_root()
    data = load()
    if data['boot_id'] != boot_identity():
        return {'runtime_id': data['id'], 'distro': DISTRO, 'root': str(ROOT),
            'requires_boot_reconciliation': True, 'full_native_verified': False, 'process_state': 'previous_boot',
            'databases_initialized': data['databases_initialized'],
            'plugin_database_initialized': data.get('plugin_database_initialized', False), 'ports': PORTS}
    processes = []
    import wsl_cgroups
    for role, info in data['processes'].items():
        process = owned_process(info, data)
        members = wsl_cgroups.live_members(info['cgroup'])
        processes.append({'role': role, 'pid': info['pid'], 'owned': process is not None,
            'running': bool(members), 'owned_tree_pids': members, 'launch_id': info['launch_id']})
    return {'runtime_id': data['id'], 'distro': DISTRO, 'root': str(ROOT), 'source_version': '1.17.1',
        'ports': PORTS, 'processes': processes, 'databases_initialized': data['databases_initialized'],
        'plugin_database_initialized': data.get('plugin_database_initialized', False),
        'public_url': f"http://127.0.0.1:{PORTS['web']}", 'full_native_verified': False}


def reconcile_boot() -> dict:
    """显式确认发行版重启后归属暂存已消失；保留数据、密钥及旧进程/网络历史。"""
    check_root()
    data = load()
    current = boot_identity()
    if data['boot_id'] == current:
        return {'already_current_boot': True, 'services_started': False}
    import wsl_cgroups
    parent, _ = wsl_cgroups.names(data['id'], '0' * 32, 'runtime')
    if parent.exists():
        raise RuntimeError('old_cgroup_still_exists_boot_reconcile_refused')
    for info in data['processes'].values():
        try:
            candidate = psutil.Process(info['pid'])
            if abs(candidate.create_time() - info['created_at']) < 0.01:
                raise RuntimeError('old_process_still_exists_boot_reconcile_refused')
        except psutil.NoSuchProcess:
            pass
    network = ROOT / 'network.json'
    history = {'previous_boot': data['boot_id'], 'reconciled_at': timestamp(), 'processes': data['processes']}
    if network.exists():
        record = json.loads(network.read_text())
        if record.get('runtime_id') != data['id']:
            raise RuntimeError('network_identity_changed_boot_reconcile_refused')
        for plan in record.get('plans', []):
            if Path('/run/netns', plan['name']).exists() or Path('/sys/class/net', plan['host_device']).exists():
                raise RuntimeError('old_network_still_exists_boot_reconcile_refused')
        archive = ROOT / ('network-history-' + uuid4().hex + '.json')
        network.replace(archive)
        history['network_archive'] = archive.name
    def change(value):
        value['boot_history'].append(history)
        value.update(boot_id=current, processes={}, cgroup_root_inode=None)
    update(change)
    return {'boot_reconciled': True, 'services_started': False, 'data_preserved': True, 'secrets_preserved': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='专用WSL无Docker的Dify生命周期')
    parser.add_argument('action', choices=['prepare', 'init-data', 'init-plugin-data', 'recover-empty-migration', 'doctor', 'start', 'stop', 'status', 'reconcile-boot', 'configure-tool-bridge'])
    parser.add_argument('--expected-runtime-id')
    parser.add_argument('--failure-log-sha256')
    parser.add_argument('--reviewed-failed-launch')
    parser.add_argument('--bridge-config', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'recover-empty-migration':
        if not args.expected_runtime_id or not args.failure_log_sha256:
            parser.error('空库恢复必须指定已审阅的runtime与原始脱敏失败日志SHA256')
    elif args.expected_runtime_id or args.failure_log_sha256 or args.reviewed_failed_launch:
        parser.error('恢复身份参数只能用于recover-empty-migration')
    if args.action == 'configure-tool-bridge':
        if not args.bridge_config:
            parser.error('工具桥配置必须指定本次Windows桥已登记的快照')
    elif args.bridge_config:
        parser.error('--bridge-config只能用于configure-tool-bridge')
    functions = {'prepare': prepare, 'init-data': init_data, 'init-plugin-data': init_plugin_data,
        'doctor': doctor, 'start': start, 'stop': stop,
        'status': status, 'reconcile-boot': reconcile_boot,
        'configure-tool-bridge': lambda: configure_tool_bridge(args.bridge_config),
        'recover-empty-migration': lambda: recover_empty_migration(args.expected_runtime_id, args.failure_log_sha256,
            args.reviewed_failed_launch)}
    try:
        def interrupted(signum, frame):
            raise InterruptedError('runtime_termination_requested')
        signal.signal(signal.SIGTERM, interrupted)
        from wsl_setup import operation_lock
        check_root()
        # 与安装共用操作锁；runtime.json的小事务锁仍独立，避免嵌套死锁。
        with operation_lock():
            result = functions[args.action]()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('stopped', True) else 1
    except Exception as error:
        import re
        result = {'status': 'failed', 'action': args.action, 'error_type': type(error).__name__}
        if isinstance(error, RuntimeError) and re.fullmatch('[a-z0-9_:./-]{1,200}', str(error)):
            result['error_code'] = str(error)
        print(json.dumps(result))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
