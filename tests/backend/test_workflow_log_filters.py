"""Log filters must search beyond the first page and agree with calendar metrics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.workflows import execution
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.periods import period_bounds
from app.errors import PermissionErrorEx
from test_workflows import API, create_flow, execute, graph, result


def clone_execution(db, source, when, **changes):
    values = {key: deepcopy(getattr(source, key)) for key in (
        "workspace_id", "owner_user_id", "workflow_id", "revision", "run_id", "request_hash",
        "snapshot", "input_json", "result_json", "status", "error_message",
    )}
    values.update(changes)
    row = WorkflowExecution(**values, created_at=when, idempotency_key=f"log-{when.isoformat()}")
    db.add(row)
    return row


def test_filters_find_old_logs_beyond_200_and_treat_wildcards_literally(client, administrator, db):
    flow = create_flow(client, administrator, graph())
    seed = db.get(WorkflowExecution, result(execute(client, administrator, flow))["id"])
    for index in range(225):
        clone_execution(db, seed, seed.created_at - timedelta(minutes=index + 1),
                        status="failed" if index == 224 else "completed",
                        error_message="中文字段_% 不存在" if index == 224 else "")
    db.commit()
    url = API + f"/{flow['id']}/runs"
    first = result(client.get(url, headers=administrator, params={"limit": 200}))
    assert len(first) == 200 and all(row["status"] == "completed" for row in first)
    for term in ("中文", "%", "_"):
        found = result(client.get(url, headers=administrator, params={"keyword": term, "status": "failed"}))
        assert len(found) == 1 and found[0]["errorMessage"] == "中文字段_% 不存在"
    assert not result(client.get(url, headers=administrator, params={"keyword": "中文", "status": "completed"}))
    older = result(client.get(url, headers=administrator, params={"offset": 200, "limit": 50}))
    assert len(older) == 26 and not {row["id"] for row in older} & {row["id"] for row in first}
    assert len(result(client.get(url, headers=administrator, params={"keyword": "V1", "limit": 200}))) == 200


def test_today_logs_and_monitor_share_browser_calendar_boundaries(client, administrator, db, monkeypatch):
    now = datetime(2026, 9, 8, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(execution, "period_bounds", lambda days, zone: period_bounds(days, zone, now=now))
    flow = create_flow(client, administrator, graph())
    seed = db.get(WorkflowExecution, result(execute(client, administrator, flow))["id"])
    seed.created_at = datetime(2026, 9, 7, 15, 59)
    for when in (datetime(2026, 9, 7, 16), datetime(2026, 9, 7, 23, 59), datetime(2026, 9, 8, 3, 59), datetime(2026, 9, 8, 4, 1)):
        clone_execution(db, seed, when)
    db.commit()
    params = {"days": 1, "timeZone": "Asia/Shanghai"}
    logs = result(client.get(API + f"/{flow['id']}/runs", headers=administrator, params=params))
    metrics = result(client.get(API + f"/{flow['id']}/monitor", headers=administrator, params=params))
    assert len(logs) == metrics["totals"]["runs"] == metrics["series"][0]["runs"] == 3
    assert metrics["start"] == metrics["end"] == "2026-09-08"
    assert metrics["timeZone"] == "Asia/Shanghai"
    utc_logs = result(client.get(API + f"/{flow['id']}/runs", headers=administrator, params={"days": 1}))
    assert len(utc_logs) == 1


@pytest.mark.parametrize("now, hours", [
    (datetime(2026, 3, 9, 3, 59, 59, tzinfo=timezone.utc), 23),
    (datetime(2026, 11, 2, 4, 59, 59, tzinfo=timezone.utc), 25),
])
def test_calendar_day_handles_daylight_saving(now, hours):
    _, _, _, start, end = period_bounds(1, "America/New_York", now=now)
    assert end - start == timedelta(hours=hours, seconds=-1)


def test_invalid_log_filters_are_rejected(client, administrator):
    flow = create_flow(client, administrator, graph())
    url = API + f"/{flow['id']}"
    for route in ("/runs", "/monitor"):
        assert client.get(url + route, headers=administrator, params={"timeZone": "invalid/zone"}).status_code == 422
    assert client.get(url + "/runs", headers=administrator, params={"status": "made_up"}).status_code == 422


def test_pagination_counts_only_currently_authorized_snapshots(client, administrator, db, monkeypatch):
    flow = create_flow(client, administrator, graph())
    seed = db.get(WorkflowExecution, result(execute(client, administrator, flow))["id"])
    expected = []
    for index in range(105):
        snapshot = deepcopy(seed.snapshot)
        hidden = index < 102
        snapshot["graph"]["nodes"][0]["title"] = "失去来源权限" if hidden else "正常来源"
        row = clone_execution(db, seed, seed.created_at - timedelta(minutes=index + 1), snapshot=snapshot)
        if not hidden:
            expected.append(row)
    db.commit()
    original = execution.authorize_graph
    def check(ctx, value):
        if value.nodes[0].title == "失去来源权限":
            raise PermissionErrorEx("来源权限已撤销")
        original(ctx, value)
    monkeypatch.setattr(execution, "authorize_graph", check)
    rows = result(client.get(API + f"/{flow['id']}/runs", headers=administrator, params={"offset": 1, "limit": 2}))
    assert [row["id"] for row in rows] == [row.id for row in expected[:2]]
