"""Dify依赖及原生入口：只操作已标记的新进程、新目录和回环端口。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import json
from pathlib import Path
import time

import psutil

API_START_SECONDS = 45
API_RSS_LIMIT = 1024 ** 3
API_MIN_PHYSICAL = 2 * 1024 ** 3
API_MIN_COMMIT = 3 * 1024 ** 3


@contextmanager
def exclusive_operation(root: Path, operation: str):
    """跨进程非阻塞排他；整个安装/构建期间持有，进程退出后由系统释放。"""
    import msvcrt
    if operation not in {'install', 'openwebui-start'}:
        raise ValueError('未知隔离操作锁')
    with (root / (operation + '.lock')).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('已有本项目安装/构建或同组件启动正在执行，禁止并行和自动等待重试') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def memory_snapshot() -> dict[str, int]:
    """读取Windows物理内存与系统提交量，不查询数据库或进程环境。"""
    class PerformanceInformation(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in [
            'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal', 'PhysicalAvailable',
            'SystemCache', 'KernelTotal', 'KernelPaged', 'KernelNonpaged', 'PageSize']] + [
            ('HandleCount', ctypes.c_ulong), ('ProcessCount', ctypes.c_ulong), ('ThreadCount', ctypes.c_ulong)]
    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        raise RuntimeError('无法核实Windows提交量，拒绝启动')
    return {'available_bytes': psutil.virtual_memory().available,
        'commit_free_bytes': (info.CommitLimit - info.CommitTotal) * info.PageSize,
        'commit_total_bytes': info.CommitTotal * info.PageSize,
        'commit_limit_bytes': info.CommitLimit * info.PageSize}


def require_api_resources() -> dict[str, int]:
    snapshot = memory_snapshot()
    if snapshot['available_bytes'] < API_MIN_PHYSICAL or snapshot['commit_free_bytes'] < API_MIN_COMMIT:
        raise RuntimeError('API启动要求可用物理内存至少2GiB且提交余量至少3GiB；未启动，不重试或加预算')
    return snapshot


def require_local_drive(path: Path) -> None:
    """排除UNC和映射网络盘；路径须已resolve以识别目录重定向。"""
    if not path.is_absolute() or str(path).startswith('\\\\'):
        raise ValueError('原生API只允许本机绝对路径，禁止网络目录')
    if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) not in {2, 3, 6}:
        raise ValueError('无法确认目标为本机磁盘，禁止使用')


def openwebui_paths(runtime, root: Path, data: dict, *, api_only: bool) -> dict[str, Path]:
    """逐项重新核验隔离根、源码副本、venv、静态资产和SQLite目标，禁止旧目录回退。"""
    root = root.resolve()
    runtime.ensure_inside(runtime.REPO / '.local', root)
    require_local_drive(root)
    source = runtime.ensure_inside(root, Path(data['sources']['openwebui']))
    if source != (root / 'sources/openwebui').resolve():
        raise ValueError('Open WebUI必须使用本项目固定的源码副本路径')
    paths = {'source': source, 'backend': source / 'backend',
        'python': root / 'venvs/openwebui/Scripts/python.exe', 'data': root / 'openwebui/data',
        'sqlite': root / 'openwebui/data/webui.db', 'static': root / 'openwebui/static',
        'model_cache': root / 'openwebui/model-cache',
        'frontend': root / 'openwebui/api-only-no-frontend' if api_only else source / 'build'}
    paths = {key: runtime.ensure_inside(root, value) for key, value in paths.items()}
    if not (paths['backend'] / 'open_webui/main.py').is_file() or not paths['python'].is_file():
        raise ValueError('隔离源码或独立Python环境不完整')
    if json.loads((source / 'package.json').read_text('utf-8')).get('version') != '0.12.3':
        raise ValueError('Open WebUI源码版本与本轮锁定版本不一致')
    venv_cfg = root / 'venvs/openwebui/pyvenv.cfg'
    config = dict(line.split('=', 1) for line in venv_cfg.read_text('utf-8').splitlines() if '=' in line)
    config = {key.strip(): value.strip() for key, value in config.items()}
    version = config.get('version_info', '')
    # uv可能只写major.minor；接受3.12或3.12.x，不能把3.120误认为3.12。
    if config.get('include-system-site-packages', '').lower() != 'false' or not (version == '3.12' or version.startswith('3.12.')):
        raise ValueError('API要求独立Python3.12环境，不能继承系统site-packages')
    if api_only:
        if paths['frontend'].exists():
            raise ValueError('API-only专用前端路径必须不存在，不能混入半成品静态页面')
        if not (source / 'static/static').is_dir():
            raise ValueError('API所需原生基础静态资产缺失')
    elif not (paths['frontend'] / 'index.html').is_file():
        raise RuntimeError('原生前端尚未构建，完整界面模式不可启动')
    if data['ports'].get('openwebui') != 13000:
        raise ValueError('本轮Open WebUI只允许127.0.0.1:13000')
    binding = data.get('databases', {}).get('openwebui')
    expected = {'runtime_id': data['id'], 'sqlite_file': str(paths['sqlite']), 'data_directory': str(paths['data'])}
    if binding and binding != expected:
        raise ValueError('SQLite归属标记与本项目目标不一致')
    if not binding and (paths['sqlite'].exists() or (paths['data'].exists() and any(paths['data'].iterdir()))):
        raise ValueError('发现未登记的既有OWUI数据，禁止自动接管或回退')
    return paths


def openwebui_environment(runtime, paths: dict[str, Path], secret: dict) -> dict[str, str]:
    """只启用指定外部模型API；禁止本地权重自动下载、旧dotenv和旧服务继承。"""
    env = runtime.base_environment({**runtime.openwebui_provider_environment(),
        'DATA_DIR': str(paths['data']), 'DATABASE_URL': 'sqlite:///' + paths['sqlite'].as_posix(),
        'STATIC_DIR': str(paths['static']), 'FRONTEND_BUILD_DIR': str(paths['frontend']),
        'WEBUI_SECRET_KEY': secret['webui_secret'], 'WEBUI_AUTH': 'True', 'ENABLE_SIGNUP': 'True',
        'HOST': '127.0.0.1', 'PORT': '13000', 'PYTHONPATH': str(paths['backend']),
        'PYTHON_DOTENV_DISABLED': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OFFLINE_MODE': 'True',
        'HF_HOME': str(paths['model_cache']), 'HUGGINGFACE_HUB_CACHE': str(paths['model_cache'] / 'hub'),
        'RAG_EMBEDDING_MODEL_AUTO_UPDATE': 'False', 'RAG_RERANKING_MODEL_AUTO_UPDATE': 'False',
        'WHISPER_MODEL_AUTO_UPDATE': 'False', 'ENABLE_OLLAMA_API': 'False',
        'ENABLE_VERSION_UPDATE_CHECK': 'False', 'ENABLE_BASE_MODELS_CACHE': 'False',
        'THREAD_POOL_SIZE': '2', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
        'OPENBLAS_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false'})
    if paths['frontend'] == paths['data'].parent / 'api-only-no-frontend':
        # 仅验收API模式降低原生后台轮询；字段来自上游config/automations，不删除功能或改变完整模式。
        env.update({'TIMER_POLL_INTERVAL': '60', 'SCHEDULER_POLL_INTERVAL': '60',
            'ENABLE_AUTOMATIONS': 'False', 'ENABLE_CALENDAR': 'False'})
    return env


def process_tree_rss(process) -> int:
    total = 0
    for child in [process, *process.children(recursive=True)]:
        try:
            total += child.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total


def record_api_failure(runtime, root: Path, reason: str, details: dict) -> None:
    def change(state):
        incident = state.setdefault('resource_incident', {})
        incident['active'] = True
        incident.setdefault('events', []).append({'component': 'openwebui', 'reason': reason,
            'details': details, 'recorded_at': datetime.now(timezone.utc).isoformat()})
        state.setdefault('installation_failures', {})['openwebui-api'] = {'status': 'startup_failed', 'reason': reason, **details}
    runtime.update_runtime(root, change)


def wait_for_api(runtime, root: Path, *, seconds=API_START_SECONDS, rss_limit=API_RSS_LIMIT, deadline=None) -> dict:
    """在本次明确预算内核进程树RSS与回环监听，不发HTTP/SQL；超限立刻失败。"""
    deadline = time.monotonic() + seconds if deadline is None else deadline
    maximum_rss = 0
    while time.monotonic() < deadline:
        data = runtime.load(root)
        process = runtime.owned_process(root, 'openwebui', data)
        if not process:
            raise RuntimeError('API启动进程已退出，未进行HTTP或数据库验收')
        rss = process_tree_rss(process)
        maximum_rss = max(maximum_rss, rss)
        if rss > rss_limit:
            record_api_failure(runtime, root, 'rss_budget_exceeded', {'rss_bytes': rss, 'limit_bytes': rss_limit})
            raise RuntimeError('本次API进程树RSS超过1GiB，必须停止；不重试或提高预算')
        if 13000 in listening_processes(process):
            return {'listening_verified': True, 'startup_max_rss_bytes': maximum_rss,
                'startup_budget_seconds': seconds, 'rss_limit_bytes': rss_limit}
        time.sleep(0.25)
    record_api_failure(runtime, root, 'startup_timeout', {'budget_seconds': seconds, 'maximum_rss_bytes': maximum_rss})
    raise RuntimeError(f'API未在{seconds}秒预算内获得本进程回环监听，必须停止；不重试或扩大超时')


def start_openwebui(runtime, root: Path, data: dict, secret: dict, *, api_only: bool, acknowledged_incident: bool,
        startup_seconds: int = API_START_SECONDS) -> dict:
    """按显式单次预算启动；180秒须同时确认API模式与资源故障，不改变资源门限。"""
    if type(startup_seconds) is not int or startup_seconds not in (45, 180):
        raise ValueError('启动预算只允许45或180秒')
    if startup_seconds == 180 and not (api_only and acknowledged_incident):
        raise ValueError('180秒预算仅适用于已明确确认的Open WebUI API-only启动')
    paths = openwebui_paths(runtime, root, data, api_only=api_only)
    snapshot = require_api_resources()
    if runtime.owned_process(root, 'openwebui', data):
        raise RuntimeError('已有本项目OWUI进程，请先核对或明确停止；本次不会接管或终止它')
    if not runtime.available_port(13000):
        raise RuntimeError('本机13000端口被其他进程占用，未连接已有服务')
    paths['data'].mkdir(parents=True, exist_ok=True)
    binding = {'runtime_id': data['id'], 'sqlite_file': str(paths['sqlite']), 'data_directory': str(paths['data'])}
    runtime.update_runtime(root, lambda state: state.setdefault('databases', {}).update(openwebui=binding))
    metadata = {'mode': 'api_only' if api_only else 'full', 'frontend_available': not api_only,
        'sqlite_file': str(paths['sqlite']), 'data_directory': str(paths['data']),
        'acknowledged_resource_incident': acknowledged_incident, 'ready': False,
        'preflight_resources': snapshot, 'startup_budget_seconds': startup_seconds, 'rss_limit_bytes': API_RSS_LIMIT}
    deadline = time.monotonic() + startup_seconds
    started, phase = None, 'prepare_environment'
    try:
        env = openwebui_environment(runtime, paths, secret)
        phase = 'spawn'
        started = runtime.spawn(root, 'openwebui', [str(paths['python']), '-m', 'uvicorn', 'open_webui.main:app',
            '--host', '127.0.0.1', '--port', '13000', '--workers', '1'], cwd=paths['backend'], env=env, metadata=metadata)
        if not started.get('started') or not started.get('launch_id'):
            raise RuntimeError('本次未创建并登记独立API进程，禁止接管既有服务')
        phase = 'readiness'
        ready = wait_for_api(runtime, root, seconds=startup_seconds, deadline=deadline)
        runtime.update_runtime(root, lambda state: state['processes']['openwebui'].update(ready=True, **ready))
    except Exception as error:
        # spawn失败时started仍为None，不能按组件名称猜测进程；spawn自身核对其Popen句柄回收结果。
        details = {'phase': phase, 'error_type': type(error).__name__, 'startup_budget_seconds': startup_seconds}
        if isinstance(error, runtime.NativeSpawnError):
            details.update(spawn_phase=error.phase, cleanup=error.cleanup,
                registered_process=error.registered_process)
        cleanup_error = None
        if started is not None:
            current = runtime.load(root).get('processes', {}).get('openwebui')
            if runtime.same_launch(current, started):
                try:
                    runtime.stop(root, 'openwebui')
                    details['registered_process_stopped'] = True
                except Exception as stop_error:
                    cleanup_error = type(stop_error).__name__
                    details.update(registered_process_stopped=False, cleanup_error_type=cleanup_error)
            else:
                details['registered_process_stopped'] = False
                details['cleanup_skipped'] = '当前登记不属于本次launch，未操作其他进程'
        # 不把异常文本、命令、env或模型密钥写入故障事件。
        try:
            record_api_failure(runtime, root, 'api_start_failed', details)
        except Exception as record_error:
            raise RuntimeError('API启动失败且故障记录未能写入；需人工核对本次PID，禁止重试（' + type(record_error).__name__ + '）') from None
        unverified_spawn = isinstance(error, runtime.NativeSpawnError) and not error.cleanup.get('verified')
        if cleanup_error or unverified_spawn:
            raise RuntimeError('API启动失败，本次进程回收未能全部核实；已保留故障与已知PID，禁止重试') from None
        raise RuntimeError('API启动失败，阶段' + phase + f'，启动预算{startup_seconds}秒；已记录非敏感故障，未清除全局暂停') from None
    return {**started, **ready, 'ready': True, 'http_health_tested': False,
        'functional_acceptance': False, 'resource_incident_remains_active': bool(runtime.load(root).get('resource_incident', {}).get('active'))}


def listening_processes(process):
    """核对监听套接字的PID归属；无法核实就拒绝后续连接。"""
    return {connection.laddr.port for child in [process, *process.children(recursive=True)]
        for connection in child.net_connections(kind='inet')
        if connection.status == 'LISTEN' and connection.laddr.ip in {'127.0.0.1', '::1'}}


def require_service(runtime, root: Path, component: str):
    data = runtime.load(root)
    process = runtime.owned_process(root, component, data)
    if not process or data['ports'][component] not in listening_processes(process):
        raise RuntimeError(f'{component}不是本项目已验证监听的本机进程；禁止回退旧服务')
    return process


def wait_owned_listener(runtime, root: Path, component: str, seconds=15):
    """只等本项目新进程，未建立数据库连接，预算到期即停止等待。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data = runtime.load(root)
        process = runtime.owned_process(root, component, data)
        if not process:
            raise RuntimeError(f'{component}已退出；请核对本机日志')
        if data['ports'][component] in listening_processes(process):
            return process
        time.sleep(0.25)
    raise RuntimeError(f'{component}未在{seconds}秒预算内就绪，未连接数据库且不会重试')


