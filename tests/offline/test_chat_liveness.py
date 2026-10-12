"""离线校验聊天任务存活判定与清理边界（谁能被判为失败、谁必须保留）。

验证行为：仍有活跃租约的任务不会被恢复清理；其它主机的任务与已成功的任务不受影响；
中断清理不会覆盖已完成任务的终态。
隔离方式：SimpleNamespace 构造 run + MagicMock 会话 + 多机名桩，
不启动应用、不连数据库（仅本地计时/事件）。
边界：判定依据是执行者证据而非超时时长；无法确认状态的任务一律保留。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
import socket

from app.modules.runtime import chat_liveness as live
from app.modules.system_tools.scheduler_lease import SchedulerLease


def run(status='running', host=None):
    return SimpleNamespace(id='run-test', workspace_id='workspace-test', status=status, context_snapshot={'chatLease': {
        'host': host or socket.gethostname(), 'runId': 'run-test'}})


def test_active_lease_is_not_recovered(monkeypatch, tmp_path):
    path=tmp_path/'run.lock'
    owner=SchedulerLease(path)
    assert owner.acquire()
    monkeypatch.setattr(live, 'chat_lease', lambda _: SchedulerLease(path))
    db=MagicMock()
    db.execute.return_value.rowcount = 1
    try:
        assert not live.recover_abandoned_chat(db, run())
        db.commit.assert_not_called()
    finally:
        owner.release()
    abandoned=run()
    assert live.recover_abandoned_chat(db, abandoned)
    assert abandoned.status=='failed'
    assert abandoned.error_code=='chat_interrupted'
    assert db.execute.call_count==3


def test_other_hosts_and_successful_runs_untouched():
    db=MagicMock()
    assert not live.recover_abandoned_chat(db, run(host='another-machine'))
    assert not live.recover_abandoned_chat(db, run(status='completed'))
    legacy=run();legacy.context_snapshot={}
    assert not live.recover_abandoned_chat(db, legacy)
    db.commit.assert_not_called()


def test_interrupted_cleanup_preserves_completed_run():
    db=MagicMock()
    assert not live.mark_interrupted(db, run(status='completed'))
    db.execute.assert_not_called()
