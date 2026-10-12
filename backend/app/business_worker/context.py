"""每项业务操作的独立数据库、用户和取消上下文。"""
from __future__ import annotations

import base64
import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.requests import Request

_controls = {}
# 运行锁只在调用方持有期间有效；弱引用避免每个历史运行的锁永久占用内存。
_locks = weakref.WeakValueDictionary()
_guard = threading.RLock()
request_actor = ContextVar("business_request_actor", default=None)


def lock_for(identity):
    """同一会话建单、同一运行台账串行，业务工具不占事件持久化锁。"""
    with _guard:
        return _locks.setdefault(identity, threading.RLock())


def control_for(run_id):
    from app.modules.runtime.task_control import TaskControl
    with _guard:
        return _controls.setdefault(run_id, TaskControl())


def release_control(run_id):
    """运行进入终态后释放取消控制对象，避免长时间运行的服务积累无用引用。"""
    with _guard:
        _controls.pop(run_id, None)


def cancel(run_id):
    control_for(run_id).cancelled.set()
    return {"status": "stopping", "run_id": run_id}


def cancel_all():
    with _guard:
        for control in _controls.values():
            control.cancelled.set()


def request_object(raw):
    """重建原请求最小 ASGI scope，保留 cookie/header 和请求目标。"""
    headers = [(str(k).lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in raw.get("headers", [])]
    header_values = {key.decode(): value.decode("latin-1") for key, value in headers}
    scheme = raw.get("scheme") or header_values.get("x-forwarded-proto", "http").split(",")[0].strip()
    return Request({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": raw.get("method", "GET").upper(), "scheme": scheme,
                    "path": raw.get("path", "/"), "raw_path": raw.get("path", "/").encode(),
                    "query_string": raw.get("query_string", "").encode(), "headers": headers,
                    "server": ("localhost", 80), "client": ("127.0.0.1", 0), "root_path": ""})


def authenticate(db, raw):
    from app.common.deps import get_current_context
    request = request_object(raw)
    return get_current_context(request, db=db, x_workspace_id=request.headers.get("x-workspace-id"), credentials=None)


@contextmanager
def session():
    """服务层可提交自身事务；失败回滚剩余事务并释放全部上下文。"""
    from app.db import new_session
    db = new_session()
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def run_context(run, *, sink=None):
    from app.modules.runtime.task_control import current
    control = control_for(run.id)
    control.business_run_id = run.id
    control.business_task_id = (run.context_snapshot or {}).get("task_id")
    control.business_workspace_id = run.workspace_id
    control.model_profile = run.model_profile
    control.model_workspace_id = run.workspace_id
    if sink:
        control.sink = sink
    token = current.set(control)
    try:
        yield control
    finally:
        current.reset(token)
