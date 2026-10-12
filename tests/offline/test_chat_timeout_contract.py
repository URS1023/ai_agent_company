"""Total task deadlines must reach the user as identifiable timeout failures.

中文说明：任务总时限必须以"可识别的超时失败"呈现给用户，而不是被降级成普通模型/工具错误。
隔离方式：离线检查（无网络、无数据库），用时间桩控制到期时机。
边界：超时必须可识别、可区分于供应商报错。
"""
import time

import pytest

from app.modules.runtime.agent import _friendly_run_error
from app.modules.runtime.task_control import ChatCancelled, TaskControl


def test_total_task_deadline_has_specific_error_and_preserves_results():
    with pytest.raises(TimeoutError) as captured:
        TaskControl(deadline=time.monotonic() - 1).check()
    message, code = _friendly_run_error(captured.value, "expired-chat")
    assert code == "chat_task_timeout"
    assert "总时间上限" in message and "保留" in message
    assert "外部服务" not in message


def test_timeout_without_message_is_still_classified():
    _, code = _friendly_run_error(TimeoutError(), "timed-out-call")
    assert code == "run_timeout"


def test_explicit_cancel_takes_precedence_over_expired_deadline():
    control = TaskControl(deadline=time.monotonic() - 1)
    control.cancelled.set()
    with pytest.raises(ChatCancelled) as captured:
        control.check()
    assert _friendly_run_error(captured.value, "cancelled-chat")[1] == "chat_cancelled"
