"""工作台口径回归；仅使用本轮新建SQLite，没有业务数据源或网络请求。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.workspace import quality_overview as overview
from agent_platform.modules.workspace.router import workbench

STAMP = datetime(2026, 10, 20, 17, tzinfo=timezone.utc)


@pytest.fixture
def context(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "overview.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, Actor("reviewer", "owned-workspace", frozenset({"m11.read"}))
        session.rollback()
    engine.dispose()


def add_run(session, *, inspection_id="original", run_id="run-one", stamp=STAMP, workspace="owned-workspace", expected=3):
    inspection = session.get(m.Inspection, inspection_id)
    if inspection is None:
        inspection = m.Inspection(
            id=inspection_id, workspace_id=workspace, name="设备检查", target_type="device",
            template_id="standard", template_revision=1, standard_snapshot={"revision": 1, "metrics": []},
        )
        session.add(inspection)
    run = m.InspectionRun(
        id=run_id, inspection_id=inspection_id, workspace_id=workspace, task_id="task-" + run_id,
        created_at=stamp, result={
            "run_id": run_id, "standard_revision": 1, "expected_samples": expected,
            "actual_samples": 2, "missing_samples": max(0, expected - 2),
            "samples": [{"outcome": "passed", "metrics": []}, {"outcome": "failed", "metrics": []}],
        },
    )
    # 运行必须来自真实授权任务，输入版本与任务固定标准一致，不能靠孤立run绕过授权。
    session.add(Task(
        id=run.task_id, workspace_id=workspace, user_id="reviewer", kind="inspection",
        title=inspection.name, engine="platform", status="waiting_review", access_permission="m11.read",
        inputs={"inspection_id": inspection.id, "template_id": inspection.template_id,
                "template_revision": inspection.template_revision}, outputs=run.result,
        source_run_id=run.id, created_at=stamp.isoformat(),
    ))
    session.add(run)
    session.flush()
    return run


def test_trend_uses_first_run_excludes_reinspection_and_preserves_missing_samples(context):
    session, actor = context
    add_run(session)
    add_run(session, run_id="later-run", stamp=STAMP + timedelta(hours=1), expected=2)
    add_run(session, inspection_id="retest", run_id="retest-run", expected=2)
    session.add(m.InspectionReinspection(
        workspace_id=actor.workspace_id, parent_inspection_id="original", child_inspection_id="retest",
        root_inspection_id="original", source_run_id="run-one", selected_sample_refs=["S1"],
        reason="复检", created_by=actor.user_id,
    ))
    add_run(session, inspection_id="another-tenant", run_id="other", workspace="another-workspace")
    session.flush()
    result = overview.first_inspection_trend(session, actor, now=STAMP)
    assert result["status"] == "ready"
    assert result["items"] == [{
        "date": "2026-10-21", "total": 3, "tasks": 1, "passed": 1,
        "failed": 1, "missing": 1, "pass_rate": "33.33",
    }]


def test_old_first_run_cannot_be_replaced_by_recent_repeat_and_denied_evidence_is_hidden(context, monkeypatch):
    session, actor = context
    add_run(session, stamp=STAMP - timedelta(days=31))
    add_run(session, run_id="recent-repeat")
    add_run(session, inspection_id="hidden", run_id="hidden-run")

    def deny(*args):
        raise DomainError(403, "evidence_denied", "无权访问证据")

    monkeypatch.setattr(overview, "authorize_run", deny)
    assert overview.first_inspection_trend(session, actor, now=STAMP)["items"] == []
    assert overview.first_inspection_trend(session, Actor("user", actor.workspace_id, frozenset()), now=STAMP)["status"] == "unavailable"


def test_overview_does_not_publish_truncated_statistics(context, monkeypatch):
    session, actor = context
    add_run(session)
    add_run(session, inspection_id="second", run_id="second-run")
    monkeypatch.setattr(overview, "MAX_OVERVIEW_RUNS", 1)
    assert overview.first_inspection_trend(session, actor, now=STAMP) == {"items": [], "status": "scope_too_large"}


def test_workbench_todo_excludes_finished_tasks(context):
    session, actor = context
    for status in ["queued", "running", "waiting_review", "succeeded", "failed", "cancelled"]:
        session.add(Task(
            workspace_id=actor.workspace_id, user_id=actor.user_id, kind="inspection",
            title=status, status=status, access_permission="m11.read",
        ))
    session.flush()
    result = workbench(actor, session)
    assert result["counts"]["tasks"] == 3
    assert {item["status"] for item in result["tasks"]} == {"queued", "running", "waiting_review"}


def test_trend_rechecks_original_task_workspace_instead_of_trusting_run_alone(context):
    session, actor = context
    run = add_run(session)
    assert overview.first_inspection_trend(session, actor, now=STAMP)["items"]
    session.get(Task, run.task_id).workspace_id = "another-workspace"
    session.flush()
    assert overview.first_inspection_trend(session, actor, now=STAMP)["items"] == []


def test_fixed_standard_mismatch_remains_an_integrity_error(context):
    session, actor = context
    run = add_run(session)
    task = session.get(Task, run.task_id)
    task.inputs = {**task.inputs, "template_revision": 2}
    session.flush()
    with pytest.raises(DomainError) as error:
        overview.first_inspection_trend(session, actor, now=STAMP)
    assert error.value.code == "inspection_run_evidence_invalid"
