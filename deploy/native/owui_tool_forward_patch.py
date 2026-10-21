"""锁定OWUI工具头函数的小适配；只给受管本机工具签名，不启用全局用户转发。"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

PATCH_ID = 'openwebui-0.12.3-platform-tool-user-forward-v1'
ORIGINAL_SHA256 = '3bcb64c6d635441532dd8989e575350512858e5f8ed700eff5ffd52e73feb473'
PATCHED_SHA256 = 'c3b64176debda0979c005bf8a7ecd5e44b59da2bd73235b0cefc87a6ef26118a'
SOURCE_FILE = Path('backend/open_webui/utils/tools.py')
CONFIG_FILE = 'openwebui-tool-forwarding.json'
CONFIG_KIND = 'ai-agent-company-owui-tool-forwarding-v1'
SECRET_KEY = 'openwebui_tool_forward_jwt_secret'
MARKER = {'kind': 'ai-agent-company-readonly-v1', 'version': 1}
ANCHOR = b'    # Add user info headers if enabled\n'
BLOCK = '''    # 仅本项目受管工具连接转发签名；普通模型和其他服务器沿原逻辑。
    tool_config = connection.get('config') or {}
    pinned_id = os.environ.get('AIC_PLATFORM_TOOL_SERVER_ID', '')
    pinned_url = os.environ.get('AIC_PLATFORM_TOOL_BRIDGE_URL', '')
    if ('platform_delegation' in tool_config or (pinned_id and server_id == pinned_id)
            or (pinned_url and connection.get('url') == pinned_url)):
        from open_webui.env import FORWARD_USER_INFO_HEADER_JWT_SECRET, FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS
        from open_webui.utils.headers import _mint_forward_user_jwt
        marker = tool_config.get('platform_delegation')
        if (marker != {'kind': 'ai-agent-company-readonly-v1', 'version': 1}
                or not isinstance(marker, dict) or type(marker.get('version')) is not int
                or not pinned_id or server_id != pinned_id or not pinned_url
                or connection.get('url') != pinned_url or connection.get('type', 'openapi') != 'openapi'
                or auth_type != 'bearer' or connection.get('forward_cookies', False) is not False
                or not isinstance(connection.get('key'), str) or not connection['key'].startswith('aic_tool_')
                or ENABLE_FORWARD_USER_INFO_HEADERS or user is None
                or not isinstance(FORWARD_USER_INFO_HEADER_JWT_SECRET, str) or len(FORWARD_USER_INFO_HEADER_JWT_SECRET) < 32
                or type(FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS) is not int
                or not 1 <= FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS <= 300
                or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value)
                    for value in (getattr(user, 'id', None), metadata.get('chat_id'), metadata.get('message_id')))):
            raise RuntimeError('platform_tool_forwarding_invalid')
        # 自定义头已经处理完，再覆盖三项准确身份，避免配置中的字面值冒充当前人员。
        headers['X-OpenWebUI-User-Jwt'] = _mint_forward_user_jwt(user)
        headers['X-OpenWebUI-Chat-Id'] = metadata['chat_id']
        headers['X-OpenWebUI-Message-Id'] = metadata['message_id']
        return headers, {}

'''.encode('utf-8')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def prepare_patch(content: bytes) -> tuple[bytes, dict]:
    """只识别锁定原摘要和已审补丁摘要；未知版本不做启发式修改。"""
    before = digest(content)
    if before == PATCHED_SHA256:
        return content, {'patch_id': PATCH_ID, 'applied': False, 'before_sha256': before, 'after_sha256': before}
    if before != ORIGINAL_SHA256 or content.count(ANCHOR) != 1:
        raise RuntimeError('owui_tool_patch_source_unknown')
    changed = content.replace(ANCHOR, BLOCK + ANCHOR, 1)
    if digest(changed) != PATCHED_SHA256:
        raise RuntimeError('owui_tool_patch_digest_mismatch')
    compile(changed, str(SOURCE_FILE), 'exec')
    return changed, {'patch_id': PATCH_ID, 'applied': True, 'before_sha256': before,
                     'after_sha256': PATCHED_SHA256}


def managed_source(root: Path) -> tuple[Path, dict]:
    """只定位本运行根固定源码副本，禁止改参考仓库或通过链接越界。"""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise RuntimeError('owui_tool_patch_root_invalid')
    manifest = json.loads((root / 'runtime.json').read_text('utf-8'))
    source = root / 'sources/openwebui'
    target = source / SOURCE_FILE
    if (manifest.get('kind') != 'manufacturing-native-windows' or manifest.get('root') != str(root)
            or not isinstance(manifest.get('id'), str) or not manifest['id']
            or manifest.get('sources', {}).get('openwebui') != str(source)
            or source.resolve() != source or target.resolve() != target or not target.is_file()
            or target.stat().st_size > 512 * 1024
            or json.loads((source / 'package.json').read_text('utf-8')).get('version') != '0.12.3'):
        raise RuntimeError('owui_tool_patch_source_invalid')
    return target, manifest


def apply_patch(root: Path) -> dict:
    """只写已核的唯一源码文件，保持权限并拒绝并发漂移；不启动服务或写凭据。"""
    target, manifest = managed_source(root)
    original = target.read_bytes()
    before_stat = target.stat()
    content, receipt = prepare_patch(original)
    receipt = {**receipt, 'runtime_id': manifest['id'], 'source_file': str(SOURCE_FILE),
               'global_user_forwarding': False}
    if not receipt['applied']:
        return receipt
    pending = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.aic-tool-', suffix='.pending', delete=False) as stream:
            pending = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        pending.chmod(stat.S_IMODE(before_stat.st_mode))
        if (target.resolve() != target or target.stat().st_ino != before_stat.st_ino
                or target.read_bytes() != original):
            raise RuntimeError('owui_tool_patch_source_changed')
        pending.replace(target)
        pending = None
        if digest(target.read_bytes()) != PATCHED_SHA256:
            raise RuntimeError('owui_tool_patch_write_unconfirmed')
        return receipt
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def forwarding_environment(runtime, paths: dict, secret: dict) -> dict:
    """只从受管非密文开关和已解密凭据构造子进程环境，不打印或落盘密钥。"""
    root = paths['data'].parent.parent
    path = root / CONFIG_FILE
    if not path.exists():
        return {}
    if path.resolve() != path or path.stat().st_size > 16 * 1024:
        raise RuntimeError('owui_tool_forwarding_config_invalid')
    config = json.loads(path.read_text('utf-8'))
    target, manifest = managed_source(root)
    if (not isinstance(config, dict) or set(config) != {'kind', 'runtime_id', 'enabled', 'server_id', 'bridge_url'}
            or config.get('kind') != CONFIG_KIND or config.get('runtime_id') != manifest['id']
            or type(config.get('enabled')) is not bool):
        raise RuntimeError('owui_tool_forwarding_config_invalid')
    if not config['enabled']:
        return {}
    server_id, url = config.get('server_id'), config.get('bridge_url')
    network = manifest.get('processes', {}).get('wsl-http-bridge', {}).get('network', {})
    try:
        address = ipaddress.IPv4Address(network['bind_ip'])
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (not isinstance(server_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', server_id)
                or not address.is_private or address.is_loopback or address.is_link_local
                or address.is_unspecified or address.is_multicast
                or url != f'http://{address}:13002/platform' or parsed.hostname != str(address)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('owui_tool_forwarding_pin_invalid') from None
    # 只读当前Windows虚拟网卡；旧NAT地址不能被当作任意局域网目标继续使用。
    from wsl_http_bridge import require_host_interface
    require_host_interface(network)
    value = secret.get(SECRET_KEY)
    if (not isinstance(value, str) or not 32 <= len(value) <= 4096 or '\x00' in value
            or value == secret.get('webui_secret') or digest(target.read_bytes()) != PATCHED_SHA256):
        raise RuntimeError('owui_tool_forwarding_not_ready')
    return {'ENABLE_FORWARD_USER_INFO_HEADERS': 'False', 'AIC_PLATFORM_TOOL_SERVER_ID': server_id,
            'AIC_PLATFORM_TOOL_BRIDGE_URL': url, 'FORWARD_USER_INFO_HEADER_JWT_SECRET': value,
            'FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS': '300'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply_patch(args.root), ensure_ascii=False))


if __name__ == '__main__':
    main()
