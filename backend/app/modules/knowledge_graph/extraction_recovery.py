"""只凭本机精确进程身份收尾抽取中断；未知归属与旧成功材料始终保留。"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import sys
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.modules.knowledge_graph.models import ExtractionJob, ExtractionRun

OWNER_KEY = "executionOwner"
SCAN_LIMIT = 200


def machine_identity() -> str | None:
    """只读 Windows 本机身份；非 Windows 与读取失败不猜测主机归属。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        if not isinstance(value, str) or not value.strip():
            return None
        return hashlib.sha256((value.strip().lower() + "\n" + socket.gethostname().lower()).encode()).hexdigest()
    except OSError:
        return None


def windows_process(pid: int) -> tuple[str, str | None]:
    """不等待地核对内核进程及创建时间；仅确定不存在或已退出才报告死亡。"""
    if sys.platform != "win32":
        return "unknown", None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER 对有效非零 PID 表示当前不存在；拒绝访问等均不可推断死亡。
        return ("dead", None) if ctypes.get_last_error() == 87 else ("unknown", None)
    try:
        created, exited, system, user = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in (created, exited, system, user))):
            return "unknown", None
        stamp = str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        state = kernel.WaitForSingleObject(handle, 0)
        return ("dead" if state == 0 else "active" if state == 258 else "unknown"), stamp
    finally:
        kernel.CloseHandle(handle)


def new_owner() -> dict:
    """代次总是唯一；无法取得可信平台身份时仍可执行，但不自动恢复。"""
    state, created = windows_process(os.getpid())
    return {"protocol": 1, "generation": uuid.uuid4().hex, "platform": sys.platform,
            "host": machine_identity(), "pid": os.getpid(), "created": created if state == "active" else None}


def owner_of(run: ExtractionRun) -> dict:
    snapshot = run.input_snapshot
    owner = snapshot.get(OWNER_KEY) if isinstance(snapshot, dict) else None
    return owner if isinstance(owner, dict) else {}


def owner_state(owner: dict) -> str:
    """创建时间隔离 PID 复用；缺失、外机、无权限及不支持的平台均未知。"""
    host = machine_identity()
    if not isinstance(owner, dict):
        return "unknown"
    created, generation = owner.get("created"), owner.get("generation")
    # FILETIME 是规范十进制 uint64；先限制 ASCII 和长度，再转换，坏记录不能阻止启动。
    if (not isinstance(created, str) or not 1 <= len(created) <= 20
            or not created.isascii() or not created.isdecimal()
            or not 0 < int(created) <= 0xffffffffffffffff or str(int(created)) != created
            or not isinstance(generation, str) or len(generation) != 32
            or any(char not in "0123456789abcdef" for char in generation)):
        return "unknown"
    if (not host or type(owner.get("protocol")) is not int or owner["protocol"] != 1 or owner.get("platform") != "win32"
            or owner.get("host") != host or type(owner.get("pid")) is not int
            or not 0 < owner["pid"] <= 0xffffffff):
        return "unknown"
    state, created = windows_process(owner["pid"])
    if state == "active" and created and created != owner["created"]:
        return "dead"
    return state


def locked_pair(db, run_id: str, workspace_id: str | None = None):
    """统一 Run→Job 锁序并强制重新读库；既有 version 列同时提供更新 CAS。"""
    statement = select(ExtractionRun).where(ExtractionRun.id == run_id)
    if workspace_id is not None:
        statement = statement.where(ExtractionRun.workspace_id == workspace_id)
    run = db.scalar(statement.with_for_update().execution_options(populate_existing=True))
    if run is None:
        return None, None
    job = db.scalar(select(ExtractionJob).where(ExtractionJob.id == run.job_id,
                    ExtractionJob.workspace_id == run.workspace_id)
                    .with_for_update().execution_options(populate_existing=True))
    return job, run


def current_run(job, run, generation: str) -> bool:
    """活动运行同时归属于本代次、任务当前 runId 和原工作区。"""
    return bool(job and run and job.status == run.status == "running"
                and job.workspace_id == run.workspace_id and job.id == run.job_id
                and isinstance(job.result_summary, dict) and job.result_summary.get("runId") == run.id
                and owner_of(run).get("generation") == generation)


def mark_failed(db, job, run, message: str, *, interrupted: bool = False, actor_user_id=None):
    """调用方已锁定并验证归属；运行、任务及审计只在同一事务提交。"""
    now = datetime.utcnow()
    job.status = run.status = "failed"
    job.error_message = run.error_message = message[:4000]
    job.result_summary = {"status": "interrupted" if interrupted else "failed", "runId": run.id}
    run.completed_at = now
    run.events = [*(run.events or []), {"at": now.isoformat(),
        "type": "interrupted" if interrupted else "failed", "message": message[:1000]}]
    audit(db, workspace_id=job.workspace_id, actor_user_id=actor_user_id,
          action="extraction.execute.interrupted" if interrupted else "extraction.execute.failed",
          resource_type="extraction_job", resource_id=job.id,
          metadata={"extractionRunId": run.id, "message": message[:1000]})


def recover_interrupted(factory) -> dict:
    """仅扫描声明本机归属的活动抽取，逐项短事务校验协议并条件收尾，不重试。"""
    report = {"examined": 0, "recovered": 0, "active": 0, "unknown": 0, "conflict": 0, "deferred": False}
    host = machine_identity()
    if not host:
        report["unsupported"] = True
        return report
    with factory() as db:
        owner = ExtractionRun.input_snapshot[OWNER_KEY]
        # 协议留给逐行严格校验，避免坏 JSON 在方言整数转换时中止整批枚举。
        ids = db.scalars(select(ExtractionRun.id).where(ExtractionRun.status == "running",
            owner["platform"].as_string() == "win32",
            owner["host"].as_string() == host).order_by(ExtractionRun.id).limit(SCAN_LIMIT + 1)).all()
    report["deferred"] = len(ids) > SCAN_LIMIT
    for run_id in ids[:SCAN_LIMIT]:
        with factory() as db:
            try:
                job, run = locked_pair(db, run_id)
                report["examined"] += 1
                owner = owner_of(run) if run else {}
                if not current_run(job, run, owner.get("generation", "")):
                    report["conflict"] += 1
                    continue
                state = owner_state(owner)
                if state != "dead":
                    report[state] += 1
                    continue
                mark_failed(db, job, run, "原抽取 Worker 已确认退出，本次运行已中断；请核对后明确重试。", interrupted=True)
                db.commit()
                report["recovered"] += 1
            except StaleDataError:
                db.rollback()
                report["conflict"] += 1
    if report["deferred"]:
        logging.warning("抽取启动恢复达到 %s 项上限，仍有活动运行尚未核对", SCAN_LIMIT)
    logging.info("抽取启动恢复结果 %s；无可信本机 owner 的旧运行不自动处理", report)
    return report
