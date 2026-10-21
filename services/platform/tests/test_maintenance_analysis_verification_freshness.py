"""仅用新临时SQLite/CSV核验维修分析观测新鲜度，不接业务库或网络。"""
import csv
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Organization, Role, TaskEvent, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.data import models as data_models
from agent_platform.modules.data.service import DataService
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.maintenance_work import MaintenanceWorkService
from agent_platform.modules.manufacturing.schemas import AnalysisInput
from agent_platform.modules.manufacturing.service import ManufacturingService
from agent_platform.modules.manufacturing.workflow import as_utc


@pytest.fixture
def repaired(tmp_path, monkeypatch):
    """独立维修人员提交维修，另一人员复核；固定当前时间避免临界用例漂移。"""
    engine = create_engine("sqlite:///" + (tmp_path / "freshness.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    settings = Settings(data_dir=tmp_path / "new-imports")
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(m, "now", lambda: now)
    with Session(engine) as session:
        session.add_all([Organization(id="org", name="隔离工厂"), Workspace(id="ws", organization_id="org", name="检修车间")])
        role = Role(id="manufacturing-role", workspace_id="ws", name="维修复核", permissions=["m10.*", "m06.*"])
        session.add(role)
        for user_id in ("operator", "reviewer"):
            session.add(User(id=user_id, organization_id="org", username=user_id, display_name=user_id, password_hash="unused-test-hash", is_admin=user_id == "operator"))
            session.add(Membership(workspace_id="ws", user_id=user_id, role_ids=[role.id]))
        session.flush()
        actor = Actor("operator", "ws", frozenset({"*"}), ("admin",), "org", True)
        reviewer = Actor("reviewer", "ws", frozenset({"m10.*", "m06.*"}), (), "org")
        service = ManufacturingService(session, actor)
        device = service.create(m.Device, {"name": "同工况循环泵"})
        original = service.create_analysis(AnalysisInput(device_id=device["id"], rows=observations(now - timedelta(days=2), recent_value=20)), settings)
        work = MaintenanceWorkService(session, actor)
        order = work.create_order("analysis", original["id"], {"revision": original["revision"], "name": "指标变化维修", "note": "现场检查温升原因"})
        for action, fields in (("assign", {"assignee_id": actor.user_id}), ("start", {}), ("repair", {"root_cause": "过滤器堵塞"})):
            order = work.action(order["id"], {"revision": order["revision"], "action": action, "note": "已记录检查与维修", **fields})
        yield session, service, MaintenanceWorkService(session, reviewer), order, settings, now
        session.rollback()
    engine.dispose()


def observations(recent_start, *, recent_value=10, explicit=True):
    """基线保留历史窗口，近期五条使用独立时间，避免重复观测被去重。"""
    return [{"timestamp": ((recent_start - timedelta(days=1) + timedelta(seconds=i)) if i < 5 else recent_start + timedelta(seconds=i - 5)).isoformat(),
             "period": ("baseline" if i < 5 else "recent") if explicit else "",
             "load": 50, "value": 10 if i < 5 else recent_value} for i in range(10)]


def candidate(context, rows, *, direct=False):
    session, service, _, order, settings, _ = context
    config = {"device_id": order["device_id"]}
    if direct:
        config["rows"] = rows
    else:
        content = StringIO()
        writer = csv.DictWriter(content, fieldnames=["timestamp", "period", "load", "value"])
        writer.writeheader()
        writer.writerows(rows)
        dataset = DataService(session, service.actor, settings).import_csv({"name": "本次维修复测", "csv_text": content.getvalue()})
        config.update(datasource_id=dataset["datasource_id"], sql=f'SELECT * FROM "{dataset["table_name"]}"')
    analysis = service.create_analysis(AnalysisInput(**config), settings)
    assert analysis["result"]["status"] == "normal"
    return service.get(m.MaintenanceAnalysis, analysis["id"])


def review(context, analysis):
    _, _, work, order, _, _ = context
    return work.action(order["id"], {"revision": order["revision"], "action": "verify", "note": "核对维修后原始观测", "verification_analysis_id": analysis.id})


def reject_unchanged(context, analysis, expected_code):
    """失败不能推进工单、任务或追加一条成功复核事件。"""
    session, _, work, order, _, _ = context
    task = deepcopy(TaskService(session, work.actor).get(order["task_id"]))
    event_count = session.scalar(select(func.count()).select_from(m.MaintenanceWorkEvent))
    task_events = session.scalar(select(func.count()).select_from(TaskEvent))
    with pytest.raises(DomainError) as error:
        review(context, analysis)
    assert error.value.code == expected_code
    row = session.get(m.MaintenanceWorkOrder, order["id"])
    assert row.status == "awaiting_review" and row.revision == order["revision"]
    assert row.verification_snapshot == {} and row.verified_by is None
    assert TaskService(session, work.actor).get(order["task_id"]) == task
    assert session.scalar(select(func.count()).select_from(m.MaintenanceWorkEvent)) == event_count
    assert session.scalar(select(func.count()).select_from(TaskEvent)) == task_events


def test_old_window_recomputed_after_repair_cannot_close(repaired):
    analysis = candidate(repaired, observations(repaired[-1] - timedelta(hours=1)))
    assert as_utc(analysis.created_at) >= repaired[-1]
    reject_unchanged(repaired, analysis, "verification_observation_old")


def test_one_old_recent_row_cannot_hide_behind_latest_observation(repaired):
    rows = observations(repaired[-1] + timedelta(seconds=1))
    rows[5]["timestamp"] = (repaired[-1] - timedelta(seconds=1)).isoformat()
    reject_unchanged(repaired, candidate(repaired, rows), "verification_observation_old")


@pytest.mark.parametrize("offset,accepted", [(timedelta(minutes=5), True), (timedelta(minutes=5, seconds=1), False)])
def test_five_minute_future_boundary(repaired, offset, accepted):
    rows = observations(repaired[-1] + timedelta(seconds=1))
    rows[-1]["timestamp"] = (repaired[-1] + offset).isoformat()
    analysis = candidate(repaired, rows)
    if accepted:
        assert review(repaired, analysis)["status"] == "resolved"
    else:
        reject_unchanged(repaired, analysis, "verification_observation_future")


def test_recent_time_requires_timezone(repaired):
    rows = observations(repaired[-1] + timedelta(seconds=1))
    for item in rows[5:]:
        item["timestamp"] = item["timestamp"].removesuffix("+00:00")
    reject_unchanged(repaired, candidate(repaired, rows), "invalid_time")


def test_imported_arrays_do_not_substitute_for_saved_source(repaired):
    reject_unchanged(repaired, candidate(repaired, observations(repaired[-1]), direct=True), "verification_source_required")


@pytest.mark.parametrize("defect,expected", [
    ("truncated", "verification_mapping_invalid"),
    ("missing_column", "verification_mapping_invalid"),
    ("empty_time", "verification_time_invalid"),
    ("parameters", "verification_source_mismatch"),
    ("datasource", "verification_source_mismatch"),
    ("sql", "verification_source_mismatch"),
])
def test_incomplete_or_mismatched_saved_evidence_fails_closed(repaired, defect, expected):
    session, *_ = repaired
    analysis = candidate(repaired, observations(repaired[-1]))
    query = session.get(data_models.QueryRecord, analysis.provenance["query_id"])
    if defect in {"parameters", "datasource", "sql"}:
        field, value = {"parameters": ("parameters", {"unexpected": 1}), "datasource": ("datasource_id", "other-source"), "sql": ("sql", "SELECT 1")}[defect]
        analysis.request_snapshot = {**analysis.request_snapshot, field: value}
    else:
        result = deepcopy(query.result)
        if defect == "truncated":
            result["truncated"] = True
        elif defect == "missing_column":
            result["columns"].remove("timestamp")
        else:
            result["rows"][5]["timestamp"] = ""
        query.result = result
    session.flush()
    reject_unchanged(repaired, analysis, expected)


def test_current_query_permission_is_rechecked_without_new_sql(repaired, monkeypatch):
    analysis = candidate(repaired, observations(repaired[-1]))
    original = DataService.get_query
    def deny_source(self, query_id):
        if query_id == analysis.provenance["query_id"]:
            raise DomainError(403, "query_scope_denied", "该历史结果不在当前数据权限范围")
        return original(self, query_id)
    monkeypatch.setattr(DataService, "get_query", deny_source)
    reject_unchanged(repaired, analysis, "query_scope_denied")


@pytest.mark.parametrize("explicit", [True, False])
def test_historical_baseline_with_post_repair_recent_closes_without_query_execution(repaired, monkeypatch, explicit):
    analysis = candidate(repaired, observations(repaired[-1], explicit=explicit))
    # 保留查询意图：仅空白/注释差异不应被当成换源。
    analysis.request_snapshot = {**analysis.request_snapshot, "sql": "\n/* 本次复测 */ " + analysis.request_snapshot["sql"] + "\n"}
    repaired[0].flush()
    def unexpected_query(*args, **kwargs):
        pytest.fail("维修复核不得重新执行业务SQL")
    monkeypatch.setattr(DataService, "execute_query", unexpected_query)
    closed = review(repaired, analysis)
    evidence = closed["verification_snapshot"]["observation_evidence"]
    assert evidence["recent_count"] == 5
    assert evidence["recent_start_at"] == repaired[-1].isoformat()
    assert evidence["recent_end_at"] == (repaired[-1] + timedelta(seconds=4)).isoformat()
    assert evidence["period_selection"] == ("explicit" if explicit else "time_midpoint")
    assert closed["status"] == "resolved"
    assert TaskService(repaired[0], repaired[2].actor).get(closed["task_id"])["status"] == "succeeded"


def test_ambiguous_automatic_period_with_deduplication_requires_explicit_period(repaired):
    rows = observations(repaired[-1], explicit=False)
    rows.append(deepcopy(rows[0]))
    analysis = candidate(repaired, rows)
    assert analysis.result["duplicate_count"] == 1
    reject_unchanged(repaired, analysis, "verification_period_ambiguous")
