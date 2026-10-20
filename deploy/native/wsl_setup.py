"""在独立WSL ext4中复用锁定源码和原生工具；各阶段显式执行，不启动服务。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

try:
    import pwd
except ImportError:
    pwd = None

ROOT = Path('/opt/ai-agent-company')
USER = 'agentapp'
DISTRO = 'ai-agent-company'
NODE = '24.21.0'
GO = '1.26.10'
PNPM = '12.10.1'
QDRANT = '1.19.2'
QDRANT_SHA = '34a788a09a4cb278b5c6d2af96d9a6db30c8c7e88ae3550455f7a4d425bb8d4b'
# 官方Git智能HTTP refs/tags/0.2.15直接指向此commit；避免部署时再依赖限流REST。
SANDBOX_COMMIT = '5631afef06ec88f80c28129aec7fd22a30006b14'
SANDBOX_GO = '1.24.13'
SANDBOX_GO_SHA = '1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730'
SANDBOX_NODE = '20.11.1'
SANDBOX_NODE_SHA = 'd8dab549b09672b03356aa2257699f3de3b58c96e74eb26a8b495fbdc9cf6fbe'
PLUGIN_ZIP_SHA = 'a3ae9b9e85e6592bd2cdd7f49ee3c5172b7bedb94758f245b7aca6378cc4bf58'
MAX_ARCHIVE = 500 * 1024 * 1024
SKIP = {'.git', 'node_modules', '.venv', '__pycache__', '.next', '.turbo', '.cache', '.pytest_cache'}


def check_linux() -> None:
    if os.name != 'posix' or os.environ.get('WSL_DISTRO_NAME') != DISTRO or os.getuid() != 0:
        raise RuntimeError('setup_requires_owned_wsl_root')
    receipt = json.loads((ROOT / 'bootstrap.json').read_text())
    if receipt.get('root') != str(ROOT) or receipt.get('distro') != DISTRO or receipt.get('status') != 'ready':
        raise RuntimeError('bootstrap_not_ready')
    if ROOT.resolve() != ROOT or not (ROOT / 'tooling/bin/uv').is_file():
        raise RuntimeError('runtime_root_or_uv_invalid')


def inside(path: Path) -> Path:
    path = path.resolve()
    if path == ROOT or not path.is_relative_to(ROOT):
        raise ValueError('runtime_path_escape')
    return path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def own_tree(path: Path, check=lambda: None) -> None:
    account = pwd.getpwnam(USER)
    for current, directories, files in os.walk(path):
        check()
        os.chown(current, account.pw_uid, account.pw_gid)
        for name in files:
            child = Path(current) / name
            if not child.is_symlink():
                os.chown(child, account.pw_uid, account.pw_gid)


def environment(extra=None) -> dict:
    return {'HOME': '/home/agentapp', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'PATH': f'{ROOT}/bin:{ROOT}/tooling/node/bin:{ROOT}/tooling/go/bin:{ROOT}/tooling/bin:/usr/local/bin:/usr/bin:/bin',
        'UV_CACHE_DIR': str(ROOT / 'cache/uv'), 'UV_PYTHON_INSTALL_DIR': str(ROOT / 'tooling/python'),
        'UV_HTTP_RETRIES': '0', 'PIP_RETRIES': '0', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'GOTOOLCHAIN': 'local', 'GOPATH': str(ROOT / 'cache/gopath'), 'GOMODCACHE': str(ROOT / 'cache/gomod'),
        'GOCACHE': str(ROOT / 'cache/go-build'), 'GOMAXPROCS': '2',
        'npm_config_cache': str(ROOT / 'cache/npm'), 'COREPACK_HOME': str(ROOT / 'cache/corepack'),
        'NEXT_TELEMETRY_DISABLED': '1', 'PYTHON_DOTENV_DISABLED': '1', **(extra or {})}


def command(args: list[str], *, cwd: Path = ROOT, extra=None, seconds=1800) -> None:
    """仅编译/安装进程；无shell拼接、无旧环境继承，失败不重试。"""
    account = pwd.getpwnam(USER)
    subprocess.run(args, cwd=cwd, env=environment(extra), user=account.pw_uid,
        group=account.pw_gid, extra_groups=[], check=True, timeout=seconds)


def metadata(url: str) -> dict | list:
    request = urllib.request.Request(url, headers={'User-Agent': 'manufacturing-agent-platform/0.1'})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(12 * 1024 * 1024 + 1)
    if len(body) > 12 * 1024 * 1024:
        raise RuntimeError('metadata_budget_exceeded')
    return json.loads(body)


def download(url: str, name: str, expected_sha: str) -> Path:
    """只接受官方HTTPS来源和已取得的SHA；保留失败partial，不自动续拉。"""
    if not expected_sha or len(expected_sha) != 64:
        raise ValueError('missing_asset_sha256')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'nodejs.org', 'go.dev', 'dl.google.com',
            'github.com', 'codeload.github.com', 'files.pythonhosted.org'}:
        raise ValueError('unapproved_asset_origin')
    target = inside(ROOT / 'downloads' / name)
    if target.is_file():
        if digest(target) != expected_sha:
            raise RuntimeError('cached_asset_digest_mismatch')
        return target
    partial = target.with_suffix(target.suffix + '.partial')
    if partial.exists():
        raise RuntimeError('previous_partial_requires_review')
    target.parent.mkdir(exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'manufacturing-agent-platform/0.1'})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open('xb') as output:
        length = 0
        while chunk := response.read(1024 * 1024):
            length += len(chunk)
            if length > MAX_ARCHIVE:
                raise RuntimeError('asset_size_budget_exceeded')
            output.write(chunk)
    if digest(partial) != expected_sha:
        raise RuntimeError('downloaded_asset_digest_mismatch')
    partial.replace(target)
    return target


def extract_tar(archive: Path, target: Path) -> None:
    """Python data过滤器拒绝绝对路径、越界链接和设备；只解到新目录。"""
    inside(target)
    target.mkdir(exist_ok=False)
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        if len(members) > 100000 or sum(max(0, item.size) for item in members) > 3 * 1024 ** 3:
            raise RuntimeError('archive_expansion_budget_exceeded')
        handle.extractall(target, members=members, filter='data')


def extract_plugin(archive: Path, target: Path) -> None:
    if digest(archive) != PLUGIN_ZIP_SHA:
        raise RuntimeError('plugin_source_digest_mismatch')
    inside(target)
    target.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as handle:
        members = handle.infolist()
        if len(members) > 20000 or sum(item.file_size for item in members) > 500 * 1024 ** 2:
            raise RuntimeError('plugin_archive_budget_exceeded')
        for item in members:
            path = Path(item.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('unsafe_plugin_archive')
        handle.extractall(target)


def copy_source(source: Path, destination: Path, lock: dict) -> dict:
    """复制现有锁定源码，不复制Git、依赖、旧.env、存储和失败构建缓存。"""
    if destination.exists():
        raise RuntimeError('source_destination_already_exists')
    verified = []
    for asset in lock['source_assets']:
        if asset['project'] == 'dify':
            path = source / asset['path']
            if path.resolve() != path.absolute() or digest(path) != asset['sha256']:
                raise RuntimeError('dify_source_lock_mismatch')
            verified.append({'path': asset['path'], 'sha256': asset['sha256']})
    def ignore(current, names):
        from wsl_sources import excluded_path
        excluded = {name for name in names if excluded_path('dify/' + (Path(current) / name).relative_to(source).as_posix())}
        for name in names:
            if name not in excluded and (Path(current) / name).is_symlink():
                raise RuntimeError('source_symlink_not_copied')
        return excluded
    shutil.copytree(source, destination, ignore=ignore)
    return {'version': lock['dify']['version'], 'filter_revision': 2, 'verified_assets': verified}


def prepare_sources(source: Path, plugin_zip: Path, lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if lock['dify']['version'] != '1.17.1':
        raise RuntimeError('dify_version_mismatch')
    sources = ROOT / 'sources'
    sources.mkdir(exist_ok=True)
    result = copy_source(source, sources / 'dify', lock)
    extract_plugin(plugin_zip, sources / 'plugin-archive')
    shutil.move(sources / 'plugin-archive/dify-plugin-daemon-0.6.10', sources / 'plugin')
    own_tree(sources)
    write_json(ROOT / 'source-receipt.json', {**result, 'plugin_source_sha256': PLUGIN_ZIP_SHA})
    return result


def recover_sources(archive: Path, receipt: Path, *, verify_only=False, reviewed_extras=None, reviewed_extras_sha256=None) -> dict:
    """归档恢复另记阶段，不清除或重写旧sources中断记录。"""
    from wsl_sources import resume_archive
    deadline = time.monotonic() + 180

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('source_resume_budget_exceeded')

    result = resume_archive(archive, receipt, ROOT / 'sources', seconds=180, apply=not verify_only,
        reviewed_extras=reviewed_extras, reviewed_extras_sha256=reviewed_extras_sha256)
    if not verify_only:
        own_tree(ROOT / 'sources', check)
        check()
        write_json(ROOT / 'source-receipt.json', result)
    return result


@contextmanager
def operation_lock():
    """安装阶段使用独立跨进程锁，避免并行编译或源码恢复互相覆盖。"""
    import fcntl
    with (ROOT / 'setup-operation.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def setup_tools() -> dict:
    for folder in ['downloads', 'bin', 'cache', 'venvs']:
        (ROOT / folder).mkdir(exist_ok=True)
    for folder in ['bin', 'cache', 'venvs']:
        own_tree(ROOT / folder)
    command([str(ROOT / 'tooling/bin/python'), '-m', 'pip', 'install', '--no-input', '--retries', '0',
        'cryptography==50.0.2', 'psutil==7.2.2'], seconds=600)
    node_name = f'node-v{NODE}-linux-x64.tar.xz'
    with urllib.request.urlopen(f'https://nodejs.org/dist/v{NODE}/SHASUMS256.txt', timeout=30) as response:
        checksums = response.read(1024 * 1024).decode()
    node_sha = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == node_name)
    node_archive = download(f'https://nodejs.org/dist/v{NODE}/{node_name}', node_name, node_sha)
    extract_tar(node_archive, ROOT / 'tooling/node-extract')
    shutil.move(ROOT / f'tooling/node-extract/node-v{NODE}-linux-x64', ROOT / 'tooling/node')
    releases = metadata('https://go.dev/dl/?mode=json&include=all')
    go_name = f'go{GO}.linux-amd64.tar.gz'
    go_file = next(item for release in releases for item in release['files'] if item['filename'] == go_name)
    go_archive = download('https://go.dev/dl/' + go_name, go_name, go_file['sha256'])
    extract_tar(go_archive, ROOT / 'tooling/go-extract')
    shutil.move(ROOT / 'tooling/go-extract/go', ROOT / 'tooling/go')
    own_tree(ROOT / 'tooling')
    command([str(ROOT / 'tooling/node/bin/npm'), 'install', '--prefix', str(ROOT / 'tooling/pnpm'),
        '--no-audit', '--no-fund', '--fetch-retries=0', f'pnpm@{PNPM}'], seconds=600)
    pnpm = ROOT / 'bin/pnpm'
    pnpm.symlink_to(ROOT / 'tooling/pnpm/node_modules/.bin/pnpm')
    result = {'node': NODE, 'node_sha256': node_sha, 'go': GO, 'go_sha256': go_file['sha256'],
        'pnpm': PNPM, 'uv': '0.13.4', 'python': '3.13.14'}
    write_json(ROOT / 'toolchain-receipt.json', result)
    return result


def python_path() -> Path:
    values = list((ROOT / 'tooling/python').glob('cpython-3.13.14-linux-*/bin/python3.13'))
    if len(values) != 1:
        raise RuntimeError('exact_python313_runtime_not_found')
    return values[0]


def setup_python() -> dict:
    # 安装前只修锁定Agent包的预算传递；未知源码拒绝，不改变锁文件或原生能力。
    agent_patch = patch_agent_sources()
    uv = str(ROOT / 'tooling/bin/uv')
    source = ROOT / 'sources/dify'
    for name, folder, extras in [('api', source / 'api', []), ('agent', source / 'dify-agent', ['--extra', 'server'])]:
        command([uv, 'sync', '--frozen', '--no-dev', '--no-editable', '--python', str(python_path()), *extras],
            cwd=folder, extra={'UV_PROJECT_ENVIRONMENT': str(ROOT / 'venvs' / name)}, seconds=3600)
    source_receipt = json.loads((ROOT / 'source-receipt.json').read_text())
    return {'api': 'locked', 'agent_server_extra': 'locked', 'source_filter_revision': source_receipt.get('filter_revision'),
        'agent_request_limit_patch': agent_patch}


def patch_agent_sources() -> dict:
    """独立准备阶段只修改源码；已安装非editable包必须另行显式核验和应用，不伪称已经生效。"""
    from wsl_agent_patch import apply_patch
    return apply_patch(ROOT / 'sources/dify/dify-agent/src/dify_agent')


def repair_python_package() -> dict:
    """仅重装因旧过滤规则缺文件的本地dify-agent包，依赖仍遵守原锁；不导入服务。"""
    uv = str(ROOT / 'tooling/bin/uv')
    source = ROOT / 'sources/dify'
    expected = list((source / 'dify-agent/src/dify_agent/storage').glob('*.py'))
    if not expected:
        raise RuntimeError('restored_agent_storage_source_missing')
    checked = {}
    for name, folder, extras in [('api', source / 'api', []), ('agent', source / 'dify-agent', ['--extra', 'server'])]:
        command([uv, 'sync', '--frozen', '--no-dev', '--no-editable', '--python', str(python_path()),
            '--reinstall-package', 'dify-agent', '--refresh-package', 'dify-agent', *extras], cwd=folder,
            extra={'UV_PROJECT_ENVIRONMENT': str(ROOT / 'venvs' / name)}, seconds=600)
        package = ROOT / 'venvs' / name / 'lib/python3.13/site-packages/dify_agent/storage'
        for item in expected:
            if not (package / item.name).is_file() or digest(package / item.name) != digest(item):
                raise RuntimeError('installed_agent_storage_digest_mismatch')
        checked[name] = {item.name: digest(item) for item in expected}
    result = {'reinstalled_package': 'dify-agent', 'lock_re_resolved': False, 'installed_file_sha256': checked,
        'source_filter_revision': 2, 'database_requests': 0, 'model_requests': 0}
    write_json(ROOT / 'python-package-repair.json', result)
    return result


def setup_binaries() -> dict:
    deadline = time.monotonic() + 3600
    go = str(ROOT / 'tooling/go/bin/go')
    source = ROOT / 'sources/dify/dify-agent-runtime'
    for binary, package in [('shellctl', 'shellctl'), ('shellctl-sanitize-pty', 'sanitize-pty'),
            ('shellctl-runner-exit', 'runner-exit'), ('shellctl-runner', 'runner'), ('dify-agent', 'dify-agent-cli')]:
        command([go, 'build', '-p=1', '-o', str(ROOT / 'bin' / binary), './cmd/' + package],
            cwd=source, extra={'CGO_ENABLED': '0'}, seconds=remaining_seconds(deadline, 1800))
    command([go, 'build', '-p=1', '-o', str(ROOT / 'bin/plugin-daemon'), './cmd/server'],
        cwd=ROOT / 'sources/plugin', extra={'CGO_ENABLED': '1'}, seconds=remaining_seconds(deadline, 1800))
    build_plugin_cli()
    return verify_binaries()


def verify_binaries(*, include_plugin=True) -> dict:
    """读取已有Go构建元信息和ELF摘要；不再次编译或执行目标二进制。"""
    targets = [(name, 'github.com/langgenius/dify/dify-agent-runtime/cmd/' + package)
        for name, package in [('shellctl', 'shellctl'), ('shellctl-sanitize-pty', 'sanitize-pty'),
            ('shellctl-runner-exit', 'runner-exit'), ('shellctl-runner', 'runner'), ('dify-agent', 'dify-agent-cli')]]
    if include_plugin:
        targets.append(('plugin-daemon', 'github.com/langgenius/dify-plugin-daemon/cmd/server'))
    result = {'shellctl_source': 'dify-1.17.1', 'plugin_source': '0.6.10',
        'plugin_local_image_equivalence_verified': False, 'compilation_performed': False, 'binaries': {}}
    for name, package in targets:
        path = inside(ROOT / 'bin' / name)
        with path.open('rb') as handle:
            header = handle.read(20)
        if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
            raise RuntimeError('native_binary_not_linux_amd64:' + name)
        probe = subprocess.run([str(ROOT / 'tooling/go/bin/go'), 'version', '-m', str(path)],
            capture_output=True, text=True, env=environment(), check=True, timeout=10)
        if '\tpath\t' + package + '\n' not in probe.stdout or '\tbuild\tGOOS=linux\n' not in probe.stdout or '\tbuild\tGOARCH=amd64\n' not in probe.stdout:
            raise RuntimeError('native_binary_build_identity_mismatch:' + name)
        result['binaries'][name] = {'sha256': digest(path), 'size': path.stat().st_size,
            'package': package, 'format': 'ELF64-linux-amd64', 'functional_verified': False}
    write_json(ROOT / ('go-binaries-receipt.json' if include_plugin else 'go-agent-binaries-receipt.json'), result)
    return result


def build_plugin() -> dict:
    """独立编译已补齐来源的插件server，不重编五个已经完成的Agent目标。"""
    source = ROOT / 'sources/plugin'
    if not (source / 'internal/storage/baidubos/bos.go').is_file():
        raise RuntimeError('plugin_storage_source_missing')
    command([str(ROOT / 'tooling/go/bin/go'), 'build', '-p=1', '-o', str(ROOT / 'bin/plugin-daemon'), './cmd/server'],
        cwd=source, extra={'CGO_ENABLED': '1'}, seconds=1800)
    return verify_binaries()


def verify_plugin_cli(root: Path | None = None) -> dict:
    """只读核官方迁移CLI的来源收据和ELF摘要，不执行程序或连接数据库。"""
    root = root or ROOT
    path = root / 'bin/plugin-commandline'
    receipt_path = root / 'plugin-cli-receipt.json'
    if any(pathname.absolute() != pathname.resolve() for pathname in (path, receipt_path)):
        raise RuntimeError('plugin_cli_path_redirected')
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('version'), receipt.get('package'), receipt.get('source_sha256')) != (
            '0.6.10', './cmd/commandline/', PLUGIN_ZIP_SHA):
        raise RuntimeError('plugin_cli_source_receipt_changed')
    with path.open('rb') as handle:
        header = handle.read(20)
    if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
        raise RuntimeError('plugin_cli_not_linux_amd64')
    if digest(path) != receipt.get('binary_sha256'):
        raise RuntimeError('plugin_cli_binary_digest_changed')
    return receipt


def build_plugin_cli() -> dict:
    """独立构建上游迁移入口；已有受信制品只验证，不覆盖或重编译。"""
    target = inside(ROOT / 'bin/plugin-commandline')
    receipt_path = ROOT / 'plugin-cli-receipt.json'
    if target.exists() or receipt_path.exists():
        return {**verify_plugin_cli(), 'reused': True}
    source = ROOT / 'sources/plugin'
    if not (source / 'cmd/commandline/migrate.go').is_file():
        raise RuntimeError('plugin_cli_source_missing')
    with tempfile.TemporaryDirectory(prefix='plugin-cli-build-', dir=ROOT / 'cache') as temporary:
        folder = Path(temporary)
        identity = pwd.getpwnam(USER)
        os.chown(folder, identity.pw_uid, identity.pw_gid)
        candidate = folder / 'plugin-commandline'
        command([str(ROOT / 'tooling/go/bin/go'), 'build', '-p=1', '-ldflags=-X=main.VersionX=0.6.10',
            '-o', str(candidate), './cmd/commandline/'], cwd=source,
            extra={'CGO_ENABLED': '1', 'GOPROXY': 'https://proxy.golang.org', 'GOSUMDB': 'sum.golang.org'}, seconds=120)
        probe = subprocess.run([str(ROOT / 'tooling/go/bin/go'), 'version', '-m', str(candidate)],
            env=environment(), check=True, capture_output=True, text=True, timeout=10)
        if '\tpath\tgithub.com/langgenius/dify-plugin-daemon/cmd/commandline\n' not in probe.stdout:
            raise RuntimeError('plugin_cli_build_package_changed')
        candidate.chmod(0o755)
        os.chown(candidate, 0, 0)
        candidate.replace(target)
    write_json(receipt_path, {'version': '0.6.10', 'package': './cmd/commandline/',
        'binary_sha256': digest(target), 'source_sha256': PLUGIN_ZIP_SHA,
        'at': datetime.now(timezone.utc).isoformat(), 'functional_verified': False})
    return verify_plugin_cli()


def setup_qdrant() -> dict:
    """下载单独锁定的官方ELF；摘要来自官方发布资产，不依赖匿名REST限额。"""
    url = f'https://github.com/qdrant/qdrant/releases/download/v{QDRANT}/qdrant-x86_64-unknown-linux-gnu.tar.gz'
    archive = download(url, f'qdrant-{QDRANT}-linux.tar.gz', QDRANT_SHA)
    target = inside(ROOT / 'bin/qdrant')
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        files = [item for item in members if item.isfile() and Path(item.name).name == 'qdrant']
        if len(files) != 1 or len(members) > 100 or files[0].size > 200 * 1024 ** 2:
            raise RuntimeError('qdrant_release_archive_invalid')
        content = handle.extractfile(files[0]).read()
    binary_sha = hashlib.sha256(content).hexdigest()
    if content[:6] != b'\x7fELF\x02\x01' or content[18:20] != b'\x3e\x00':
        raise RuntimeError('qdrant_release_not_linux_amd64')
    if target.exists():
        if digest(target) != binary_sha:
            raise RuntimeError('existing_qdrant_not_overwritten')
    else:
        with target.open('xb') as output:
            output.write(content)
        target.chmod(0o755)
    own_tree(ROOT / 'bin')
    result = {'version': QDRANT, 'archive_sha256': QDRANT_SHA, 'binary_sha256': binary_sha,
        'source': url, 'digest_source': f'https://github.com/qdrant/qdrant/releases/expanded_assets/v{QDRANT}',
        'functional_verified': False}
    write_json(ROOT / 'qdrant-receipt.json', result)
    return result


def verify_sandbox_toolchain() -> dict:
    """只核沙箱专用Go来源及摘要，不覆盖插件/API使用的1.26工具链。"""
    folder = ROOT / ('tooling/go-sandbox-' + SANDBOX_GO)
    if folder.resolve() != folder.absolute():
        raise RuntimeError('sandbox_go_path_redirected')
    receipt = json.loads((ROOT / 'sandbox-toolchain-receipt.json').read_text())
    if (receipt.get('version'), receipt.get('root'), receipt.get('archive_sha256')) != (
            SANDBOX_GO, str(folder), SANDBOX_GO_SHA):
        raise RuntimeError('sandbox_go_receipt_changed')
    if (folder / 'VERSION').read_text().splitlines()[0] != 'go' + SANDBOX_GO:
        raise RuntimeError('sandbox_go_version_changed')
    binary = folder / 'bin/go'
    if binary.resolve() != binary.absolute() or digest(binary) != receipt.get('binary_sha256'):
        raise RuntimeError('sandbox_go_binary_changed')
    return receipt


def setup_sandbox_toolchain() -> dict:
    """复用已校验缓存或下载官方归档；只安装独立目录，失败不接管不完整安装。"""
    folder = ROOT / ('tooling/go-sandbox-' + SANDBOX_GO)
    receipt_path = ROOT / 'sandbox-toolchain-receipt.json'
    if folder.exists() or receipt_path.exists():
        return {**verify_sandbox_toolchain(), 'reused': True}
    filename = 'go' + SANDBOX_GO + '.linux-amd64.tar.gz'
    url = 'https://go.dev/dl/' + filename
    archive = download(url, filename, SANDBOX_GO_SHA)
    staging = ROOT / ('tooling/go-sandbox-' + SANDBOX_GO + '-extract')
    extract_tar(archive, staging)
    extracted = staging / 'go'
    if (extracted / 'VERSION').read_text().splitlines()[0] != 'go' + SANDBOX_GO:
        raise RuntimeError('sandbox_go_archive_version_changed')
    shutil.move(extracted, folder)
    write_json(receipt_path, {'version': SANDBOX_GO, 'root': str(folder), 'archive_sha256': SANDBOX_GO_SHA,
        'binary_sha256': digest(folder / 'bin/go'), 'source_url': url,
        'digest_source': 'https://go.dev/dl/?mode=json&include=all',
        'at': datetime.now(timezone.utc).isoformat()})
    return verify_sandbox_toolchain()


def sandbox_go_binary() -> str:
    """沙箱构建只接受官方同版本Go；其他构建继续用全局GO=1.26.10。"""
    receipt = verify_sandbox_toolchain()
    return str(Path(receipt['root']) / 'bin/go')


def verify_sandbox_node() -> dict:
    """核验上游沙箱专用Node；不与Next所用Node24混用。"""
    folder = ROOT / ('tooling/node-sandbox-' + SANDBOX_NODE)
    receipt = json.loads((ROOT / 'sandbox-node-receipt.json').read_text())
    if (receipt.get('version'), receipt.get('root'), receipt.get('archive_sha256')) != (
            SANDBOX_NODE, str(folder), SANDBOX_NODE_SHA):
        raise RuntimeError('sandbox_node_receipt_changed')
    binary = folder / 'bin/node'
    if folder.resolve() != folder.absolute() or binary.resolve() != binary.absolute():
        raise RuntimeError('sandbox_node_path_redirected')
    if digest(binary) != receipt.get('binary_sha256'):
        raise RuntimeError('sandbox_node_binary_changed')
    return receipt


def setup_sandbox_node() -> dict:
    """复用官方0.2.15模板的Node版本，独立安装且保留归档校验记录。"""
    folder = ROOT / ('tooling/node-sandbox-' + SANDBOX_NODE)
    if folder.exists() or (ROOT / 'sandbox-node-receipt.json').exists():
        return {**verify_sandbox_node(), 'reused': True}
    name = 'node-v' + SANDBOX_NODE + '-linux-x64.tar.xz'
    archive = download('https://nodejs.org/dist/v' + SANDBOX_NODE + '/' + name, name, SANDBOX_NODE_SHA)
    staging = ROOT / ('tooling/node-sandbox-' + SANDBOX_NODE + '-extract')
    extract_tar(archive, staging)
    shutil.move(staging / ('node-v' + SANDBOX_NODE + '-linux-x64'), folder)
    binary = folder / 'bin/node'
    version = subprocess.run([str(binary), '--version'], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
    if version != 'v' + SANDBOX_NODE:
        raise RuntimeError('sandbox_node_archive_version_changed')
    write_json(ROOT / 'sandbox-node-receipt.json', {
        'version': SANDBOX_NODE, 'root': str(folder), 'archive_sha256': SANDBOX_NODE_SHA,
        'binary_sha256': digest(binary),
        'source_reference': 'https://github.com/langgenius/dify-sandbox/blob/' + SANDBOX_COMMIT
                            + '/docker/templates/production.dockerfile',
    })
    return verify_sandbox_node()


def setup_sandbox() -> dict:
    """复用官方0.2.15源和四个原生构建目标；不运行上游删除全局/tmp的build脚本。"""
    deadline = time.monotonic() + 3600
    commit = SANDBOX_COMMIT
    archive = ROOT / 'downloads' / ('dify-sandbox-' + commit + '.tar.gz')
    if archive.exists():
        raise RuntimeError('unregistered_sandbox_source_archive')
    url = 'https://codeload.github.com/langgenius/dify-sandbox/tar.gz/' + commit
    # 官方源以不可变Git commit固定；首取测量压缩包SHA后保留，后续不信任可变tag或缓存。
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'manufacturing-agent-platform/0.1'}), timeout=120) as response, archive.open('xb') as output:
        length = 0
        while chunk := response.read(1024 * 1024):
            length += len(chunk)
            if length > 50 * 1024 ** 2:
                raise RuntimeError('sandbox_source_budget_exceeded')
            output.write(chunk)
    temporary = ROOT / 'sources/sandbox-extract'
    extract_tar(archive, temporary)
    shutil.move(temporary / ('dify-sandbox-' + commit), ROOT / 'sources/code-sandbox')
    source = ROOT / 'sources/code-sandbox'
    own_tree(source)
    from wsl_sandbox_patch import apply_python_stat_patch
    source_patches = [apply_python_stat_patch(source)]
    toolchain = setup_sandbox_toolchain()
    setup_sandbox_node()
    go = sandbox_go_binary()
    for output, package, options in [
            ('internal/core/runner/python/python.so', './cmd/lib/python', ['-buildmode=c-shared']),
            ('internal/core/runner/nodejs/nodejs.so', './cmd/lib/nodejs', ['-buildmode=c-shared']),
            (str(ROOT / 'bin/code-sandbox'), './cmd/server', []),
            (str(ROOT / 'bin/code-sandbox-env'), './cmd/dependencies', [])]:
        command([go, 'build', '-p=1', *options, '-ldflags=-s -w', '-o', output, package], cwd=source,
            extra={'CGO_ENABLED': '1', 'GOOS': 'linux', 'GOARCH': 'amd64'}, seconds=remaining_seconds(deadline, 1800))
    result = {'version': '0.2.15', 'source_commit': commit, 'source_sha256': digest(archive),
        'source_patches': source_patches, 'go_version': toolchain['version'],
        'go_binary_sha256': toolchain['binary_sha256'],
        'binary_sha256': digest(ROOT / 'bin/code-sandbox'),
        'seccomp_preserved': False, 'seccomp_enforced': True,
        'seccomp_default_action_preserved': True, 'seccomp_network_policy_preserved': True,
        'allowed_syscalls_added': [{'architecture': 'linux-amd64', 'number': 4, 'name': 'stat'}],
        'source_reference': 'https://github.com/langgenius/dify-sandbox/tree/' + commit,
        'tag_reference': 'https://github.com/langgenius/dify-sandbox/tree/0.2.15',
        'functional_verified': False}
    write_json(ROOT / 'sandbox-receipt.json', result)
    return result


def remaining_seconds(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('setup_stage_budget_exceeded')
    return min(maximum, remaining)


def interrupted(signum, frame):
    """外层正常超时先抛可记录异常，随后停止同一命令组，不吞掉中断。"""
    raise InterruptedError('setup_termination_requested')


def setup_web() -> dict:
    """优先使用官方Next制品，按同版本官方预编译包适配glibc，不重新构建页面。"""
    artifact = ROOT / 'web'
    receipt_path = artifact / 'artifact-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('revision') != '8387590ace4a094de812b7847fc6a4c3a27cd52b' or not receipt.get('extracted'):
        raise RuntimeError('official_next_artifact_not_prepared')
    requirements = receipt.get('gnu_packages_required', {})
    if requirements:
        compat = artifact / 'native-gnu'
        compat.mkdir(exist_ok=False)
        (compat / 'package.json').write_text('{"private":true}', encoding='utf-8')
        own_tree(artifact)
        command([str(ROOT / 'tooling/node/bin/npm'), 'install', '--prefix', str(compat),
            '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--fetch-retries=0',
            *[name + '@' + version for name, version in requirements.items()]], seconds=600)
        for name in requirements:
            target = artifact / 'node_modules' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise RuntimeError('existing_gnu_native_package_not_overwritten')
            target.symlink_to(compat / 'node_modules' / name, target_is_directory=True)
    own_tree(artifact)
    receipt['gnu_packages_installed'] = requirements
    receipt['source_equivalence_verified'] = False
    write_json(receipt_path, receipt)
    return {'version': '1.17.1', 'official_revision': receipt['revision'], 'prebuilt': True,
        'gnu_packages': requirements, 'source_equivalence_verified': False, 'frontend_available': None}


def build_web_from_source() -> dict:
    """显式回退才复用上游构建命令；默认web阶段不进行大型构建。"""
    source = ROOT / 'sources/dify'
    command([str(ROOT / 'bin/pnpm'), 'install', '--frozen-lockfile', '--ignore-scripts'], cwd=source, seconds=1800)
    command([str(ROOT / 'bin/pnpm'), 'build'], cwd=source / 'web', seconds=1800)
    command([str(ROOT / 'bin/pnpm'), 'build:vinext'], cwd=source / 'web', seconds=1800)
    server = source / 'web/.next/standalone/web/server.js'
    if not server.is_file() or not (source / 'web/.next/BUILD_ID').is_file():
        raise RuntimeError('native_web_build_not_complete')
    # 沿上游Dockerfile补齐standalone省略的public/static，不重写前端代码。
    for original, target in [(source / 'web/public', server.parent / 'public'),
            (source / 'web/.next/static', server.parent / '.next/static')]:
        shutil.copytree(original, target, dirs_exist_ok=True)
    return {'version': '1.17.1', 'next_server_sha256': digest(server), 'vinext_available':
        (source / 'web/dist/standalone/server.js').is_file()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='专用WSL原生依赖分阶段准备，不启动服务')
    parser.add_argument('stage', choices=['sources', 'verify-resume', 'sources-storage-repair', 'tools', 'python',
        'python-package-repair', 'agent-request-limit-patch', 'binaries', 'verify-binaries', 'verify-agent-binaries', 'plugin-build', 'plugin-cli-build', 'qdrant', 'sandbox', 'web', 'web-build'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--plugin-archive', type=Path)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--archive-receipt', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--reviewed-extras', type=Path)
    parser.add_argument('--reviewed-extras-sha256')
    parser.add_argument('--resume-reviewed-failure', action='store_true')
    args = parser.parse_args(argv)
    check_linux()
    signal.signal(signal.SIGTERM, interrupted)
    with operation_lock():
        return execute_stage(args)


def execute_stage(args) -> int:
    """各阶段先登记，失败不重放；只读核对不消耗恢复阶段。"""
    source_recovery = args.stage in {'verify-resume', 'sources-storage-repair'}
    review = {'reviewed_extras': getattr(args, 'reviewed_extras', None),
        'reviewed_extras_sha256': getattr(args, 'reviewed_extras_sha256', None)}
    if args.verify_only and not source_recovery:
        raise ValueError('verify_only_requires_verify_resume')
    if source_recovery and not all([args.archive, args.archive_receipt]):
        raise ValueError('archive_arguments_required')
    if args.verify_only:
        result = recover_sources(args.archive, args.archive_receipt, verify_only=True, **review)
        print(json.dumps({'stage': args.stage, 'status': 'verified_only', 'result': result}))
        return 0
    path = ROOT / 'setup-stages.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    previous = state.get(args.stage, {})
    reviewed_resume = getattr(args, 'resume_reviewed_failure', False)
    if reviewed_resume and (not source_recovery or not all(review.values()) or previous.get('status') != 'failed'):
        raise RuntimeError('reviewed_resume_requires_failed_source_stage_and_exact_audit')
    if previous.get('status') in {'attempted', 'failed'} and not reviewed_resume:
        raise RuntimeError('previous_failed_stage_requires_review')
    if state.get(args.stage, {}).get('status') == 'ready':
        print(json.dumps({'stage': args.stage, 'status': 'already_prepared'}))
        return 0
    history = previous.get('history', []) + ([{key: value for key, value in previous.items() if key != 'history'}] if reviewed_resume else [])
    state[args.stage] = {'status': 'attempted', 'at': datetime.now(timezone.utc).isoformat(), 'history': history}
    if reviewed_resume:
        state[args.stage]['reviewed_extras_sha256'] = review['reviewed_extras_sha256']
    write_json(path, state)
    try:
        if args.stage == 'sources':
            if not all([args.source, args.plugin_archive, args.lock]):
                raise ValueError('source_arguments_required')
            result = prepare_sources(args.source, args.plugin_archive, args.lock)
        elif source_recovery:
            result = recover_sources(args.archive, args.archive_receipt, **review)
        else:
            result = {'tools': setup_tools, 'python': setup_python, 'binaries': setup_binaries,
                'verify-binaries': verify_binaries, 'qdrant': setup_qdrant,
                'verify-agent-binaries': lambda: verify_binaries(include_plugin=False), 'plugin-build': build_plugin,
                'plugin-cli-build': build_plugin_cli,
                'python-package-repair': repair_python_package,
                'agent-request-limit-patch': patch_agent_sources,
                'sandbox': setup_sandbox, 'web': setup_web, 'web-build': build_web_from_source}[args.stage]()
        state[args.stage].update(status='ready', result=result)
        write_json(path, state)
        print(json.dumps({'stage': args.stage, 'status': 'ready', 'result': result}, ensure_ascii=False))
        return 0
    except BaseException as error:
        state[args.stage].update(status='failed', error_type=type(error).__name__)
        write_json(path, state)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
