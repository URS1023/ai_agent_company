"""Windows本机原生隔离运行器；不使用Docker/WSL，不连接旧数据库或改系统服务。

所有源码副本、虚拟环境、数据、随机凭据与进程标记均位于本项目.local。
doctor只检查文件/版本/端口归属；prepare/install/start/stop各自显式执行。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

from cryptography.fernet import Fernet
import psutil

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO / '.local/native_windows'
LOCAL_BINARIES = {
    'uv': Path('C:/Users/urdcbk/.local/bin/uv.exe'),
    'python312': Path('C:/Users/urdcbk/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe'),
    'node22': Path('E:/Environment_f/nvm_f/v22.13.0/node.exe'),
    'node24': Path('E:/Environment_f/nvm_f/v24.21.0/node.exe'),
    'postgres': Path('C:/Program Files/PostgreSQL/15/bin/postgres.exe'),
    'redis': Path('E:/Environment_f/Redis-x64-5.0.14.1/redis-server.exe'),
    'git': Path('D:/App_folder/git_f/cmd/git.exe'),
}
PORTS = {'openwebui': 13000, 'postgres': 15432, 'redis': 16379, 'qdrant': 16333,
         'dify_api': 15001, 'dify_web': 13001, 'plugin_daemon': 15002}
SAFE_ENV = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA',
            'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}
ASSETS = {
    'qdrant': {'version': '1.19.2', 'url': 'https://github.com/qdrant/qdrant/releases/download/v1.19.2/qdrant-x86_64-pc-windows-msvc.zip',
        'sha256': '7d86596f16c6e85d45a50312f5e16ccb51e059b62e3d7308110cb65b4c799a4d', 'filename': 'qdrant.zip'},
    'plugin_cli': {'version': '0.6.10', 'url': 'https://github.com/langgenius/dify-plugin-daemon/releases/download/0.6.10/dify-plugin-windows-amd64.exe',
        'sha256': '99daa0fb22509636b138e0acb5e33edd9d168c21abfbc0556d3b4dd79c04d94f', 'filename': 'plugin-cli.exe'},
    'go': {'version': '1.26.10', 'url': 'https://go.dev/dl/go1.26.10.windows-amd64.zip',
        'sha256': '7ecd2d7260e82c59bcbd7f1077a07cee2dea607295b858df67195a1e4e09629b', 'filename': 'go1.26.10.zip'},
    'plugin_source': {'version': '0.6.10', 'url': 'https://codeload.github.com/langgenius/dify-plugin-daemon/zip/refs/tags/0.6.10',
        'sha256': 'a3ae9b9e85e6592bd2cdd7f49ee3c5172b7bedb94758f245b7aca6378cc4bf58', 'filename': 'plugin-daemon-0.6.10-source.zip'},
}


class NativeSpawnError(RuntimeError):
    """只携带阶段、已登记归属和回收状态，不暴露命令环境或上游凭据。"""
    def __init__(self, *, phase: str, error_type: str, cleanup: dict, registered_process: dict | None = None):
        self.phase = phase
        self.error_type = error_type
        self.cleanup = cleanup
        self.registered_process = registered_process
        state = '已核验本次已知进程退出' if cleanup.get('verified') else '本次进程回收未能全部核实，禁止重试'
        super().__init__(f'原生启动失败（阶段{phase}，类型{error_type}）；{state}')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as content:
        for chunk in iter(lambda: content.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    temporary.replace(path)


@contextmanager
def runtime_lock(root: Path):
    """短临界区保护manifest更新；下载、构建与网络等待均不持锁。"""
    import msvcrt
    with (root / 'runtime.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def update_runtime(root: Path, change) -> dict:
    with runtime_lock(root):
        data = load(root)
        change(data)
        write_json(root / 'runtime.json', data)
        return data


def base_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """只继承Windows运行必需项，避免旧系统DB/模型配置自动进入新服务。"""
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    env.update({'PATH': os.pathsep.join([str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32'),
        str(LOCAL_BINARIES['uv'].parent), str(LOCAL_BINARIES['git'].parent)]), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
        'UV_CACHE_DIR': str(DEFAULT_ROOT / 'tools/uv-cache'),
        'npm_config_cache': str(DEFAULT_ROOT / 'tools/npm-cache'),
        'UV_PYTHON_INSTALL_BIN': 'false', 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
        'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
    env.update(extra or {})
    return env


def ensure_inside(root: Path, target: Path) -> Path:
    root, target = root.resolve(), target.resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError('路径必须位于当前隔离运行目录内')
    return target


def load(root: Path) -> dict:
    root = root.resolve()
    data = json.loads((root / 'runtime.json').read_text('utf-8'))
    if data.get('kind') != 'manufacturing-native-windows' or data.get('root') != str(root):
        raise ValueError('该目录不属于本项目Windows原生环境，禁止操作')
    return data


def credentials(root: Path) -> dict:
    cipher = Fernet((root / 'runtime.key').read_bytes().strip())
    return json.loads(cipher.decrypt((root / 'credentials.enc').read_bytes()))


def copy_source(source: Path, target: Path) -> None:
    """仅复制代码副本；不复制在用env、依赖、缓存或原数据库文件。"""
    ignored = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.next', '.cache', '.pytest_cache'}
    def ignore(directory, names):
        return [name for name in names if name in ignored or name.startswith('.venv') or
                (name.startswith('.env') and not name.endswith('.example')) or name.lower().endswith(('.sqlite3', '.sqlite', '.db', '.key', '.pem', '.pfx'))]
    shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=True)


def prepare(root: Path, dify_source: Path, webui_source: Path) -> dict:
    root = root.resolve()
    if root.exists():
        existing = load(root)
        if existing.get('prepared'):
            return existing
        # 只恢复有本项目归属标记且已创建加密凭据的未完成副本。
        credentials(root)
        copy_source(dify_source.resolve(), root / 'sources/dify')
        copy_source(webui_source.resolve(), root / 'sources/openwebui')
        return update_runtime(root, lambda state: state.update(prepared=True))
    for source, expected, package in [(dify_source, '1.17.1', 'web/package.json'), (webui_source, '0.12.3', 'package.json')]:
        if json.loads((source / package).read_text('utf-8'))['version'] != expected:
            raise ValueError('原生源码版本与已核验版本不一致')
        if root == source.resolve() or source.resolve() in root.parents:
            raise ValueError('运行目录不能位于参考项目内')
    root.mkdir(parents=True)
    data = {'kind': 'manufacturing-native-windows', 'id': secrets.token_hex(12), 'root': str(root),
        'ports': PORTS, 'binaries': {k: str(v) for k, v in LOCAL_BINARIES.items()},
        'sources': {'dify': str(root / 'sources/dify'), 'openwebui': str(root / 'sources/openwebui')},
        'processes': {}, 'prepared_at': datetime.now(timezone.utc).isoformat(), 'installations': {},
        'native_acceptance': {'openwebui': False, 'dify': False, 'agent_v2': False}}
    write_json(root / 'runtime.json', data)
    key = Fernet.generate_key()
    with (root / 'runtime.key').open('xb') as output:
        output.write(key)
    (root / 'runtime.key').chmod(0o600)
    value = {key: secrets.token_urlsafe(40) for key in ['postgres_password', 'redis_password', 'qdrant_key', 'plugin_key', 'plugin_inner_key', 'webui_secret', 'dify_secret', 'sandbox_key']}
    (root / 'credentials.enc').write_bytes(Fernet(key).encrypt(json.dumps(value).encode()))
    for path in ['logs', 'tools', 'venvs', 'postgres', 'redis/data', 'qdrant/storage', 'openwebui/data', 'dify/storage', 'plugin/storage']:
        (root / path).mkdir(parents=True, exist_ok=True)
    copy_source(dify_source.resolve(), root / 'sources/dify')
    copy_source(webui_source.resolve(), root / 'sources/openwebui')
    data['prepared'] = True
    write_json(root / 'runtime.json', data)
    return data


def available_port(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def run_step(root: Path, name: str, command: list[str], *, cwd: Path | None = None, env=None, timeout=1800) -> None:
    """明确一步一次，不自动改版本/放大超时/重试；日志只含工具输出。"""
    logfile = root / 'logs' / (name + '.log')
    actual_env = env or base_environment()
    actual_env.update({'UV_CACHE_DIR': str(root / 'tools/uv-cache'), 'npm_config_cache': str(root / 'tools/npm-cache')})
    with logfile.open('ab') as log:
        process = subprocess.Popen(command, cwd=cwd, env=actual_env, stdout=log,
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # npm/构建工具会派生子进程；预算到期只收回本次调用拥有的进程树。
            parent = psutil.Process(process.pid)
            for child in reversed(parent.children(recursive=True)):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
            parent.wait(timeout=5)
            raise RuntimeError(f'{name}超过{timeout}秒预算，已停止本次进程树；不会自动重试') from None
    if code:
        raise RuntimeError(f'{name}失败（exit {code}），请核对本机日志：{logfile}')


def download_asset(root: Path, name: str) -> Path:
    asset = ASSETS[name]
    target = ensure_inside(root, root / 'tools' / asset['filename'])
    if not target.exists():
        request = urllib.request.Request(asset['url'], headers={'User-Agent': 'ManufacturingAgentPlatform/0.1 native-windows'})
        temporary = target.with_suffix(target.suffix + '.part')
        with urllib.request.urlopen(request, timeout=60) as source, temporary.open('xb') as output:
            shutil.copyfileobj(source, output)
        if sha(temporary) != asset['sha256']:
            temporary.unlink()
            raise ValueError('下载文件SHA256不匹配，已停止')
        temporary.replace(target)
    if sha(target) != asset['sha256']:
        raise ValueError('本机运行文件SHA256不匹配，禁止执行')
    if name in {'qdrant', 'go', 'plugin_source'}:
        directory = ensure_inside(root, root / 'tools' / ('qdrant' if name == 'qdrant' else 'native-build'))
        with zipfile.ZipFile(target) as archive:
            for member in archive.infolist():
                ensure_inside(directory, directory / member.filename)
            archive.extractall(directory)
        executable = directory / {'qdrant': 'qdrant.exe', 'go': 'go/bin/go.exe', 'plugin_source': 'dify-plugin-daemon-0.6.10/go.mod'}[name]
        if not executable.is_file():
            raise ValueError('发行包缺少已核查的必要入口')
        return executable
    return target


def install(root: Path, component: str) -> dict:
    """同一隔离根的安装/构建只能运行一个；资源故障没有安装豁免。"""
    from windows_services import exclusive_operation
    if load(root).get('resource_incident', {}).get('active'):
        raise RuntimeError('资源异常后的安装已暂停；API单次确认不能恢复安装或大型构建')
    with exclusive_operation(root, 'install'):
        return _install(root, component)


def _install(root: Path, component: str) -> dict:
    data = load(root)
    if data.get('resource_incident', {}).get('active'):
        raise RuntimeError('资源异常后的安装已暂停；需处理异常并取得用户恢复授权，禁止自动重试')
    uv = data['binaries']['uv']
    if component in ASSETS:
        executable = download_asset(root, component)
        data['binaries'][component] = str(executable)
    elif component == 'plugin_daemon':
        go = download_asset(root, 'go')
        source = download_asset(root, 'plugin_source').parent
        executable = root / 'tools/plugin-daemon-server.exe'
        env = base_environment({'GOTOOLCHAIN': 'local', 'GOPATH': str(root / 'tools/gopath'),
            'GOCACHE': str(root / 'tools/go-cache'), 'GOPROXY': 'https://proxy.golang.org',
            'GOSUMDB': 'sum.golang.org', 'CGO_ENABLED': '0'})
        run_step(root, 'plugin-build', [str(go), 'build', '-trimpath', '-o', str(executable), './cmd/server'], cwd=source, env=env)
        data['binaries'][component] = str(executable)
    elif component == 'python313':
        run_step(root, component, [uv, 'python', 'install', '3.13.14', '--install-dir', str(root / 'tools/python')])
        found = list((root / 'tools/python').glob('cpython-3.13.*/python.exe'))
        if len(found) != 1:
            raise RuntimeError('未找到项目独立Python3.13运行时')
        data['binaries']['python313'] = str(found[0])
    elif component in {'openwebui', 'dify'}:
        python = data['binaries'].get('python312' if component == 'openwebui' else 'python313')
        if not python or not Path(python).is_file():
            raise RuntimeError('缺少该组件要求的独立Python运行时')
        venv = root / 'venvs' / component
        if not (venv / 'Scripts/python.exe').is_file():
            run_step(root, component + '-venv', [uv, 'venv', str(venv), '--python', python])
        if component == 'openwebui':
            run_step(root, 'openwebui-python', [uv, 'pip', 'install', '--python', str(venv / 'Scripts/python.exe'),
                '-r', str(Path(data['sources']['openwebui']) / 'backend/requirements.txt')], env=base_environment({'UV_INDEX_URL': 'https://pypi.org/simple'}))
        else:
            run_step(root, 'dify-python', [uv, 'sync', '--frozen', '--no-dev', '--no-default-groups',
                '--group', 'kms', '--group', 'storage', '--group', 'tools', '--group', 'vdb-qdrant',
                '--project', str(Path(data['sources']['dify']) / 'api'), '--python', python],
                env=base_environment({'UV_PROJECT_ENVIRONMENT': str(venv), 'UV_INDEX_URL': 'https://pypi.org/simple'}))
    elif component in {'openwebui-web', 'openwebui-web-build', 'dify-web'}:
        is_webui = component.startswith('openwebui')
        node = Path(data['binaries']['node22' if is_webui else 'node24'])
        project = Path(data['sources']['openwebui' if is_webui else 'dify'])
        env = base_environment({'PATH': str(node.parent) + os.pathsep + base_environment()['PATH'], 'NODE_OPTIONS': '--max-old-space-size=4096'})
        if is_webui:
            npm = node.parent / 'node_modules/npm/bin/npm-cli.js'
            if component == 'openwebui-web':
                run_step(root, 'openwebui-npm', [str(node), str(npm), 'ci', '--no-audit', '--no-fund'], cwd=project, env=env)
            elif not (project / 'node_modules/.package-lock.json').is_file():
                raise RuntimeError('增量构建要求先完成原生npm ci')
            from windows_patch import apply_crypto_path_fix
            apply_crypto_path_fix(project)
            run_step(root, 'openwebui-build', [str(node), str(npm), 'run', 'build'], cwd=project, env=env)
        else:
            # 使用项目锁要求的pnpm，不切换系统pnpm。
            corepack = node.parent / 'node_modules/corepack/dist/corepack.js'
            env['COREPACK_HOME'] = str(root / 'tools/corepack')
            shims = root / 'tools/corepack-shims'
            shims.mkdir(parents=True, exist_ok=True)
            run_step(root, 'dify-pnpm-shims', [str(node), str(corepack), 'enable', '--install-directory', str(shims)], env=env)
            env['PATH'] = str(shims) + os.pathsep + env['PATH']
            # 原生workspace安装会配置Git hooks；给源码副本独立Git元数据，避免碰到外层平台仓库。
            env.update({'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
            if not (project / '.git').exists():
                run_step(root, 'dify-source-git', [str(LOCAL_BINARIES['git']), 'init', str(project)], env=env)
            env.update(dify_frontend_environment(data))
            run_step(root, 'dify-pnpm', [str(node), str(corepack), 'pnpm', 'install', '--frozen-lockfile', '--store-dir', str(root / 'tools/pnpm-store')], cwd=project / 'web', env=env)
            run_step(root, 'dify-build', [str(node), str(corepack), 'pnpm', 'build'], cwd=project / 'web', env=env)
    else:
        raise ValueError('未知安装组件')
    def complete(state):
        if component in ASSETS or component in {'python313', 'plugin_daemon'}:
            state['binaries'][component] = data['binaries'][component]
        state['installations'][component] = {'completed_at': datetime.now(timezone.utc).isoformat()}
    update_runtime(root, complete)
    return {'component': component, 'installed': True}


def owned_process(root: Path, name: str, data: dict) -> psutil.Process | None:
    info = data.get('processes', {}).get(name)
    if not info:
        return None
    try:
        process = psutil.Process(info['pid'])
        if abs(process.create_time() - info['create_time']) > 0.01 or Path(process.exe()).resolve() != Path(info['executable']).resolve():
            raise RuntimeError('进程ID已被复用，禁止操作其他进程')
        return process
    except psutil.NoSuchProcess:
        return None


def same_launch(current: dict | None, expected: dict | None) -> bool:
    """必须匹配本次随机launch_id及完整进程身份，不能只凭组件名称/PID清理。"""
    keys = ('launch_id', 'pid', 'create_time', 'executable')
    return bool(current and expected and expected.get('launch_id') and
        all(current.get(key) == expected.get(key) for key in keys))


def cleanup_created_process(process) -> dict:
    """仅回收本次Popen句柄及可核实后代；任何存活/核验失败都明确保留在结果中。"""
    if process is None:
        return {'verified': True, 'attempted_pids': [], 'alive_pids': [], 'errors': [], 'scope': '未创建Popen进程'}
    children, errors = [], []
    if process.poll() is None:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            errors.append('enumerate:' + type(error).__name__)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            errors.append('terminate_child:' + type(error).__name__)
    if process.poll() is None:
        try:
            # 使用Popen原始句柄，不按名称查找或扩大到别的进程。
            process.terminate()
        except OSError as error:
            errors.append('terminate_parent:' + type(error).__name__)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        errors.append('parent_wait_timeout')
    except OSError as error:
        errors.append('parent_wait:' + type(error).__name__)
    alive = []
    try:
        _, alive = psutil.wait_procs(children, timeout=5)
    except psutil.Error as error:
        errors.append('children_wait:' + type(error).__name__)
        alive = children
    alive_pids = sorted({child.pid for child in alive} | ({process.pid} if process.poll() is None else set()))
    return {'verified': not alive_pids and not errors, 'attempted_pids': [process.pid, *[child.pid for child in children]],
        'alive_pids': alive_pids, 'errors': errors, 'scope': '本次Popen句柄与已核实后代'}


def spawn(root: Path, name: str, command: list[str], *, cwd: Path, env: dict[str, str], metadata: dict | None = None) -> dict:
    data = load(root)
    if owned_process(root, name, data):
        return {'component': name, 'running': True, 'already_running': True}
    port = data['ports'].get(name)
    if port and not available_port(port):
        raise RuntimeError(f'本机端口{port}已被其他进程占用，未连接该服务')
    logfile = root / 'logs' / (name + '.log')
    process, info, phase = None, None, 'create'
    launch_id = secrets.token_hex(12)
    try:
        with logfile.open('ab') as output:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        phase = 'initial_process_check'
        time.sleep(0.2)
        if process.poll() is not None:
            raise RuntimeError(f'{name}启动退出，请核对本机日志：{logfile}')
        created = psutil.Process(process.pid)
        info = {'pid': process.pid, 'create_time': created.create_time(), 'executable': created.exe(), 'port': port, 'launch_id': launch_id}
        if metadata:
            # 调用方补充运行语义，不允许覆盖归属判断所依赖的进程字段。
            if set(metadata) & set(info):
                raise ValueError('运行metadata不能覆盖进程归属字段')
            info.update(metadata)
        phase = 'register_process'
        update_runtime(root, lambda state: state['processes'].update({name: info}))
    except Exception as error:
        cleanup = cleanup_created_process(process)
        registered = None
        if info is not None:
            try:
                current = load(root).get('processes', {}).get(name)
                if same_launch(current, info):
                    registered = {key: info[key] for key in ('pid', 'create_time', 'executable', 'port', 'launch_id')}
                    if cleanup['verified']:
                        def remove_own(state):
                            if same_launch(state.get('processes', {}).get(name), info):
                                state['processes'].pop(name, None)
                        update_runtime(root, remove_own)
            except (OSError, ValueError, RuntimeError) as registry_error:
                cleanup['errors'].append('registry_verify:' + type(registry_error).__name__)
                cleanup['verified'] = False
        raise NativeSpawnError(phase=phase, error_type=type(error).__name__, cleanup=cleanup,
            registered_process=registered) from None
    return {'component': name, 'started': True, **info}


def start(root: Path, component: str, *, api_only: bool = False, ack_resource_incident: bool = False) -> dict:
    """API单次确认只影响本次OWUI启动，不清除故障、不恢复安装或其他原生服务。"""
    data = load(root)
    if (api_only or ack_resource_incident) and component != 'openwebui':
        raise ValueError('API模式及资源确认仅适用于Open WebUI')
    if ack_resource_incident and not api_only:
        raise ValueError('资源确认必须与--api-only一起使用')
    acknowledged_api = component == 'openwebui' and api_only and ack_resource_incident
    if data.get('resource_incident', {}).get('active') and not acknowledged_api:
        raise RuntimeError('资源异常后的原生启动已暂停；需处理异常并取得用户恢复授权')
    if component == 'openwebui':
        from windows_services import exclusive_operation, openwebui_paths, start_openwebui
        openwebui_paths(sys.modules[__name__], root, data, api_only=api_only)
        secret = credentials(root)
        with exclusive_operation(root, 'openwebui-start'):
            return start_openwebui(sys.modules[__name__], root, load(root), secret,
                api_only=api_only, acknowledged_incident=acknowledged_api)
    secret = credentials(root)
    if component == 'redis':
        config = root / 'redis/runtime.conf'
        config.write_text('\n'.join(['bind 127.0.0.1', 'protected-mode yes', f'port {data["ports"][component]}',
            f'dir "{(root / "redis/data").as_posix()}"', 'appendonly yes', 'maxmemory 512mb', 'maxmemory-policy noeviction',
            'requirepass ' + secret['redis_password']]) + '\n', 'utf-8')
        return spawn(root, component, [data['binaries']['redis'], str(config)], cwd=root / 'redis', env=base_environment())
    if component == 'qdrant':
        executable = data['binaries'].get(component)
        if not executable:
            raise RuntimeError('先安装经SHA256核验的Qdrant Windows发行包')
        env = base_environment({'QDRANT__SERVICE__HOST': '127.0.0.1', 'QDRANT__SERVICE__HTTP_PORT': str(data['ports'][component]),
            'QDRANT__SERVICE__GRPC_PORT': '16334', 'QDRANT__STORAGE__STORAGE_PATH': str(root / 'qdrant/storage'),
            'QDRANT__SERVICE__API_KEY': secret['qdrant_key'], 'QDRANT__TELEMETRY_DISABLED': 'true'})
        return spawn(root, component, [executable], cwd=root / 'qdrant', env=env)
    if component == 'agent_v2':
        raise RuntimeError('Agent V2 openshell没有Windows wheel，shellctl依赖tmux/Linux隔离；不能将关闭隔离伪装成等价部署')
    from windows_services import start_service
    return start_service(sys.modules[__name__], root, component, data, secret)


def stop(root: Path, component: str) -> dict:
    data = load(root)
    process = owned_process(root, component, data)
    if not process:
        return {'component': component, 'running': False}
    if component == 'postgres':
        executable = Path(data['binaries']['postgres']).parent / 'pg_ctl.exe'
        run_step(root, 'postgres-stop', [str(executable), '-D', str(root / 'postgres/data'), '-m', 'fast', '-w', '-t', '15', 'stop'], timeout=20)
        update_runtime(root, lambda state: state['processes'].pop(component, None))
        return {'component': component, 'stopped': True}
    if component == 'redis':
        from windows_services import require_service
        require_service(sys.modules[__name__], root, component)
        executable = Path(data['binaries']['redis']).with_name('redis-cli.exe')
        # 密码只经子进程环境传入，不进入命令行或日志；AOF由Redis正常关闭时刷盘。
        run_step(root, 'redis-stop', [str(executable), '-h', '127.0.0.1', '-p', str(data['ports']['redis']), 'shutdown', 'save'],
            env=base_environment({'REDISCLI_AUTH': credentials(root)['redis_password']}), timeout=10)
        process.wait(timeout=10)
        update_runtime(root, lambda state: state['processes'].pop(component, None))
        return {'component': component, 'stopped': True}
    children = process.children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        pass
    _, alive = psutil.wait_procs([*children, process], timeout=15)
    if alive:
        raise RuntimeError('本项目进程未在预算内停止；不扩大范围强杀其他进程')
    update_runtime(root, lambda state: state['processes'].pop(component, None))
    return {'component': component, 'stopped': True}


def doctor(root: Path) -> dict:
    data = load(root) if (root / 'runtime.json').is_file() else None
    binaries = data['binaries'] if data else {k: str(v) for k, v in LOCAL_BINARIES.items()}
    return {'mode': 'windows-native', 'prepared': bool(data and data.get('prepared')), 'root': str(root.resolve()),
        'binaries': {key: {'path': value, 'exists': Path(value).is_file()} for key, value in binaries.items()},
        'ports': {name: {'port': port, 'free': available_port(port)} for name, port in PORTS.items()},
        'processes': {name: process_status(root, name, data) for name in data['processes']} if data else {},
        'installations': data.get('installations', {}) if data else {}, 'database_connections': 0,
        'installation_failures': data.get('installation_failures', {}) if data else {},
        'resource_incident': data.get('resource_incident') if data else None,
        'native_acceptance': data.get('native_acceptance', {}) if data else {},
        'blockers': [{'component': 'agent_v2', 'reason': 'openshell没有Windows wheel；shellctl执行tmux并依赖Linux Landlock，原样无法达到同等隔离'}]}


def process_status(root: Path, name: str, data: dict) -> dict:
    """只读展示进程归属和当前RSS，供协调者串行监控；不自动停止服务。"""
    from windows_services import process_tree_rss
    info = data['processes'][name]
    process = owned_process(root, name, data)
    result = {**info, 'running': bool(process)}
    if process:
        try:
            result['current_tree_rss_bytes'] = process_tree_rss(process)
            limit = info.get('rss_limit_bytes')
            result['rss_over_budget'] = bool(limit and result['current_tree_rss_bytes'] > limit)
        except psutil.NoSuchProcess:
            result['running'] = False
    return result


def smoke(root: Path, component: str) -> dict:
    """先核验本机进程和监听归属，再进行一次有限健康/静态页检查。"""
    import httpx
    from windows_services import require_service
    data = load(root)
    info = data.get('processes', {}).get(component, {})
    api_only = component == 'openwebui' and info.get('mode') == 'api_only'
    if data.get('resource_incident', {}).get('active') and not (api_only and info.get('acknowledged_resource_incident')):
        raise RuntimeError('资源故障仍有效；只允许明确确认后启动的API-only进程做有限健康检查')
    require_service(sys.modules[__name__], root, component)
    paths = {'openwebui': ['/health'] if api_only else ['/health', '/'], 'dify_api': ['/health'], 'dify_web': ['/']}.get(component)
    if paths is None:
        raise ValueError('smoke只适用于原生HTTP应用，不隐式查询数据库')
    checks = []
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        for path in paths:
            response = client.get(f'http://127.0.0.1:{data["ports"][component]}' + path)
            checks.append({'path': path, 'status': response.status_code,
                'html': 'text/html' in response.headers.get('content-type', '')})
            if response.status_code != 200:
                break
    result = {'component': component, 'checks': checks, 'passed': len(checks) == len(paths) and all(c['status'] == 200 for c in checks),
        'mode': 'api_only' if api_only else 'full', 'frontend_available': False if api_only else info.get('frontend_available'),
        'scope': '仅API健康，无原生首页；不是登录/模型/流程/知识验收' if api_only else '本机归属、HTTP健康和原生静态页；不是登录/模型/流程/知识验收', 'model_calls': 0}
    update_runtime(root, lambda state: state.setdefault('http_checks', {}).update({component: result}))
    return result


def dify_frontend_environment(data: dict) -> dict[str, str]:
    origin = f'http://127.0.0.1:{data["ports"]["dify_api"]}'
    return {'NEXT_PUBLIC_DEPLOY_ENV': 'PRODUCTION', 'CONSOLE_API_URL': origin,
        'NEXT_PUBLIC_API_PREFIX': origin + '/console/api', 'NEXT_PUBLIC_PUBLIC_API_PREFIX': origin + '/api',
        'NEXT_PUBLIC_SOCKET_URL': origin.replace('http:', 'ws:'), 'NEXT_TELEMETRY_DISABLED': '1'}


def openwebui_provider_environment() -> dict[str, str]:
    """模型凭据仅交给目标子进程环境，不记录或打印返回值。"""
    sys.path.insert(0, str(REPO / 'services/platform'))
    from agent_platform.core.provider_settings import LocalProviderStore
    return LocalProviderStore().openwebui_environment()


def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Windows本机原生隔离部署，不使用Docker或WSL')
    parser.add_argument('action', choices=['doctor', 'prepare', 'install', 'start', 'stop', 'smoke'], nargs='?', default='doctor')
    parser.add_argument('component', nargs='?')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--dify-source', type=Path, default=REPO.parent / 'dify-main')
    parser.add_argument('--openwebui-source', type=Path, default=REPO.parent / 'open-webui-main')
    parser.add_argument('--api-only', action='store_true', help='仅启动Open WebUI API，明确没有原生首页')
    parser.add_argument('--ack-resource-incident', action='store_true', help='仅确认本次API-only启动，不解除安装/构建暂停')
    args = parser.parse_args()
    if (args.api_only or args.ack_resource_incident) and (args.action != 'start' or args.component != 'openwebui'):
        parser.error('--api-only/--ack-resource-incident仅适用于start openwebui')
    root = args.root.resolve()
    if args.action == 'doctor': result = doctor(root)
    elif args.action == 'prepare': result = {'prepared': prepare(root, args.dify_source, args.openwebui_source).get('prepared', False), 'root': str(root)}
    elif not args.component: parser.error('该操作需要指定组件')
    elif args.action == 'install': result = install(root, args.component)
    elif args.action == 'start': result = start(root, args.component, api_only=args.api_only, ack_resource_incident=args.ack_resource_incident)
    elif args.action == 'smoke': result = smoke(root, args.component)
    else: result = stop(root, args.component)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
