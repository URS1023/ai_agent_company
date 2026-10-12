"""临时 SQLite 验证抽取归属、条件收尾与已提交结果的恢复边界。"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.common.deps import AuthContext
from app.models import AuditLog, User, Workspace, WorkspaceMember
from app.modules.knowledge_graph import extraction, extraction_recovery as recovery
from app.modules.knowledge_graph.models import ExtractionCandidate, ExtractionJob, ExtractionRun
from app.modules.knowledge_graph.schemas import ExtractionExecuteInput
from test_extraction_materialization_contract import accepted_run, job_payload, materialize
from test_graph_property_contracts import EXTRACTIONS, definition, model_graph
from test_knowledge_graph import result


class StopAfterStarted(BaseException):
    """仅固定真实初次提交后的状态，不触发产品普通异常收尾。"""


def actor(db, headers):
    """沿用真实登录令牌，新的授权上下文不继承请求权限缓存。"""
    workspace_id = headers["X-Workspace-Id"]
    member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id,
                                                    WorkspaceMember.role == "admin"))
    return AuthContext(db, db.get(User, member.user_id), db.get(Workspace, workspace_id), "admin",
                       headers["Authorization"][7:])


def hold_running(db, headers, job_id, monkeypatch):
    """走实际 execute_job 的 running 提交，随后用受控硬中断哨兵停止。"""
    def stop(*args, **kwargs):
        raise StopAfterStarted()
    with monkeypatch.context() as patch:
        patch.setattr(extraction, "_structured_specs", stop)
        job = db.get(ExtractionJob, job_id, populate_existing=True)
        with pytest.raises(StopAfterStarted):
            extraction.execute_job(actor(db, headers), job_id, ExtractionExecuteInput(version=job.version))
    db.rollback()
    return db.scalar(select(ExtractionRun).where(ExtractionRun.job_id == job_id)
                     .order_by(ExtractionRun.run_number.desc()))


def test_historical_materialization_keeps_current_running_job(client, administrator, db, monkeypatch):
    """历史合法运行可物化，但不能覆盖同任务新运行的活动归属。"""
    model = model_graph(client, administrator, entity_attributes=[definition()])
    job, old_run_id, _ = accepted_run(client, administrator, model)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    before = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    assert before["status"] == "running" and active.run_number == 2
    completed = result(materialize(client, administrator, model, job, old_run_id))
    assert completed["run"]["status"] == "completed"
    after = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    assert after["status"] == "running", after
    assert after["resultSummary"]["runId"] == active.id


def test_historical_review_keeps_current_running_summary(client, administrator, db, monkeypatch):
    """历史运行仍可合法复核，计数只归该运行，不能混入新一轮任务摘要。"""
    model = model_graph(client, administrator, entity_attributes=[definition()])
    job, old_run_id, _ = accepted_run(client, administrator, model)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    before = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    candidates = result(client.get(f"{EXTRACTIONS}/{job['id']}/runs/{old_run_id}/candidates", headers=administrator))
    item = candidates[0]
    reviewed = result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{old_run_id}/review", headers=administrator,
        json={"items": [{"candidateId": item["id"], "version": item["version"], "decision": "rejected"}]}))
    after = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    assert reviewed["run"]["id"] == old_run_id
    assert after["status"] == "running" and after["resultSummary"]["runId"] == active.id
    assert after["resultSummary"] == before["resultSummary"]


def pending_job(client, headers):
    """创建单条结构化输入，模型、网络与业务源均不参与。"""
    model = model_graph(client, headers, entity_attributes=[definition()])
    return result(client.post(EXTRACTIONS, headers=headers, json=job_payload(model)), 201)


@pytest.mark.parametrize("phase", ["initial_before", "initial_after", "final_before", "final_after"])
def test_commit_failure_uses_actual_persisted_generation(client, administrator, db, monkeypatch, phase):
    """初次与终态提交前后故障不能互相冒充，已提交成功仅按新会话授权回读。"""
    job = pending_job(client, administrator)
    original = extraction._commit
    stages = []

    def fail(ctx, message):
        stages.append(message)
        target = len(stages) == (1 if phase.startswith("initial") else 2)
        if target and phase.endswith("before"):
            raise RuntimeError("受控提交前故障")
        original(ctx, message)
        if target:
            raise RuntimeError("受控提交后 ACK 丢失")

    monkeypatch.setattr(extraction, "_commit", fail)
    response = client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=administrator,
                           json={"version": job["version"]})
    assert response.status_code == (200 if phase == "final_after" else 500), response.text
    db.expire_all()
    stored = db.get(ExtractionJob, job["id"])
    runs = db.scalars(select(ExtractionRun).where(ExtractionRun.job_id == job["id"])).all()
    count = db.scalar(select(func.count()).select_from(ExtractionCandidate))
    if phase == "initial_before":
        assert stored.status == "draft" and runs == [] and count == 0
    elif phase == "final_after":
        assert stored.status == runs[0].status == "review" and count == 3
        payload = result(response)
        assert payload["run"]["id"] == runs[0].id
        assert recovery.OWNER_KEY not in response.text and "generation" not in response.text
    else:
        assert stored.status == runs[0].status == "failed" and count == 0
    assert len(stages) <= 2, "不能为补 ACK 重做抽取或重发提交"


def test_committed_result_rechecks_current_membership(client, administrator, db, monkeypatch):
    """同一请求已缓存管理员权限，提交后撤权仍必须阻止恢复响应。"""
    job = pending_job(client, administrator)
    original = extraction._commit
    stages = []

    def revoke_after_commit(ctx, message):
        original(ctx, message)
        stages.append(message)
        if len(stages) == 2:
            with Session(bind=db.get_bind()) as other:
                member = other.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ctx.workspace_id,
                                                                      WorkspaceMember.user_id == ctx.user_id))
                other.delete(member)
                other.commit()
            raise RuntimeError("业务已提交，但回执前成员被撤销")

    monkeypatch.setattr(extraction, "_commit", revoke_after_commit)
    response = client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=administrator,
                           json={"version": job["version"]})
    assert response.status_code in {401, 403}, response.text
    db.expire_all()
    assert db.get(ExtractionJob, job["id"]).status == "review"
    assert db.scalar(select(func.count()).select_from(ExtractionCandidate)) == 3
    assert "candidate" not in response.text.lower()


@pytest.mark.parametrize("observation,created,expected", [
    ("active", "11", "active"), ("active", "12", "dead"),
    ("dead", "11", "dead"), ("dead", None, "dead"), ("unknown", None, "unknown"),
])
def test_owner_identity_distinguishes_pid_reuse(monkeypatch, observation, created, expected):
    """离线进程观测桩验证创建时间比较；不把 PID 相同当作同一执行者。"""
    monkeypatch.setattr(recovery, "machine_identity", lambda: "fixture-host")
    monkeypatch.setattr(recovery, "windows_process", lambda pid: (observation, created))
    owner = {"protocol": 1, "platform": "win32", "host": "fixture-host", "pid": 123,
             "created": "11", "generation": "a" * 32}
    assert recovery.owner_state(owner) == expected


@pytest.mark.parametrize("change", [{}, {"host": "foreign"}, {"platform": "linux"}, {"created": None},
                                    {"pid": 0}, {"pid": True}, {"generation": ""}])
def test_unknown_owner_never_uses_pid_probe(monkeypatch, change):
    """旧记录、其他机器或缺失字段在打开任何进程前即停止判断。"""
    monkeypatch.setattr(recovery, "machine_identity", lambda: "fixture-host")
    monkeypatch.setattr(recovery, "windows_process", lambda pid: pytest.fail("未知身份不得探测 PID"))
    owner = {"protocol": 1, "platform": "win32", "host": "fixture-host", "pid": 123,
             "created": "11", "generation": "a" * 32}
    assert recovery.owner_state({**owner, **change} if change else {}) == "unknown"


@pytest.mark.skipif(os.name != "nt", reason="真实内核身份只支持 Windows")
def test_current_windows_process_has_exact_active_identity():
    """实际调用 Windows 内核读取当前测试进程，不能由自造 PID 代替。"""
    owner = recovery.new_owner()
    assert owner["pid"] == os.getpid() and owner["created"]
    assert recovery.owner_state(owner) == "active"


@pytest.mark.parametrize("field,value", [("created", "²"), ("created", "9" * 5000),
    ("created", "18446744073709551616"), ("created", "011"), ("generation", "z" * 32), ("protocol", True)])
def test_malformed_owner_is_unknown_without_crashing(monkeypatch, field, value):
    """损坏身份只能保留未知，不能使一次 Worker 初始化被单条坏记录终止。"""
    monkeypatch.setattr(recovery, "machine_identity", lambda: "fixture-host")
    monkeypatch.setattr(recovery, "windows_process", lambda pid: ("active", "11"))
    owner = {"protocol": 1, "platform": "win32", "host": "fixture-host", "pid": 123,
             "created": "11", "generation": "a" * 32, field: value}
    assert recovery.owner_state(owner) == "unknown"


def test_ack_restore_never_mixes_new_job_with_old_success(client, administrator, db, monkeypatch):
    """Run1 提交后 Run2 已真实开始时，丢 ACK 不得返回跨代次的成功组合。"""
    job = pending_job(client, administrator)
    original = extraction._commit
    stages = []

    def next_generation(ctx, message):
        original(ctx, message)
        stages.append(message)
        if len(stages) == 2:
            with Session(bind=db.get_bind(), expire_on_commit=False) as next_db:
                hold_running(next_db, administrator, job["id"], monkeypatch)
            raise RuntimeError("Run1 回执丢失，此时 Run2 已开始")

    monkeypatch.setattr(extraction, "_commit", next_generation)
    response = client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=administrator,
                           json={"version": job["version"]})
    assert response.status_code == 500, response.text
    db.expire_all()
    runs = db.scalars(select(ExtractionRun).where(ExtractionRun.job_id == job["id"])
                     .order_by(ExtractionRun.run_number)).all()
    assert [run.status for run in runs] == ["review", "running"]
    assert db.get(ExtractionJob, job["id"]).result_summary["runId"] == runs[1].id


@pytest.mark.parametrize("state", ["dead", "active", "unknown"])
def test_recovery_only_closes_confirmed_current_owner(client, administrator, db, monkeypatch, state):
    job = pending_job(client, administrator)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    monkeypatch.setattr(recovery, "owner_state", lambda owner: state)
    report = recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))
    db.expire_all()
    expected = "failed" if state == "dead" else "running"
    assert db.get(ExtractionJob, job["id"]).status == db.get(ExtractionRun, active.id).status == expected
    assert report["recovered"] == int(state == "dead")
    assert report["examined"] == 1
    if state == "dead":
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "extraction.execute.interrupted"))
        assert audit.actor_user_id is None and "generation" not in str(audit.metadata_json)
        assert recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))["examined"] == 0


@pytest.mark.parametrize("kind", ["legacy", "foreign", "malformed"])
def test_startup_preserves_unprovable_persisted_owners(client, administrator, db, monkeypatch, kind):
    """实际 JSON 行经启动扫描后仍保留未知，不以旧 running 状态推断可接管。"""
    job = pending_job(client, administrator)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    snapshot = dict(active.input_snapshot)
    if kind == "legacy":
        snapshot.pop(recovery.OWNER_KEY)
    else:
        snapshot[recovery.OWNER_KEY] = {**snapshot[recovery.OWNER_KEY],
            **({"host": "foreign"} if kind == "foreign" else {"created": "²"})}
    active.input_snapshot = snapshot
    db.commit()
    report = recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))
    db.expire_all()
    assert db.get(ExtractionJob, job["id"]).status == db.get(ExtractionRun, active.id).status == "running"
    assert report["recovered"] == 0
    assert report["unknown"] == (1 if kind == "malformed" else 0)


def test_startup_scan_limit_reports_deferred_work(client, administrator, db, monkeypatch, caplog):
    """以更小同机制上限固定边界，不能把未检查任务写成已恢复。"""
    job = pending_job(client, administrator)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    monkeypatch.setattr(recovery, "SCAN_LIMIT", 0)
    report = recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))
    assert report["deferred"] is True and report["examined"] == report["recovered"] == 0
    assert "尚未核对" in caplog.text
    db.expire_all()
    assert db.get(ExtractionRun, active.id).status == "running"


def test_startup_scan_pg_does_not_cast_untrusted_protocol(monkeypatch):
    """编译实际扫描语句，坏协议不能在 Python 未知判断前触发 PG 整数转换。"""
    statements = []

    class Capture:
        """只捕获产品 SQLAlchemy 语句，不连接 PostgreSQL 或任何数据库。"""
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalars(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(recovery, "machine_identity", lambda: "fixture-host")
    report = recovery.recover_interrupted(Capture)
    assert report["examined"] == 0 and len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    print("实际恢复查询 PostgreSQL 离线编译：", sql)
    assert " AS INTEGER" not in sql.upper(), sql
    assert "protocol" not in sql, "协议类型应由逐行严格判断，不能依赖方言 JSON 转换或相等运算"
    assert "->> 'platform'" in sql and "->> 'host'" in sql and "'running'" in sql
    assert f"LIMIT {recovery.SCAN_LIMIT + 1}" in sql


@pytest.mark.parametrize("protocol", ["abc", "1", True])
def test_damaged_and_valid_protocols_are_classified_independently(client, administrator, db, monkeypatch, protocol):
    """临时 SQLite 同时保存坏协议和正规协议，未知行保留且不阻止已确认死亡的正常行。"""
    bad_job = pending_job(client, administrator)
    bad = hold_running(db, administrator, bad_job["id"], monkeypatch)
    bad.input_snapshot = {**bad.input_snapshot, recovery.OWNER_KEY: {
        **recovery.owner_of(bad), "protocol": protocol}}
    # Python 将 True 与 1 比作相等；显式标脏并回读类型，保证坏值确实进入临时库。
    flag_modified(bad, "input_snapshot")
    db.commit()
    db.refresh(bad)
    saved_protocol = recovery.owner_of(bad)["protocol"]
    assert type(saved_protocol) is type(protocol) and saved_protocol == protocol, (
        "损坏协议夹具必须真实持久化", type(saved_protocol).__name__, saved_protocol)
    good_job = pending_job(client, administrator)
    good = hold_running(db, administrator, good_job["id"], monkeypatch)
    probes = []

    def confirmed_dead(pid):
        probes.append(pid)
        return "dead", None

    # 只控制进程观测；协议校验与数据库枚举/条件更新仍走真实产品函数。
    monkeypatch.setattr(recovery, "windows_process", confirmed_dead)
    report = recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))
    db.expire_all()
    assert db.get(ExtractionRun, bad.id).status == db.get(ExtractionJob, bad_job["id"]).status == "running"
    assert db.get(ExtractionRun, good.id).status == db.get(ExtractionJob, good_job["id"]).status == "failed"
    assert report["examined"] == 2 and report["unknown"] == report["recovered"] == 1, report
    assert len(probes) == 1, "坏协议必须在查询内核进程前判为未知"
    assert db.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.action == "extraction.execute.interrupted")) == 1


def test_late_old_failure_cannot_overwrite_new_generation(client, administrator, db, monkeypatch):
    job = pending_job(client, administrator)
    first = hold_running(db, administrator, job["id"], monkeypatch)
    generation = recovery.owner_of(first)["generation"]
    monkeypatch.setattr(recovery, "owner_state", lambda owner: "dead")
    recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))
    second = hold_running(db, administrator, job["id"], monkeypatch)
    ctx = actor(db, administrator)
    assert not extraction._record_failure(db.get_bind(), ctx.workspace_id, ctx.user_id,
                                          job["id"], first.id, generation, "迟到的旧失败")
    db.expire_all()
    current = db.get(ExtractionJob, job["id"])
    assert current.status == "running" and current.result_summary["runId"] == second.id
    assert db.get(ExtractionRun, first.id).events[-1]["type"] == "interrupted"


def test_late_success_is_fenced_before_candidate_inserts(client, administrator, db, monkeypatch):
    """受控代次撤销模拟迟到末段，实际数据库状态使候选写入被拒绝。"""
    job = pending_job(client, administrator)
    original = extraction._structured_specs

    def revoke(*args, **kwargs):
        specs = original(*args, **kwargs)
        monkeypatch.setattr(recovery, "owner_state", lambda owner: "dead")
        assert recovery.recover_interrupted(sessionmaker(bind=db.get_bind()))["recovered"] == 1
        return specs

    monkeypatch.setattr(extraction, "_structured_specs", revoke)
    response = client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=administrator,
                           json={"version": job["version"]})
    assert response.status_code == 409, response.text
    assert db.scalar(select(func.count()).select_from(ExtractionCandidate)) == 0
    db.expire_all()
    assert db.get(ExtractionJob, job["id"]).result_summary["status"] == "interrupted"


def test_two_recoverers_use_actual_version_cas(client, administrator, db, monkeypatch, tmp_path):
    """两个独立连接先读同一版本，再顺序提交，过期恢复者必须由数据库 CAS 拒绝。"""
    job = pending_job(client, administrator)
    active = hold_running(db, administrator, job["id"], monkeypatch)
    db.rollback()
    local = create_engine("sqlite:///" + (tmp_path / "race.sqlite").as_posix())
    # SQLite 原生备份只复制当前临时夹具，保留真实外键和 version 映射。
    source, target = db.get_bind().raw_connection(), local.raw_connection()
    try:
        source.driver_connection.backup(target.driver_connection)
    finally:
        source.close()
        target.close()
    factory = sessionmaker(bind=local, expire_on_commit=False)
    barrier, first_done = Barrier(2), Event()
    original = recovery.mark_failed
    updates = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE kg_extraction_"):
            updates.append(statement)

    def interleave(session, *args, **kwargs):
        original(session, *args, **kwargs)
        barrier.wait(timeout=5)
        if current_thread().name.endswith("_1"):
            assert first_done.wait(5)

    def attempt():
        try:
            return recovery.recover_interrupted(factory)
        finally:
            if current_thread().name.endswith("_0"):
                first_done.set()

    monkeypatch.setattr(recovery, "owner_state", lambda owner: "dead")
    monkeypatch.setattr(recovery, "mark_failed", interleave)
    event.listen(local, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="recovery") as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        assert sum(item["recovered"] for item in results) == 1
        assert sum(item["conflict"] for item in results) == 1
        with factory() as check:
            assert check.get(ExtractionRun, active.id).status == check.get(ExtractionJob, job["id"]).status == "failed"
            assert check.scalar(select(func.count()).select_from(AuditLog)
                                .where(AuditLog.action == "extraction.execute.interrupted")) == 1
        assert updates and all("version = ?" in sql.split("WHERE", 1)[1] for sql in updates)
    finally:
        event.remove(local, "before_cursor_execute", observe)
        local.dispose()
