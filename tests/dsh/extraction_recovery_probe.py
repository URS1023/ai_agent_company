"""用真实 Windows Worker 进程及临时文件 SQLite 验证抽取中断恢复。"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def child(mode, fault, directory):
    """父进程完成 Job 归属后才加载业务；所有连接与模型请求都禁止。"""
    registration = directory / (mode + "-process.json")
    temporary = directory / (mode + "-process.tmp")
    write_json(temporary, {"pid": os.getpid(), "parent_pid": os.getppid()})
    temporary.replace(registration)
    deadline = time.monotonic() + 10
    while not (directory / (mode + "-release")).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("父进程未完成内核容器绑定，不启动业务代码")
        time.sleep(.02)
    sys.path.insert(0, str(ROOT / "tests"))
    from run_isolated_backend import configure_isolation
    configure_isolation()
    os.environ.update(DATABASE_URL="sqlite:///" + (directory / "platform.sqlite").as_posix(),
        LOCAL_STORAGE_ROOT=str(directory / "storage"), STORAGE_ROOT=str(directory / "storage"),
        LOG_DIR=str(directory / "logs"), HARNESS_HOME_ROOT=str(directory / "harness"))
    import socket
    calls = {"network": 0, "model": 0}

    def forbidden(*args, **kwargs):
        calls["network"] += 1
        raise AssertionError("抽取硬中断专项禁止网络连接")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    from sqlalchemy import func, select
    from app.business_worker.server import Worker
    from app.common.deps import AuthContext
    from app.db import new_session, get_engine
    from app.models import User, Workspace, WorkspaceMember
    from app.modules.knowledge_graph import extraction, ontology
    from app.modules.knowledge_graph.models import ExtractionCandidate, ExtractionJob, ExtractionRun
    from app.modules.knowledge_graph.schemas import (ConceptInput, ExtractionExecuteInput,
        ExtractionJobInput, OntologyInput, PublishInput)

    class NoModel:
        """真实 Worker 初始化可用，任何模型或宿主回调都使样本失败。"""
        def request(self, *args, **kwargs):
            calls["model"] += 1
            raise AssertionError("结构化抽取不应调用模型")

        def notify(self, *args, **kwargs):
            raise AssertionError("本样本未预期宿主通知")

    worker = Worker(NoModel())
    initialized = worker.initialize()
    with new_session() as db:
        member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.role == "admin"))
        ctx = AuthContext(db, db.get(User, member.user_id), db.get(Workspace, member.workspace_id), "admin", "isolated")
        state_path = directory / "input.json"
        if mode == "hold":
            model = ontology.save_ontology(ctx, OntologyInput(name="硬中断恢复合成本体"))
            concept = ontology.save_concept(ctx, model["id"], ConceptInput(code="Device", name="合成设备"))
            current = ontology.get_ontology(ctx, model["id"])
            ontology.publish_ontology(ctx, model["id"], PublishInput(version=current.version))
            payload = {"name": "硬中断恢复合成抽取", "mode": "structured", "ontologyId": model["id"],
                "records": [{"id": "fixture-device", "name": "合成设备一"}],
                "mappings": {"entityMappings": [{"conceptId": concept["id"], "keyField": "id", "nameField": "name"}]}}
            job = extraction.save_job(ctx, ExtractionJobInput(**payload))
            write_json(state_path, {"job_id": job["id"], "payload": payload})
            original_specs, original_commit = extraction._structured_specs, extraction._commit
            commits = []

            def barrier():
                """屏障前的 SQL 由真实产品执行；这里只固定明确的中断位置。"""
                run = db.scalar(select(ExtractionRun).where(ExtractionRun.job_id == job["id"]))
                write_json(directory / "barrier.json", {"pid": os.getpid(), "run_id": run.id,
                    "fault": fault, "run_status": run.status, "calls": calls,
                    "candidate_count_in_transaction": db.scalar(select(func.count()).select_from(ExtractionCandidate))})
                deadline = time.monotonic() + 30
                while not (directory / "continue").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("父进程未在既定故障预算内终止或释放 Worker")
                    time.sleep(.02)

            def specs(*args, **kwargs):
                if fault in {"started", "alive"}:
                    barrier()
                return original_specs(*args, **kwargs)

            def commit(context, message):
                commits.append(message)
                if fault == "flushed" and len(commits) == 2:
                    context.db.flush()
                    barrier()
                original_commit(context, message)
                if fault == "committed" and len(commits) == 2:
                    barrier()

            extraction._structured_specs, extraction._commit = specs, commit
            completed = extraction.execute_job(ctx, job["id"], ExtractionExecuteInput(version=job["version"]))
            write_json(directory / "finished.json", {"result": completed, "calls": calls})
        else:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            job = db.get(ExtractionJob, state["job_id"])
            runs = db.scalars(select(ExtractionRun).where(ExtractionRun.job_id == job.id)
                             .order_by(ExtractionRun.run_number)).all()
            report = {"pid": os.getpid(), "worker_initialized": initialized, "job_status": job.status,
                "run_statuses": [run.status for run in runs], "run_ids": [run.id for run in runs],
                "candidate_count": db.scalar(select(func.count()).select_from(ExtractionCandidate))}
            if fault in {"started", "flushed"}:
                assert job.status == runs[0].status == "failed" and report["candidate_count"] == 0, report
                assert runs[0].events[-1]["type"] == "interrupted"
                edited = extraction.save_job(ctx, ExtractionJobInput(**state["payload"], version=job.version), job.id)
                completed = extraction.execute_job(ctx, job.id, ExtractionExecuteInput(version=edited["version"]))
                report["manual_retry"] = {"runNumber": completed["run"]["runNumber"], "status": completed["run"]["status"]}
                assert report["manual_retry"] == {"runNumber": 2, "status": "review"}
                assert db.scalar(select(func.count()).select_from(ExtractionCandidate)) == 1
            elif fault == "committed":
                assert job.status == runs[0].status == "review" and len(runs) == report["candidate_count"] == 1, report
            else:
                assert job.status == runs[0].status == "running" and report["candidate_count"] == 0, report
            report["calls"] = calls
            assert calls == {"network": 0, "model": 0}
            write_json(directory / "after_restart.json", report)
    get_engine().dispose()


def start_owned(command, mode, directory, log):
    """同时核对虚拟环境 launcher 和实际 Python 的 Windows Job 归属。"""
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT / "tests/dsh"))
    from business_browser_acceptance import WindowsProcessJob, stop_test_process
    assert os.name == "nt", "本真实进程故障专项仅实现 Windows"
    job = WindowsProcessJob()
    process = None
    try:
        process = subprocess.Popen([*command, "--child", mode, "--directory", str(directory)], cwd=ROOT,
            stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        job.assign(process)
        registration = directory / (mode + "-process.json")
        deadline = time.monotonic() + 10
        while not registration.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Python 未完成启动登记，不放行业务")
            time.sleep(.02)
        identity = json.loads(registration.read_text(encoding="utf-8"))
        assert identity["pid"] == process.pid or identity["parent_pid"] == process.pid, identity
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1101, False, identity["pid"])
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            inside = wintypes.BOOL()
            if not kernel.IsProcessInJob(handle, job.handle, ctypes.byref(inside)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not inside.value:
                job.assign(SimpleNamespace(_handle=handle))
        finally:
            job.kernel.CloseHandle(handle)
        identity["launcher_pid"] = process.pid
        (directory / (mode + "-release")).write_text("released", encoding="utf-8")
        return process, job, identity
    except BaseException:
        stop_test_process(process, job)
        raise


def probe(fault, output):
    """每故障点仅一次，失败即结束，临时库删除前确认全部子进程退出。"""
    output.mkdir(parents=True, exist_ok=False)
    processes, jobs = [], []
    summary = {"fault": fault, "scope": "真实 Worker / 临时文件 SQLite；无 Host/RPC/模型/业务源", "status": "running"}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="test5-extraction-recovery-") as temporary:
        directory = Path(temporary)
        command = [sys.executable, str(Path(__file__).resolve()), "--fault", fault]
        try:
            with (output / "old-worker.log").open("w", encoding="utf-8") as old_log, (output / "new-worker.log").open("w", encoding="utf-8") as new_log:
                old, old_job, identity = start_owned(command, "hold", directory, old_log)
                processes.append(old)
                jobs.append(old_job)
                summary["old_identity"] = identity
                deadline = time.monotonic() + 30
                while not (directory / "barrier.json").exists():
                    if old.poll() is not None:
                        raise RuntimeError("旧 Worker 未到达故障屏障，详见原始日志")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("到达故障屏障超出原定 30 秒预算")
                    time.sleep(.05)
                summary["barrier"] = json.loads((directory / "barrier.json").read_text(encoding="utf-8"))
                assert summary["barrier"]["pid"] == identity["pid"]
                if fault != "alive":
                    summary["fault_cleanup"] = old_job.close()
                    assert summary["fault_cleanup"]["active_after"] == 0
                    summary["old_exit_before_restart"] = old.wait(timeout=5)
                new, new_job, identity = start_owned(command, "inspect", directory, new_log)
                processes.append(new)
                jobs.append(new_job)
                summary["new_identity"] = identity
                new.wait(timeout=30)
                assert new.returncode == 0, "新 Worker 断言失败，详见原始日志"
                summary["after_restart"] = json.loads((directory / "after_restart.json").read_text(encoding="utf-8"))
                if fault == "alive":
                    assert old.poll() is None, "活跃对照必须始终保留原 Worker"
                    (directory / "continue").write_text("released", encoding="utf-8")
                    old.wait(timeout=5)
                    assert old.returncode == 0
                    summary["after_release"] = json.loads((directory / "finished.json").read_text(encoding="utf-8"))
                    assert summary["after_release"]["result"]["run"]["status"] == "review"
                    assert summary["after_release"]["calls"] == {"network": 0, "model": 0}
                summary["status"] = "passed"
        except BaseException as error:
            summary.update(status="failed", error_type=type(error).__name__, message=str(error))
            raise
        finally:
            summary["cleanup"] = [job.close() for job in jobs if job.handle]
            for process in processes:
                process.wait(timeout=5)
            summary["all_processes_exited"] = all(process.poll() is not None for process in processes)
            summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
            write_json(output / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=["hold", "inspect"])
    parser.add_argument("--fault", required=True, choices=["started", "flushed", "committed", "alive"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.fault, args.directory)
    else:
        probe(args.fault, args.output)
