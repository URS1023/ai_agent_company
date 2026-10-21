"""日常启动时更新已配置工具桥的上游快照，不启动完整Dify服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import windows
import wsl
import wsl_http_bridge as bridge
import wsl_bridge_startup
from wsl_tool_credentials import ToolCredentialStore


def configured(root: Path) -> bool:
    """只检查公开索引，不解密凭据；撤销或未完成登记的工具不触发启动。"""
    records = ToolCredentialStore(root)._load()['records']
    return any(row.get('enabled') and row.get('phase') == 'configured'
               for row in records.values())


def reconcile(root: Path) -> dict:
    """已知就绪桥可因所属上游变化重建一次，失败或未完成尝试须人工核查。

    预约在运行清单短锁内完成；停止使用launch_id比较，防止误停并发替换的桥。
    原启动器再次验证真实监听者和NAT，最终代理只消费新桥的所属配置快照。
    此函数不启动Dify角色，不调用模型，也不查询业务数据库。
    """
    if not configured(root):
        return {'configured': False, 'ready': False}
    attempt_id = uuid4().hex

    def reserve(state):
        previous = state.get('bridge_lifecycle')
        if previous and previous.get('status') != 'ready':
            raise RuntimeError('bridge_lifecycle_previous_attempt_requires_review')
        record = state.get('processes', {}).get(bridge.COMPONENT, {})
        if state.get('bridge_failures') and not record.get('ready'):
            raise RuntimeError('bridge_previous_startup_requires_review')
        if previous:
            state.setdefault('bridge_lifecycle_history', []).append(previous)
        state['bridge_lifecycle'] = {'id': attempt_id, 'status': 'running',
            'started_at': datetime.now(timezone.utc).isoformat(),
            'previous_launch_id': record.get('launch_id')}

    windows.update_runtime(root, reserve)

    def finish(status, **details):
        def update(state):
            if state.get('bridge_lifecycle', {}).get('id') != attempt_id:
                raise RuntimeError('bridge_lifecycle_reservation_changed')
            state['bridge_lifecycle'].update(status=status, **details,
                finished_at=datetime.now(timezone.utc).isoformat())
        windows.update_runtime(root, update)

    try:
        state = windows.load(root)
        process = bridge.owned_bridge(root, state)
        record = state.get('processes', {}).get(bridge.COMPONENT, {})
        if record and not record.get('ready'):
            raise RuntimeError('bridge_unready_process_requires_review')
        if record and not process:
            # 有登记但非正常停止不等于冷启动，保留证据，不能自动掩盖异常退出。
            raise RuntimeError('bridge_registered_process_missing_requires_review')
        network = wsl.verified_nat_network()
        upstreams = bridge.capture_upstreams(root)
        rebuilt = False
        if process and (record.get('network') != network or record.get('upstreams') != upstreams):
            stopped = bridge.stop_bridge(root, expected_launch_id=record['launch_id'])
            if stopped.get('stopped') is not True:
                raise RuntimeError('bridge_previous_process_not_stopped')
            rebuilt = True
        result = bridge.start_bridge(root)
        proxy = wsl_bridge_startup.reconcile_windows(root)
        current = windows.load(root)['processes'][bridge.COMPONENT]
        finish('ready', launch_id=current['launch_id'], rebuilt=rebuilt)
        return {'configured': True, **result, 'rebuilt': rebuilt, 'tool_proxy': proxy}
    except BaseException as error:
        finish('failed', error_type=type(error).__name__)
        raise