def dify_environment(runtime, root: Path, data: dict, secret: dict) -> dict[str, str]:
    """所有存储端点显式指向新隔离实例；不读取原项目.env。"""
    return runtime.base_environment({
        'DB_TYPE': 'postgresql', 'DB_HOST': '127.0.0.1', 'DB_PORT': str(data['ports']['postgres']),
        'DB_USERNAME': 'agent_native', 'DB_PASSWORD': secret['postgres_password'], 'DB_DATABASE': 'dify',
        'SQLALCHEMY_POOL_SIZE': '5', 'SQLALCHEMY_MAX_OVERFLOW': '2',
        'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(data['ports']['redis']),
        'REDIS_PASSWORD': secret['redis_password'], 'REDIS_DB': '0', 'REDIS_RETRY_RETRIES': '0',
        'CELERY_BROKER_URL': f'redis://:{secret["redis_password"]}@127.0.0.1:{data["ports"]["redis"]}/1',
        'CELERY_BACKEND': 'redis', 'VECTOR_STORE': 'qdrant',
        'QDRANT_URL': f'http://127.0.0.1:{data["ports"]["qdrant"]}', 'QDRANT_API_KEY': secret['qdrant_key'],
        'QDRANT_GRPC_ENABLED': 'false', 'QDRANT_CLIENT_TIMEOUT': '5',
        'SECRET_KEY': secret['dify_secret'], 'STORAGE_TYPE': 'opendal',
        'OPENDAL_SCHEME': 'fs', 'OPENDAL_FS_ROOT': str(root / 'dify/storage'),
        'PLUGIN_DAEMON_URL': f'http://127.0.0.1:{data["ports"]["plugin_daemon"]}',
        'PLUGIN_DAEMON_KEY': secret['plugin_key'], 'INNER_API_KEY_FOR_PLUGIN': secret['plugin_inner_key'],
        'CONSOLE_WEB_URL': f'http://127.0.0.1:{data["ports"]["dify_web"]}',
        'CONSOLE_API_URL': f'http://127.0.0.1:{data["ports"]["dify_api"]}',
        'APP_WEB_URL': f'http://127.0.0.1:{data["ports"]["dify_web"]}',
        'SERVICE_API_URL': f'http://127.0.0.1:{data["ports"]["dify_api"]}',
        'FILES_URL': f'http://127.0.0.1:{data["ports"]["dify_api"]}',
        'CONSOLE_CORS_ALLOW_ORIGINS': f'http://127.0.0.1:{data["ports"]["dify_web"]}',
        'WEB_API_CORS_ALLOW_ORIGINS': f'http://127.0.0.1:{data["ports"]["dify_web"]}',
        # 沙箱尚无Windows等价运行时，明确指向本项目未启用端口，不能回退旧沙箱。
        'CODE_EXECUTION_ENDPOINT': 'http://127.0.0.1:18194', 'CODE_EXECUTION_API_KEY': secret['sandbox_key'],
        'LOG_LEVEL': 'WARNING', 'DEBUG': 'false', 'FLASK_DEBUG': '0', 'DEPLOY_ENV': 'PRODUCTION',
        'ENABLE_OTEL': 'false', 'PYTHONPATH': str(Path(data['sources']['dify']) / 'api')})


