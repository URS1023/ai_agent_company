"""为原生Agent和代码沙箱建立专属网络命名空间；只改新namespace，不改宿主策略。"""

from __future__ import annotations

import json
import ipaddress
from pathlib import Path
import re
import subprocess


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=15,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
    if result.returncode:
        raise RuntimeError('private_network_command_failed')
    return result.stdout


def plans(runtime_id: str) -> list[dict]:
    if not re.fullmatch('[a-f0-9]{32}', runtime_id):
        raise ValueError('invalid_network_runtime_id')
    result = []
    for kind, number, control, proxy in [('agent', 17, 15004, 13129), ('code', 18, 18194, 13128)]:
        suffix = runtime_id[:6]
        result.append({'kind': kind, 'name': f'aic-{kind}-{suffix}', 'host_device': f'aic{number}h{suffix}',
            'job_device': f'aic{number}j{suffix}', 'host_address': f'10.203.{number}.1',
            'job_address': f'10.203.{number}.2', 'control_port': control, 'proxy_port': proxy})
    return result


def commands(plan: dict) -> list[list[str]]:
    ns, host, job = plan['name'], plan['host_device'], plan['job_device']
    ns_exec = ['ip', 'netns', 'exec', ns]
    rules = [
        ['ip', 'netns', 'add', ns], ['ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', job],
        ['ip', 'link', 'set', job, 'netns', ns], ['ip', 'addr', 'add', plan['host_address'] + '/30', 'dev', host],
        ['ip', 'link', 'set', host, 'up'],
        ['ip', '-n', ns, 'addr', 'add', plan['job_address'] + '/30', 'dev', job],
        ['ip', '-n', ns, 'link', 'set', job, 'up'], ['ip', '-n', ns, 'link', 'set', 'lo', 'up'],
        ['ip', '-n', ns, 'route', 'add', 'default', 'via', plan['host_address']],
    ]
    for utility in ['iptables', 'ip6tables']:
        for chain in ['INPUT', 'OUTPUT', 'FORWARD']:
            rules.append([*ns_exec, utility, '-P', chain, 'DROP'])
        rules += [[*ns_exec, utility, '-A', 'INPUT', '-i', 'lo', '-j', 'ACCEPT'],
            [*ns_exec, utility, '-A', 'OUTPUT', '-o', 'lo', '-j', 'ACCEPT'],
            [*ns_exec, utility, '-A', 'INPUT', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'],
            [*ns_exec, utility, '-A', 'OUTPUT', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']]
    rules += [[*ns_exec, 'iptables', '-A', 'INPUT', '-s', plan['host_address'], '-p', 'tcp', '--dport', str(plan['control_port']), '-j', 'ACCEPT'],
        [*ns_exec, 'iptables', '-A', 'OUTPUT', '-d', plan['host_address'], '-p', 'tcp', '--dport', str(plan['proxy_port']), '-j', 'ACCEPT']]
    return rules


def check_route_conflicts(routes: list[dict], expected: list[dict]) -> None:
    """新veth子网不得覆盖发行版已有非默认路由；冲突时不自动挑选其他网络。"""
    proposed = [ipaddress.ip_network(plan['host_address'] + '/30', strict=False) for plan in expected]
    for route in routes:
        destination = route.get('dst', 'default')
        if destination == 'default':
            continue
        existing = ipaddress.ip_network(destination, strict=False)
        if existing.version == 4 and any(existing.overlaps(network) for network in proposed):
            raise RuntimeError('private_network_subnet_conflicts_existing_route')


def create(root: Path, runtime_id: str) -> dict:
    """先预约标记，再创建新名字；任一步失败保留账本，不接管或自动重试未知网络。"""
    path = root / 'network.json'
    if path.exists():
        value = json.loads(path.read_text())
        if value.get('runtime_id') != runtime_id or value.get('status') != 'ready':
            raise RuntimeError('previous_private_network_requires_review')
        verify(root, runtime_id)
        return value
    existing = {item['name'] for item in json.loads(run(['ip', '-j', 'netns', 'list']) or '[]')}
    expected = plans(runtime_id)
    if any(item['name'] in existing or (Path('/sys/class/net') / item['host_device']).exists() for item in expected):
        raise RuntimeError('existing_network_name_not_adopted')
    check_route_conflicts(json.loads(run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']) or '[]'), expected)
    value = {'kind': 'ai-agent-private-network-v1', 'runtime_id': runtime_id, 'status': 'attempted', 'plans': expected}
    path.write_text(json.dumps(value, indent=2))
    try:
        for plan in expected:
            for args in commands(plan):
                run(args)
            plan['namespace_inode'] = (Path('/run/netns') / plan['name']).stat().st_ino
            plan['host_ifindex'] = int((Path('/sys/class/net') / plan['host_device'] / 'ifindex').read_text())
            plan['iptables_rules'] = run(['ip', 'netns', 'exec', plan['name'], 'iptables-save'])
            plan['ip6tables_rules'] = run(['ip', 'netns', 'exec', plan['name'], 'ip6tables-save'])
        value['status'] = 'ready'
        path.write_text(json.dumps(value, indent=2))
        return value
    except BaseException as error:
        value.update(status='failed', error_type=type(error).__name__)
        path.write_text(json.dumps(value, indent=2))
        raise


def stable_rules(value: str) -> list[str]:
    """去掉iptables-save生成时间与计数；策略和每条匹配必须保持。"""
    return [re.sub(r'\[[0-9]+:[0-9]+\]', '[0:0]', line) for line in value.splitlines() if not line.startswith('#')]


def verify(root: Path, runtime_id: str) -> dict:
    value = json.loads((root / 'network.json').read_text())
    if value.get('kind') != 'ai-agent-private-network-v1' or value.get('runtime_id') != runtime_id or value.get('status') != 'ready':
        raise RuntimeError('private_network_receipt_invalid')
    expected = plans(runtime_id)
    if len(value.get('plans', [])) != len(expected):
        raise RuntimeError('private_network_count_changed')
    for actual, plan in zip(value['plans'], expected, strict=True):
        if any(actual.get(key) != val for key, val in plan.items()):
            raise RuntimeError('private_network_plan_changed')
        if (Path('/run/netns') / plan['name']).stat().st_ino != actual['namespace_inode']:
            raise RuntimeError('private_network_namespace_replaced')
        if int((Path('/sys/class/net') / plan['host_device'] / 'ifindex').read_text()) != actual['host_ifindex']:
            raise RuntimeError('private_network_link_replaced')
        for command_name, key in [('iptables-save', 'iptables_rules'), ('ip6tables-save', 'ip6tables_rules')]:
            current = run(['ip', 'netns', 'exec', plan['name'], command_name])
            if stable_rules(current) != stable_rules(actual[key]):
                raise RuntimeError('private_network_firewall_changed')
    return value
