"""从官方OCI提取预构建Dify Next制品；不依赖Docker、不下载基础镜像、不执行容器。

下载固定清单的三个前端层；匿名token只在内存。提取只允许app/targets/next范围，
记录官方revision与本地额外语音能力差异，不把同版本标签当源码逐字节等价。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request

REPOSITORY = 'langgenius/dify-web'
REGISTRY = 'https://registry-1.docker.io/v2/' + REPOSITORY
MANIFEST = 'sha256:c4baeb297722671a23317b17d2af669f50b36f6239d1d976832a77b2057344e5'
CONFIG = 'sha256:581dd38420dfed0602e54c1847270ed7c3df6bc07bfb000c1b426a1393d13799'
REVISION = '8387590ace4a094de812b7847fc6a4c3a27cd52b'
LAYERS = [
    {'role': 'public', 'digest': 'sha256:8a758d8443779b5d23a44bc34cceab527e270878d4b8c7d19559e0b576ebb295', 'size': 12451606},
    {'role': 'standalone', 'digest': 'sha256:58aff7e4d3abb26b0240e5740bcc77d9c9ff10313447a46a42f436c28aa1e24c', 'size': 40824884},
    {'role': 'static', 'digest': 'sha256:35765cbcbb317c33035bfd392dad04d2c423d6bf79daa922679e37f88eb027fd', 'size': 18990811},
]
GNU_PACKAGES = {'@img/sharp-linuxmusl-x64': '@img/sharp-linux-x64',
    '@img/sharp-libvips-linuxmusl-x64': '@img/sharp-libvips-linux-x64',
    '@next/swc-linux-x64-musl': '@next/swc-linux-x64-gnu'}


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """只跟随Docker官方HTTPS存储跳转，跨域不转发Bearer。"""
    def redirect_request(self, request, fp, code, message, headers, target):
        parsed = urllib.parse.urlparse(target)
        hostname = parsed.hostname or ''
        if parsed.scheme != 'https' or not (hostname.endswith('.docker.com') or
                hostname.endswith('.cloudflare.docker.com') or hostname.endswith('.cloudflarestorage.com')):
            raise RuntimeError('unexpected_registry_redirect')
        redirected = super().redirect_request(request, fp, code, message, headers, target)
        if urllib.parse.urlparse(request.full_url).hostname != hostname:
            redirected.remove_header('Authorization')
        return redirected


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def small_json(url: str, *, token=None, expected=None, opener=None) -> dict:
    headers = {'User-Agent': 'manufacturing-agent-platform/0.1',
        'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    opener = opener or urllib.request.build_opener(SafeRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise RuntimeError('metadata_size_budget_exceeded')
    if expected and 'sha256:' + hashlib.sha256(body).hexdigest() != expected:
        raise RuntimeError('metadata_digest_mismatch')
    return json.loads(body)


def fetch(directory: Path, *, seconds=900) -> dict:
    """唯一一次逐层下载；已有成功文件只验SHA，partial或失败账本禁止静默重拉。"""
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('artifact_directory_symlink')
    ledger = directory / 'fetch.json'
    if ledger.exists():
        previous = json.loads(ledger.read_text())
        if previous.get('status') != 'ready':
            raise RuntimeError('previous_fetch_requires_review')
        for item in LAYERS:
            path = directory / (item['role'] + '.tar.gz')
            if path.stat().st_size != item['size'] or 'sha256:' + sha(path) != item['digest']:
                raise RuntimeError('cached_frontend_layer_changed')
        return previous
    result = {'kind': 'dify-native-web-oci-v1', 'status': 'attempted', 'repository': REPOSITORY,
        'version': '1.17.1', 'manifest_digest': MANIFEST, 'config_digest': CONFIG, 'revision': REVISION,
        'platform': 'linux/amd64', 'source_equivalence_verified': False, 'layers': LAYERS,
        'local_extra_api_paths': ['/agent/{agent_id}/text-to-audio/voices', '/agent/{agent_id}/text-to-audio']}
    ledger.write_text(json.dumps(result, indent=2), encoding='utf-8')
    deadline = time.monotonic() + seconds
    token = None
    try:
        opener = urllib.request.build_opener(SafeRedirect())
        token = small_json('https://auth.docker.io/token?service=registry.docker.io&scope=repository:langgenius/dify-web:pull', opener=opener)['token']
        manifest = small_json(REGISTRY + '/manifests/' + MANIFEST, token=token, expected=MANIFEST, opener=opener)
        if manifest['config']['digest'] != CONFIG:
            raise RuntimeError('official_config_changed')
        config = small_json(REGISTRY + '/blobs/' + CONFIG, token=token, expected=CONFIG, opener=opener)
        if config.get('architecture') != 'amd64' or config.get('os') != 'linux' or config['config']['Labels'].get('org.opencontainers.image.revision') != REVISION:
            raise RuntimeError('official_build_provenance_changed')
        for item in LAYERS:
            if not any(layer['digest'] == item['digest'] and layer['size'] == item['size'] for layer in manifest['layers']):
                raise RuntimeError('frontend_layer_not_in_pinned_manifest')
            target = directory / (item['role'] + '.tar.gz')
            partial = directory / (item['role'] + '.partial')
            if target.exists() or partial.exists():
                raise RuntimeError('unregistered_frontend_layer_exists')
            request = urllib.request.Request(REGISTRY + '/blobs/' + item['digest'], headers={
                'Authorization': 'Bearer ' + token, 'User-Agent': 'manufacturing-agent-platform/0.1'})
            with opener.open(request, timeout=min(30, max(0.1, deadline - time.monotonic()))) as response, partial.open('xb') as output:
                written = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('frontend_fetch_deadline')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > item['size']:
                        raise RuntimeError('frontend_layer_size_exceeded')
                    output.write(chunk)
            if written != item['size'] or 'sha256:' + sha(partial) != item['digest']:
                raise RuntimeError('frontend_layer_digest_mismatch')
            partial.replace(target)
        result['status'] = 'ready'
        ledger.write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    except BaseException as error:
        result.update(status='failed', error_type=type(error).__name__)
        ledger.write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise
    finally:
        token = None


def extract(directory: Path, target: Path) -> dict:
    """只从核验过的三层提取Next目录；拒绝穿越、白化、设备和目录外链接。"""
    directory, target = directory.resolve(), target.absolute()
    if target.resolve() != target or target.exists():
        raise RuntimeError('frontend_target_must_be_new_and_unredirected')
    receipt = json.loads((directory / 'fetch.json').read_text())
    if receipt.get('status') != 'ready' or receipt.get('manifest_digest') != MANIFEST or receipt.get('revision') != REVISION:
        raise RuntimeError('frontend_fetch_receipt_invalid')
    for item in LAYERS:
        path = directory / (item['role'] + '.tar.gz')
        if path.stat().st_size != item['size'] or 'sha256:' + sha(path) != item['digest']:
            raise RuntimeError('frontend_layer_changed_before_extract')
    target.mkdir(parents=True, exist_ok=False)
    count, expanded = 0, 0
    for item in LAYERS:
        with tarfile.open(directory / (item['role'] + '.tar.gz')) as archive:
            for original in archive:
                name = PurePosixPath(original.name.removeprefix('./'))
                if original.isdir() and not name.parts:
                    continue
                if name.is_absolute() or '..' in name.parts or any(part.startswith('.wh.') for part in name.parts):
                    raise RuntimeError('unsafe_frontend_layer_path')
                if name.parts[:3] != ('app', 'targets', 'next'):
                    if original.isdir() and name.parts in {('app',), ('app', 'targets')}:
                        continue
                    raise RuntimeError('unexpected_frontend_layer_prefix')
                relative = PurePosixPath(*name.parts[3:])
                if str(relative) == '.':
                    continue
                member = copy.copy(original)
                member.name = str(relative)
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise RuntimeError('special_frontend_layer_member')
                if member.islnk():
                    link = PurePosixPath(member.linkname.removeprefix('./'))
                    if link.parts[:3] != ('app', 'targets', 'next'):
                        raise RuntimeError('frontend_hardlink_escape')
                    member.linkname = str(PurePosixPath(*link.parts[3:]))
                count += 1
                expanded += max(0, member.size)
                if count > 100000 or expanded > 800 * 1024 ** 2:
                    raise RuntimeError('frontend_expansion_budget_exceeded')
                archive.extract(member, target, filter='data')
    server = target / 'web/server.js'
    if not server.is_file() or not (target / 'web/.next/BUILD_ID').is_file() or not (target / 'web/.next/static').is_dir():
        raise RuntimeError('frontend_standalone_incomplete')
    requirements = {}
    for package_file in target.rglob('package.json'):
        if package_file.stat().st_size > 1024 * 1024:
            raise RuntimeError('unexpected_package_metadata_size')
        package = json.loads(package_file.read_text())
        if package.get('name') in GNU_PACKAGES:
            version = package.get('version', '')
            if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version):
                raise RuntimeError('native_package_version_invalid')
            requirements[GNU_PACKAGES[package['name']]] = version
    receipt.update(extracted=True, file_count=count, expanded_bytes=expanded, target=str(target),
        next_server_sha256=sha(server), frontend_available=None, gnu_packages_required=requirements)
    (target / 'artifact-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='无Docker的官方Dify前端制品下载与安全提取')
    parser.add_argument('action', choices=['fetch', 'extract'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--target', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'extract' and not args.target:
        parser.error('extract需要--target')
    try:
        result = fetch(args.directory) if args.action == 'fetch' else extract(args.directory, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