def start_postgres(runtime, root: Path, data: dict, secret: dict):
    existing = runtime.owned_process(root, 'postgres', data)
    if existing:
        require_service(runtime, root, 'postgres')
        return {'component': 'postgres', 'already_running': True, 'port': data['ports']['postgres']}
    if not runtime.available_port(data['ports']['postgres']):
        raise RuntimeError('隔离PostgreSQL端口被占用；未连接已有服务')
    datadir = runtime.ensure_inside(root, root / 'postgres/data')
    binary_dir = Path(data['binaries']['postgres']).parent
    if not (datadir / 'PG_VERSION').is_file():
        if datadir.exists() and any(datadir.iterdir()):
            raise RuntimeError('隔离PG目录有未知内容；不初始化、不覆盖')
        password_file = root / 'postgres/init-password.tmp'
        with password_file.open('x', encoding='utf-8') as output:
            output.write(secret['postgres_password'] + '\n')
        try:
            runtime.run_step(root, 'postgres-init', [str(binary_dir / 'initdb.exe'), '-D', str(datadir),
                '-U', 'agent_native', '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256',
                '--pwfile=' + str(password_file)], timeout=60)
        finally:
            password_file.unlink(missing_ok=True)
    result = runtime.spawn(root, 'postgres', [str(binary_dir / 'postgres.exe'), '-D', str(datadir),
        '-h', '127.0.0.1', '-p', str(data['ports']['postgres'])], cwd=root / 'postgres', env=runtime.base_environment())
    wait_owned_listener(runtime, root, 'postgres')
    # 已验证PID、可执行文件、全新datadir与监听归属后，才允许在该新集群建业务库。
    for name in ['dify', 'dify_plugin']:
        if name in runtime.load(root).get('databases_created', []):
            continue
        runtime.run_step(root, 'postgres-createdb-' + name, [str(binary_dir / 'createdb.exe'),
            '-h', '127.0.0.1', '-p', str(data['ports']['postgres']), '-U', 'agent_native', '--no-password', name],
            env=runtime.base_environment({'PGPASSWORD': secret['postgres_password'], 'PGCONNECT_TIMEOUT': '5'}), timeout=15)
        runtime.update_runtime(root, lambda state: state.setdefault('databases_created', []).append(name))
    result['databases_created'] = runtime.load(root)['databases_created']
    return result


