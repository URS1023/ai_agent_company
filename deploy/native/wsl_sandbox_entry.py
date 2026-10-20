"""代码沙箱入口：保留官方seccomp，隔离其硬编码/tmp与/var/sandbox路径。"""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/opt/ai-agent-company')


def sandbox_passwd(original: str) -> str:
    """在私有副本预登记上游UID池，供启动时复制到Python chroot；宿主身份不变。"""
    entries = [line.split(':') for line in original.splitlines() if line and not line.startswith('#')]
    additions = []
    for uid in range(10000, 11000):
        expected = f'sandbox{uid}:x:{uid}:0::/nonexistent:/usr/sbin/nologin'
        matches = [parts for parts in entries if parts[0] == f'sandbox{uid}' or len(parts) > 2 and parts[2] == str(uid)]
        if any(':'.join(parts) != expected for parts in matches):
            raise RuntimeError('sandbox_uid_pool_conflict')
        if not matches:
            additions.append(expected)
    # 上游首次AcquireUID还会追加相同映射；预登记让更早生成的chroot副本也有完整UID。
    return original.rstrip('\n') + '\n' + ''.join(line + '\n' for line in additions)


def main():
    if os.getuid() != 0 or os.environ.get('WSL_DISTRO_NAME') != 'ai-agent-company':
        raise RuntimeError('sandbox_requires_private_wsl_root_launcher')
    if len(sys.argv) != 2 or Path(sys.argv[1]) != ROOT or ROOT.resolve() != ROOT:
        raise RuntimeError('sandbox_runtime_root_mismatch')
    if not os.environ.get('AGENT_WSL_RUNTIME_ID') or not os.environ.get('API_KEY'):
        raise RuntimeError('sandbox_runtime_credentials_missing')
    if os.environ.get('AIC_PRIVATE_MOUNT_READY') != '1':
        env = {**os.environ, 'AIC_PRIVATE_MOUNT_READY': '1'}
        os.execve('/usr/bin/unshare', ['unshare', '--mount', '--propagation', 'private',
            '/usr/bin/python3', str(Path(__file__).resolve()), str(ROOT)], env)
    # private propagation保证挂载仅影响本次沙箱进程树，不覆盖发行版/其他服务的/tmp。
    subprocess.run(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=1777,nosuid,nodev',
        'aic-code-tmp', '/tmp'], check=True, timeout=10)
    work = ROOT / 'data/code'
    # 上游UID池首次执行会向/etc/passwd追加运行身份；只改本次私有挂载副本。
    private_passwd = work / 'uid-pool-passwd'
    if private_passwd.is_symlink():
        raise RuntimeError('sandbox_passwd_state_symlink')
    descriptor = os.open(private_passwd, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(sandbox_passwd(Path('/etc/passwd').read_text()).encode())
    private_passwd.chmod(0o644)
    subprocess.run(['/usr/bin/mount', '--bind', str(private_passwd), '/etc/passwd'], check=True, timeout=10)
    # 官方Go init会删除/重建/var/sandbox；只在私有mount namespace映射本项目数据。
    subprocess.run(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=0755,nosuid,nodev',
        'aic-code-var', '/var'], check=True, timeout=10)
    Path('/var/sandbox').mkdir(mode=0o755)
    native_state = work / 'native-state'
    native_state.mkdir(mode=0o755, exist_ok=True)
    subprocess.run(['/usr/bin/mount', '--bind', str(native_state), '/var/sandbox'], check=True, timeout=10)
    (work / 'conf').mkdir(exist_ok=True)
    (work / 'dependencies').mkdir(exist_ok=True)
    # API_KEY/PYTHON_PATH/NODEJS_PATH由官方配置器读取环境；配置文件不落真实凭据。
    configuration = {'app': {'port': 18194, 'debug': False, 'key': 'runtime-environment-required'},
        'max_workers': 4, 'max_requests': 50, 'worker_timeout': 15,
        'enable_network': True, 'enable_preload': False, 'log_path': str(work / 'logs'),
        'allowed_syscalls': [], 'proxy': {'socks5': '', 'http': '', 'https': ''}}
    # JSON是YAML子集，保留上游yaml解析器与完整seccomp默认规则。
    (work / 'conf/config.yaml').write_text(json.dumps(configuration))
    (work / 'dependencies/python-requirements.txt').touch(exist_ok=True)
    os.chdir(work)
    os.execve(str(ROOT / 'bin/code-sandbox'), [str(ROOT / 'bin/code-sandbox')], dict(os.environ))


if __name__ == '__main__':
    main()
