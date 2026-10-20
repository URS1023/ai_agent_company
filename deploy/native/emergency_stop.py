"""资源异常时停止本项目原生安装/构建进程树；不停止数据库或其他Agent服务。"""

import json
from pathlib import Path
import psutil

ROOT = Path(__file__).resolve().parents[2]


def main():
    owned = []
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            args = process.info['cmdline'] or []
            if (any(arg.replace('\\', '/').endswith('deploy/native/windows.py') for arg in args)
                    and 'install' in args and Path(process.cwd()).resolve() == ROOT):
                owned.append(process)
        except (psutil.Error, OSError):
            continue
    selected = {}
    for process in owned:
        try:
            for child in [process, *process.children(recursive=True)]:
                selected[child.pid] = child
        except psutil.Error:
            continue
    stopping = {'stopping_owned_installers': [{'pid': process.pid, 'name': process.name()}
        for process in selected.values()]}
    print(json.dumps(stopping, ensure_ascii=False))
    for process in reversed(list(selected.values())):
        try:
            process.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(list(selected.values()), timeout=10)
    # Windows提交量是所有进程实际资源上限的重要证据，不打印任何进程环境或密钥。
    import ctypes
    class PerformanceInformation(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in [
            'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal', 'PhysicalAvailable',
            'SystemCache', 'KernelTotal', 'KernelPaged', 'KernelNonpaged', 'PageSize']] + [
            ('HandleCount', ctypes.c_ulong), ('ProcessCount', ctypes.c_ulong), ('ThreadCount', ctypes.c_ulong)]
    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    valid = ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb)
    result = {'stopped': [process.pid for process in gone], 'still_alive': [process.pid for process in alive],
        'available_memory_mb': round(psutil.virtual_memory().available / 1024**2),
        'memory_percent': psutil.virtual_memory().percent,
        'commit_mb': round(info.CommitTotal * info.PageSize / 1024**2) if valid else None,
        'commit_limit_mb': round(info.CommitLimit * info.PageSize / 1024**2) if valid else None}
    print(json.dumps(result, ensure_ascii=False))


def inspect_only():
    """不发SQL/HTTP，仅确认新数据库无客户端套接字及有无明确属于本项目的编译器。"""
    runtime_root = ROOT / '.local/native_windows'
    data = json.loads((runtime_root / 'runtime.json').read_text('utf-8'))
    database = data['processes'].get('postgres')
    connections = []
    if database:
        parent = psutil.Process(database['pid'])
        if abs(parent.create_time() - database['create_time']) > 0.01:
            raise RuntimeError('PG PID归属变化，拒绝判断')
        for process in [parent, *parent.children(recursive=True)]:
            connections.extend({'pid': process.pid, 'status': connection.status,
                'port': connection.laddr.port} for connection in process.net_connections(kind='inet')
                if connection.status == 'ESTABLISHED')
    compilers = []
    for process in psutil.process_iter(['exe', 'name']):
        try:
            executable = Path(process.info['exe'] or '').resolve()
            if executable.is_relative_to(runtime_root / 'tools/native-build/go'):
                compilers.append({'pid': process.pid, 'name': process.info['name']})
        except (psutil.Error, OSError):
            pass
    print(json.dumps({'postgres_established_clients': connections, 'owned_go_compilers': compilers,
        'database_queries_issued_by_check': 0, 'native_application_started': False}, ensure_ascii=False))


def close_native_services():
    """主协调者授权的安全停止；先核验本轮三个实例无客户端，不连接旧服务。"""
    import windows
    root = ROOT / '.local/native_windows'
    data = windows.load(root)
    for name in ['postgres', 'redis', 'qdrant']:
        process = windows.owned_process(root, name, data)
        if process:
            for child in [process, *process.children(recursive=True)]:
                if any(connection.status == 'ESTABLISHED' for connection in child.net_connections(kind='inet')):
                    raise RuntimeError(f'{name}仍有客户端连接，禁止自动关闭')
    for name in ['postgres', 'redis', 'qdrant']:
        print(json.dumps(windows.stop(root, name), ensure_ascii=False))


def record_incident():
    """只落盘已确认故障，阻止下一轮无授权自动恢复；不运行检查或构建。"""
    from datetime import datetime, timezone
    import windows
    root = ROOT / '.local/native_windows'
    def change(state):
        state['resource_incident'] = {'active': True, 'recorded_at': datetime.now(timezone.utc).isoformat(),
            'reason': 'Windows系统提交量/堆资源异常；主协调者要求停止且待用户恢复授权',
            'after_installers_stopped': {'available_memory_mb': 4336, 'commit_mb': 33424, 'commit_limit_mb': 40113},
            'owned_installers_stopped': [8980, 29288, 12776, 28748, 28932, 2112, 22968, 25740],
            'native_services_safely_stopped': ['postgres', 'redis', 'qdrant'], 'outstanding_database_clients': 0}
        state['installation_failures'] = {
            'dify': {'status': 'partial_install_failed', 'evidence': 'uv install google-cloud-aiplatform: Windows os error1450 系统资源不足'},
            'dify-web': {'status': 'build_failed', 'evidence': 'Next/Rust memory allocation of 278528 bytes failed；exit3221226505'},
            'plugin_daemon': {'status': 'build_failed', 'evidence': 'Go编译器GC/内存堆栈；exit1；官方Windows发行工具是CLI'},
            'openwebui-web': {'status': 'interrupted', 'evidence': 'Pyodide离线包准备中按资源异常指令终止，原WASM校验已通过'}}
        state['native_acceptance'] = {'openwebui': False, 'dify': False, 'agent_v2': False}
        state['installations'].pop('plugin_daemon', None)
        cli = state['binaries'].get('plugin_daemon', '')
        if cli.endswith('plugin-daemon.exe'):
            state['binaries']['plugin_cli'] = state['binaries'].pop('plugin_daemon')
    windows.update_runtime(root, change)
    print(json.dumps({'resource_incident_recorded': True, 'automatic_install_or_start_blocked': True}, ensure_ascii=False))


if __name__ == '__main__':
    import sys
    if '--inspect-only' in sys.argv:
        inspect_only()
    elif '--stop-services' in sys.argv:
        close_native_services()
    elif '--record-incident' in sys.argv:
        record_incident()
    else:
        main()