def start_service(runtime, root: Path, component: str, data: dict, secret: dict):
    if component == 'postgres':
        return start_postgres(runtime, root, data, secret)
    if component in {'plugin_daemon', 'dify_api', 'dify_worker'}:
        for dependency in ['postgres', 'redis']:
            require_service(runtime, root, dependency)
    if component == 'plugin_daemon':
        executable = data['binaries'].get(component)
        if not executable:
            raise RuntimeError('先安装经SHA256核验的官方插件运行器')
        env = runtime.base_environment({'SERVER_HOST': '127.0.0.1', 'SERVER_PORT': str(data['ports'][component]),
            'SERVER_KEY': secret['plugin_key'], 'PLATFORM': 'local', 'GIN_MODE': 'release',
            'DB_TYPE': 'postgresql', 'DB_HOST': '127.0.0.1', 'DB_PORT': str(data['ports']['postgres']),
            'DB_USERNAME': 'agent_native', 'DB_PASSWORD': secret['postgres_password'], 'DB_DATABASE': 'dify_plugin',
            'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(data['ports']['redis']), 'REDIS_PASSWORD': secret['redis_password'],
            'DIFY_INNER_API_KEY': secret['plugin_inner_key'], 'DIFY_INNER_API_URL': f'http://127.0.0.1:{data["ports"]["dify_api"]}',
            'PLUGIN_REMOTE_INSTALLING_ENABLED': 'false', 'PLUGIN_STORAGE_TYPE': 'local',
            'PLUGIN_STORAGE_LOCAL_ROOT': str(root / 'plugin/storage'), 'PLUGIN_WORKING_PATH': str(root / 'plugin/cwd'),
            'PLUGIN_INSTALLED_PATH': str(root / 'plugin/installed'), 'PERSISTENCE_STORAGE_PATH': str(root / 'plugin/persistence'),
            'PYTHON_INTERPRETER_PATH': data['binaries']['python312'], 'UV_PATH': data['binaries']['uv'],
            'FORCE_VERIFYING_SIGNATURE': 'true', 'ENFORCE_LANGGENIUS_PLUGIN_SIGNATURES': 'true',
            'PIP_MIRROR_AUTO_DETECT': 'false', 'PIP_MIRROR_URL': 'https://pypi.org/simple', 'ENABLE_OTEL': 'false'})
        return runtime.spawn(root, component, [executable], cwd=root / 'plugin', env=env)
    if component in {'dify_api', 'dify_worker'}:
        require_service(runtime, root, 'qdrant')
        source = Path(data['sources']['dify']) / 'api'
        python = root / 'venvs/dify/Scripts/python.exe'
        if not python.is_file() or 'dify' not in data['installations']:
            raise RuntimeError('Dify锁定Python依赖尚未安装完成')
        env = dify_environment(runtime, root, data, secret)
        if component == 'dify_api':
            if not data.get('dify_migrated'):
                runtime.run_step(root, 'dify-migrations', [str(python), '-m', 'flask', '--app', 'app', 'db', 'upgrade'], cwd=source, env=env, timeout=120)
                runtime.update_runtime(root, lambda state: state.update(dify_migrated=True))
            # 独立引导只改变监听方式；保留上游socketio/gevent应用与节点实现。
            return runtime.spawn(root, component, [str(python), str(Path(__file__).with_name('dify_server.py')),
                str(data['ports']['dify_api'])], cwd=source, env=env)
        return runtime.spawn(root, component, [str(python), '-m', 'celery', '-A', 'app.celery', 'worker',
            '--pool=solo', '--concurrency=1', '--loglevel=WARNING', '-Q', 'dataset,workflow,priority_workflow,mail,ops_trace,app_deletion,plugin,conversation'], cwd=source, env=env)
    if component == 'dify_web':
        source = Path(data['sources']['dify']) / 'web'
        if not (source / '.next/BUILD_ID').is_file():
            raise RuntimeError('Dify原生前端尚未构建')
        node = Path(data['binaries']['node24'])
        env = runtime.base_environment(runtime.dify_frontend_environment(data))
        return runtime.spawn(root, component, [str(node), str(source / 'node_modules/next/dist/bin/next'), 'start',
            '--hostname', '127.0.0.1', '--port', str(data['ports'][component])], cwd=source, env=env)
    raise ValueError('未知原生启动组件')
