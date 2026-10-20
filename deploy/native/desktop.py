"""Windows 本机双服务入口：沿用所属进程与数据，服务成功启动后持续供用户使用。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from cryptography.fernet import Fernet

import windows
import windows_services as services

ROOT = windows.DEFAULT_ROOT
PLATFORM_DATA = windows.REPO / '.local/platform'
PLATFORM_URL = 'http://127.0.0.1:8080'
CHAT_URL = 'http://127.0.0.1:13000'


def start_platform() -> dict:
    """只启动固定本项目 SQLite 和已有前端，拒绝接管占用端口或来源未知的数据。"""
    data = windows.load(ROOT)
    info = data.get('processes', {}).get('platform', {})
    if windows.owned_process(ROOT, 'platform', data):
        if info.get('ready') and info.get('data_directory') == str(PLATFORM_DATA):
            return {'reused': True, 'url': PLATFORM_URL}
        raise RuntimeError('平台已有未就绪进程，请核查后停止')
    if not windows.available_port(8080):
        raise RuntimeError('8080端口已被占用，未接管已有服务')
    services.require_local_drive(windows.ensure_inside(windows.REPO / '.local', PLATFORM_DATA))
    binding = {'data_directory': str(PLATFORM_DATA), 'sqlite_file': str(PLATFORM_DATA / 'platform.sqlite3')}
    previous = data.get('databases', {}).get('platform')
    if previous != binding and (previous or PLATFORM_DATA.exists()):
        raise RuntimeError('平台数据目录尚未登记归属，停止启动')
    if not (windows.REPO / 'apps/web/dist/index.html').is_file():
        raise RuntimeError('缺少平台已编译前端')
    windows.update_runtime(ROOT, lambda state: state.setdefault('databases', {}).update(platform=binding))
    env = windows.base_environment({
        'PYTHONPATH': str(windows.REPO / 'services/platform'),
        'AGENT_DATA_DIR': str(PLATFORM_DATA), 'AGENT_ENV': 'development',
        'AGENT_DATABASE_URL': 'sqlite:///' + (PLATFORM_DATA / 'platform.sqlite3').as_posix(),
        'AGENT_ALLOW_REMOTE_CONNECTIONS': 'false',
    })
    launched = windows.spawn(ROOT, 'platform', [
        str(windows.REPO / '.venv/Scripts/python.exe'), '-m', 'uvicorn',
        'agent_platform.main:create_app', '--factory', '--host', '127.0.0.1', '--port', '8080',
    ], cwd=windows.REPO, env=env, metadata={**binding, 'ready': False})
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            process = windows.owned_process(ROOT, 'platform', windows.load(ROOT))
            if not process:
                raise RuntimeError('平台进程已退出')
            if 8080 in services.listening_processes(process):
                windows.update_runtime(ROOT, lambda state: state['processes']['platform'].update(ready=True))
                return {'ready': True, 'url': PLATFORM_URL}
            time.sleep(.25)
        raise TimeoutError('平台45秒内未就绪')
    except Exception:
        if windows.same_launch(windows.load(ROOT).get('processes', {}).get('platform'), launched):
            windows.stop(ROOT, 'platform')
        raise


def start() -> dict:
    """复用完整服务；遵照用户要求取消内存门槛，保留180秒超时与进程归属核验。"""
    data = windows.load(ROOT)
    info = data.get('processes', {}).get('openwebui', {})
    if windows.owned_process(ROOT, 'openwebui', data):
        if not info.get('ready') or info.get('mode') != 'full':
            raise RuntimeError('已有Open WebUI进程不是已就绪完整模式，请先核查')
    else:
        windows.start(ROOT, 'openwebui', ack_resource_incident=True, startup_seconds=180, limit_memory=False)
    platform = start_platform()
    return {'platform': platform, 'chat': CHAT_URL}


def accounts() -> None:
    """仅向用户主动打开的本机控制台显示交付账号；不读取或显示模型密钥。"""
    key = windows.REPO / '.local/desktop-access.key'
    encrypted = windows.REPO / '.local/desktop-access.enc'
    if not encrypted.exists():
        raise RuntimeError('尚未生成交付账号，请先在页面完成初始化')
    value = json.loads(Fernet(key.read_bytes()).decrypt(encrypted.read_bytes()))
    for label, account in value.items():
        print(f"{label}\n  地址：{account['url']}\n  账号：{account['username']}\n  密码：{account['password']}\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'accounts'], default='start', nargs='?')
    args = parser.parse_args()
    if args.action == 'accounts':
        accounts()
    elif args.action == 'stop':
        print(json.dumps([windows.stop(ROOT, name) for name in ['platform', 'openwebui']], ensure_ascii=False))
    else:
        print(json.dumps(start(), ensure_ascii=False))
