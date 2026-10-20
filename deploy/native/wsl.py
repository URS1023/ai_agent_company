"""Windows到本项目专用WSL的显式分阶段入口；不调用Docker、不读取旧环境配置。"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

PROJECT = Path(__file__).resolve().parents[2]
DISTRO = 'ai-agent-company'
LINUX_ROOT = '/opt/ai-agent-company'
INSTALL_STAGES = {'tools': 900, 'python': 7200, 'binaries': 3600, 'verify-binaries': 180,
    'verify-agent-binaries': 180, 'plugin-build': 1800, 'plugin-cli-build': 180, 'python-package-repair': 1200,
    'qdrant': 300, 'sandbox': 3600, 'web': 900, 'agent-request-limit-patch': 30}
RUNTIME_BUDGETS = {'prepare': 180, 'init-data': 600, 'init-plugin-data': 240, 'doctor': 30, 'start': 900, 'stop': 180,
    'status': 30, 'reconcile-boot': 60}
RUNTIME_ROLES = {'postgres', 'redis', 'qdrant', 'proxy', 'agent_proxy', 'shellctl', 'code',
    'plugin', 'agent', 'api', 'worker', 'beat', 'web', 'gateway'}


def require_registered_distro() -> None:
    """先核当前用户注册表中的独立D盘WSL2归属，不接管同名异位置发行版。"""
    import winreg
    entries = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Lxss') as key:
        for index in range(winreg.QueryInfoKey(key)[0]):
            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as child:
                if winreg.QueryValueEx(child, 'DistributionName')[0] == DISTRO:
                    entries.append((winreg.QueryValueEx(child, 'BasePath')[0], winreg.QueryValueEx(child, 'Version')[0]))
    if len(entries) != 1:
        raise RuntimeError('owned_wsl_registration_not_unique')
    raw, version = entries[0]
    path = Path(str(raw).removeprefix('\\\\?\\')).absolute()
    expected = Path('D:/WSL/ai-agent-company')
    if version != 2 or path != expected or path.resolve() != expected.absolute() or not (path / 'ext4.vhdx').is_file():
        raise RuntimeError('owned_wsl_disk_or_version_mismatch')


def linux_path(path: Path) -> str:
    """仅转换本项目实际本地盘路径；拒绝UNC、重解析或项目外文件。"""
    original = path.absolute()
    if original.resolve() != original or not original.is_relative_to(PROJECT):
        raise RuntimeError('wsl_source_path_not_owned')
    windows = PureWindowsPath(str(original))
    if len(windows.drive) != 2 or windows.drive[1] != ':' or not windows.drive[0].isalpha():
        raise RuntimeError('wsl_source_requires_local_drive')
    return '/mnt/' + windows.drive[0].lower() + '/' + '/'.join(windows.parts[1:])


def clean_environment() -> dict:
    system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    result = {'SystemRoot': str(system), 'WINDIR': str(system), 'PATH': str(system / 'System32'),
        'PATHEXT': '.COM;.EXE;.BAT;.CMD'}
    for name in ['USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP']:
        if os.environ.get(name):
            result[name] = os.environ[name]
    return result


def parse_nat_network(addresses: list, routes: list, windows_addresses: list) -> dict:
    """交叉核专属发行版eth0、默认网关和Windows虚拟NIC，不猜地址或接受全局监听。"""
    if len(addresses) != 1 or addresses[0].get('ifname') != 'eth0' or 'UP' not in addresses[0].get('flags', []):
        raise RuntimeError('wsl_nat_interface_not_unique')
    values = [item for item in addresses[0].get('addr_info', [])
        if item.get('family') == 'inet' and item.get('scope') == 'global']
    defaults = [item for item in routes if item.get('dst') == 'default' and item.get('dev') == 'eth0']
    if len(values) != 1 or len(defaults) != 1:
        raise RuntimeError('wsl_nat_address_or_gateway_not_unique')
    source = ipaddress.IPv4Address(values[0]['local'])
    gateway = ipaddress.IPv4Address(defaults[0]['gateway'])
    prefix = values[0]['prefixlen']
    if type(prefix) is not int or not 8 <= prefix <= 30:
        raise RuntimeError('wsl_nat_prefix_invalid')
    network = ipaddress.IPv4Network(f'{source}/{prefix}', strict=False)
    if source == gateway or gateway not in network or any(
        not value.is_private or value.is_loopback or value.is_link_local or value.is_multicast or value.is_unspecified
        for value in (source, gateway)
    ):
        raise RuntimeError('wsl_nat_network_not_private')
    matched = [item for item in windows_addresses if item.get('IPAddress') == str(gateway)
        and 'wsl' in str(item.get('InterfaceAlias', '')).lower() and item.get('PrefixLength') == prefix
        and item.get('AddressState') in (4, 'Preferred')]
    if len(matched) != 1 or type(matched[0].get('InterfaceIndex')) is not int:
        raise RuntimeError('wsl_nat_windows_interface_not_verified')
    return {'source_ip': str(source), 'bind_ip': str(gateway), 'prefix_length': prefix,
        'windows_interface_index': matched[0]['InterfaceIndex'], 'windows_interface_alias': matched[0]['InterfaceAlias']}


def verified_nat_network() -> dict:
    """只读操作系统网络与既有keeper归属；不启动服务、不发健康或数据库请求。"""
    require_registered_distro()
    import windows
    import wsl_lifetime
    lifetime = wsl_lifetime.linux_call('status')
    state = windows.load(windows.DEFAULT_ROOT)
    record = state.get('processes', {}).get(wsl_lifetime.COMPONENT, {})
    if (not windows.owned_process(windows.DEFAULT_ROOT, wsl_lifetime.COMPONENT, state)
            or not lifetime.get('running') or lifetime.get('requires_boot_reconciliation')
            or lifetime.get('runtime_id') != record.get('runtime_id') or lifetime.get('keeper_id') != record.get('keeper_id')):
        raise RuntimeError('wsl_nat_lifetime_identity_changed')
    system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    wsl_exe = str(system / 'System32/wsl.exe')

    def read_json(command):
        result = subprocess.run(command, env=clean_environment(), check=True, capture_output=True,
            timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # WSL可能在stderr输出Windows本地编码的提示；只解析约定为JSON的stdout。
        raw = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode('utf-8')
        if len(raw) > 128 * 1024:
            raise RuntimeError('wsl_nat_metadata_budget_exceeded')
        return json.loads(raw)

    prefix = [wsl_exe, '--distribution', DISTRO, '--user', 'root', '--exec', '/usr/sbin/ip', '-j', '-4']
    addresses = read_json([*prefix, 'address', 'show', 'dev', 'eth0'])
    routes = read_json([*prefix, 'route', 'show', 'default'])
    # 固定脚本没有用户输入插值；只读取WSL虚拟网卡，不修改地址/防火墙/转发。
    powershell = str(system / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    script = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*WSL*' } | "
        "Select-Object InterfaceIndex,InterfaceAlias,IPAddress,PrefixLength,AddressState | ConvertTo-Json -Compress")
    interfaces = read_json([powershell, '-NoProfile', '-NonInteractive', '-Command', script])
    network = parse_nat_network(addresses, routes, interfaces if isinstance(interfaces, list) else [interfaces])
    return {**network, 'distro': DISTRO, 'root': LINUX_ROOT, 'runtime_id': lifetime['runtime_id'],
        'boot_id': lifetime['boot_id'], 'keeper_id': lifetime['keeper_id']}


def specification(action: str) -> tuple[list[str], int]:
    setup = linux_path(PROJECT / 'deploy/native/wsl_setup.py')
    runtime = linux_path(PROJECT / 'deploy/native/wsl_runtime.py')
    if action in INSTALL_STAGES:
        return ['/usr/bin/python3', setup, action], INSTALL_STAGES[action]
    if action in RUNTIME_BUDGETS:
        return [LINUX_ROOT + '/tooling/bin/python', runtime, action], RUNTIME_BUDGETS[action]
    if action == 'bootstrap':
        return ['/bin/bash', linux_path(PROJECT / 'deploy/native/wsl-bootstrap.sh')], 900
    if action in {'verify-resume', 'sources-storage-repair'}:
        name = 'dify-source-1.17.1.tar' if action == 'verify-resume' else 'dify-source-1.17.1-clean.tar'
        archive = PROJECT / '.local/native-linux-artifacts' / name
        return ['/usr/bin/python3', setup, action, '--archive', linux_path(archive),
            '--archive-receipt', linux_path(archive.with_suffix('.tar.json'))], 180
    if action == 'web-extract':
        return ['/usr/bin/python3', linux_path(PROJECT / 'deploy/native/wsl_oci.py'), 'extract',
            '--directory', linux_path(PROJECT / '.local/native-linux-artifacts/dify-web-1.17.1-next'),
            '--target', LINUX_ROOT + '/web'], 180
    raise ValueError('unsupported_wsl_action')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='本项目无Docker WSL分阶段准备与生命周期')
    parser.add_argument('action', choices=['bootstrap', 'verify-resume', 'sources-storage-repair', 'web-extract', *INSTALL_STAGES, *RUNTIME_BUDGETS])
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('此桥接入口只在Windows使用；Linux直接调用wsl_setup/wsl_runtime')
    require_registered_distro()
    if args.action in {'start', 'stop'}:
        result = start_runtime() if args.action == 'start' else stop_runtime()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    command, seconds = specification(args.action)
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'
    # Linux timeout控制整个命令组；Windows外层额外留给有界失败记录与所属进程回收。
    grace = 150 if args.action in {'start', 'stop'} else 10
    argv = [str(executable), '--distribution', DISTRO, '--user', 'root', '--exec', '/usr/bin/timeout',
        '--signal=TERM', '--kill-after=' + str(grace), str(seconds), *command]
    try:
        result = subprocess.run(argv, env=clean_environment(), timeout=seconds + grace + 15, check=False)
        return result.returncode
    except subprocess.TimeoutExpired:
        # WSL客户端退出不代表Linux任务已停止；不宣称成功，也不根据进程名批量kill。
        print(json.dumps({'status': 'failed', 'action': args.action, 'error': 'wsl_client_timeout',
            'linux_process_state': 'unknown', 'next_action': '核对专用发行版任务；业务启动可执行wsl.py stop'}))
        return 1


def runtime_call(action: str) -> dict:
    """只调用本项目Linux控制器；状态投影不包含服务密钥或数据库连接字符串。"""
    require_registered_distro()
    command, seconds = specification(action)
    grace = 150 if action in {'start', 'stop'} else 10
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'
    result = subprocess.run([
        str(executable), '--distribution', DISTRO, '--user', 'root', '--exec',
        '/usr/bin/timeout', '--signal=TERM', '--kill-after=' + str(grace), str(seconds), *command,
    ], env=clean_environment(), timeout=seconds + grace + 15, capture_output=True)
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError):
        raise RuntimeError('wsl_runtime_response_invalid:' + action) from None
    if result.returncode:
        raise RuntimeError('wsl_runtime_' + action + '_failed:' + str(value.get('error_code', value.get('error_type', 'unknown'))))
    return value


def start_runtime() -> dict:
    """已完整启动时复用；部分残留不自动补启动或接管，先保留诊断。"""
    import windows
    import wsl_lifetime
    keeper = wsl_lifetime.ensure_windows_lifetime()
    state = runtime_call('status')
    if state['runtime_id'] != keeper['runtime_id']:
        raise RuntimeError('wsl_runtime_identity_changed')
    if not state.get('plugin_database_initialized'):
        raise RuntimeError('wsl_plugin_database_requires_initialization')
    processes = state.get('processes', [])
    active = [item for item in processes if item['owned'] or item['running']]
    if active:
        if {item['role'] for item in active} != RUNTIME_ROLES or not all(
            item['owned'] and item['running'] for item in active
        ):
            raise RuntimeError('wsl_partial_services_require_review')
        state['reused'] = True
    else:
        state = runtime_call('start')
    if {item['role'] for item in state.get('processes', []) if item['owned'] and item['running']} != RUNTIME_ROLES:
        raise RuntimeError('wsl_services_not_all_ready')
    windows.update_runtime(windows.DEFAULT_ROOT, lambda value: value.update(dify_wsl={
        'enabled': True, 'runtime_id': state['runtime_id'], 'url': state['public_url'],
    }))
    return {'ready': True, 'url': state['public_url'], 'reused': state.get('reused', False),
            'runtime_id': state['runtime_id'], 'business_verified': False}


def stop_runtime() -> dict:
    """先核14项服务已停止，才结束前台WSL任务；数据和发行版保留。"""
    import wsl_lifetime
    state = runtime_call('stop')
    if not state.get('stopped'):
        raise RuntimeError('wsl_service_cleanup_incomplete')
    keeper = wsl_lifetime.stop_windows_lifetime()
    return {'stopped': True, 'data_preserved': True, 'lifetime': keeper}


if __name__ == '__main__':
    raise SystemExit(main())
