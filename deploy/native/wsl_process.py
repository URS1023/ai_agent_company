"""原生进程的最小exec包装器：先加入独占cgroup/netns，再降权执行上游入口。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess

import wsl_cgroups

ROOT = Path('/opt/ai-agent-company')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', required=True)
    parser.add_argument('--launch', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--namespace')
    parser.add_argument('--supervise', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if os.name != 'posix' or os.getuid() != 0 or os.environ.get('WSL_DISTRO_NAME') != 'ai-agent-company':
        raise RuntimeError('private_exec_requires_owned_wsl_root')
    data = json.loads((ROOT / 'runtime.json').read_text())
    if data.get('root') != str(ROOT) or data['id'] != os.environ.get('AGENT_WSL_RUNTIME_ID'):
        raise RuntimeError('private_exec_runtime_mismatch')
    info = json.loads((ROOT / 'process-launches' / args.launch / (args.role + '.json')).read_text())
    if info['runtime_id'] != data['id'] or info['launch_id'] != args.launch or info['role'] != args.role:
        raise RuntimeError('private_exec_launch_mismatch')
    wsl_cgroups.join(info)
    if args.namespace:
        import wsl_network
        plan = next((plan for plan in wsl_network.verify(ROOT, data['id'])['plans'] if plan['name'] == args.namespace), None)
        if not plan:
            raise RuntimeError('private_exec_network_not_owned')
        with Path('/run/netns', args.namespace).open('rb') as namespace:
            os.setns(namespace.fileno(), os.CLONE_NEWNET)
    folder = Path(args.cwd)
    if folder.resolve() != folder.absolute() or not folder.is_relative_to(ROOT):
        raise RuntimeError('private_exec_cwd_escape')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or not Path(command[0]).is_absolute():
        raise RuntimeError('private_exec_command_invalid')
    identity = pwd.getpwnam(args.user)
    os.chdir(folder)
    os.setgroups([])
    os.setgid(identity.pw_gid)
    os.setuid(identity.pw_uid)
    if args.supervise:
        # Nginx/Node会改进程标题；保留带运行标签的父进程，子树仍受独占cgroup回收。
        return subprocess.call(command, env=dict(os.environ))
    os.execvpe(command[0], command, dict(os.environ))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
