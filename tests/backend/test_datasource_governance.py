"""Data-source health, resource policy and portable configuration contracts.

中文说明：数据源治理：健康检查策略与历史、列表摘要；查询策略强制并发与每日上限；
连接租约对瞬时 MySQL 死锁做有限重试；配置导出先 dry-run、批量导入时凭据安全。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连真实业务库。
边界：导入/导出不得泄露密钥明文；限额与并发为硬约束。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.modules.analytics import governance
from app.modules.analytics.models import (
    DataSourceHealthCheck,
    DataSourcePolicy,
    DataSourceQueryLease,
    QueryHistory,
)
from app.modules.system_tools.models import ScheduledJob
from test_analytics import API, run_sql, source


def test_health_policy_schedule_history_and_list_summary(
    client, administrator, source, db,
):
    initial = client.get(
        API + f"/datasources/{source['id']}/policy", headers=administrator,
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["data"] == {
        **initial.json()["data"],
        "datasourceId": source["id"],
        "version": 1,
        "maxConcurrency": 4,
        "dailyQueryLimit": 10000,
        "healthCheckEnabled": False,
        "healthIntervalMinutes": 60,
        "scheduleStatus": "not_configured",
        "activeQueries": 0,
        "queriesToday": 0,
    }

    saved = client.put(
        API + f"/datasources/{source['id']}/policy",
        headers=administrator,
        json={
            "version": initial.json()["data"]["version"],
            "maxConcurrency": 2,
            "dailyQueryLimit": 20,
            "healthCheckEnabled": True,
            "healthIntervalMinutes": 5,
        },
    )
    assert saved.status_code == 200, saved.text
    policy = saved.json()["data"]
    assert policy["version"] == initial.json()["data"]["version"] + 1
    assert policy["scheduleStatus"] == "active"
    assert policy["healthScheduleId"] and policy["nextHealthCheckAt"]

    checked = client.post(
        API + f"/datasources/{source['id']}/health-checks",
        headers=administrator,
    )
    assert checked.status_code == 200, checked.text
    check = checked.json()["data"]
    assert check["status"] == "healthy" and check["triggerType"] == "manual"
    assert check["datasourceVersion"] == source["version"]
    assert check["latencyMs"] >= 0 and "密码" not in check["detail"]

    history = client.get(
        API + f"/datasources/{source['id']}/health-checks",
        headers=administrator,
    )
    assert history.status_code == 200, history.text
    assert history.json()["data"][0]["id"] == check["id"]

    listed = client.get(API + "/datasources", headers=administrator)
    listed_source = next(
        item for item in listed.json()["data"] if item["id"] == source["id"]
    )
    assert listed_source["healthStatus"] == "healthy"
    assert listed_source["lastHealthCheckAt"] == check["checkedAt"]
    assert listed_source["healthLatencyMs"] == check["latencyMs"]

    persisted_policy = db.scalar(select(DataSourcePolicy).where(
        DataSourcePolicy.datasource_id == source["id"],
    ))
    schedule = db.get(ScheduledJob, policy["healthScheduleId"])
    assert persisted_policy is not None
    assert schedule is not None and schedule.task_type == "datasource_health"
    assert schedule.target_id == source["id"] and schedule.status == "active"
    assert db.scalar(select(func.count()).select_from(DataSourceHealthCheck).where(
        DataSourceHealthCheck.datasource_id == source["id"],
    )) == 1

    scheduled_run = client.post(
        f"/api/v1/system-tools/jobs/{schedule.id}/run",
        headers=administrator,
        json={"version": schedule.version, "idempotencyKey": "source-health-scheduled-1"},
    )
    assert scheduled_run.status_code == 200, scheduled_run.text
    assert scheduled_run.json()["status"] == "completed"
    db.expire_all()
    triggers = db.scalars(select(DataSourceHealthCheck.trigger_type).where(
        DataSourceHealthCheck.datasource_id == source["id"],
    )).all()
    assert sorted(triggers) == ["manual", "schedule"]


def test_query_policy_enforces_concurrency_and_daily_limit(
    client, administrator, source, db,
):
    saved = client.put(
        API + f"/datasources/{source['id']}/policy",
        headers=administrator,
        json={
            "version": client.get(
                API + f"/datasources/{source['id']}/policy", headers=administrator,
            ).json()["data"]["version"],
            "maxConcurrency": 1,
            "dailyQueryLimit": 1,
            "healthCheckEnabled": False,
            "healthIntervalMinutes": 60,
        },
    )
    assert saved.status_code == 200, saved.text

    lease = DataSourceQueryLease(
        workspace_id=administrator["X-Workspace-Id"],
        datasource_id=source["id"],
        user_id="parallel-query",
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(lease)
    db.commit()
    busy = run_sql(client, administrator, source)
    assert busy.status_code == 429, busy.text
    assert busy.json()["code"] == "datasource_busy"
    assert db.scalar(select(func.count()).select_from(QueryHistory).where(
        QueryHistory.datasource_id == source["id"],
    )) == 0

    status = client.get(
        API + f"/datasources/{source['id']}/pool-status", headers=administrator,
    )
    assert status.status_code == 200, status.text
    resource = status.json()["data"]
    assert resource["poolType"] == "NullPool"
    assert resource["activeQueries"] == 1
    assert resource["maxConcurrency"] == 1
    assert resource["usageRate"] == 100

    db.delete(lease)
    db.commit()
    first = run_sql(client, administrator, source)
    assert first.status_code == 200, first.text
    assert db.scalar(select(func.count()).select_from(DataSourceQueryLease).where(
        DataSourceQueryLease.datasource_id == source["id"],
    )) == 0

    limited = run_sql(client, administrator, source)
    assert limited.status_code == 429, limited.text
    assert limited.json()["code"] == "datasource_daily_limit"
    status = client.get(
        API + f"/datasources/{source['id']}/pool-status", headers=administrator,
    ).json()["data"]
    assert status["queriesToday"] == 1 and status["dailyQueryLimit"] == 1


def test_query_lease_retries_a_transient_mysql_deadlock(
    client, administrator, source, db, monkeypatch,
):
    original = governance._reserve_query_lease
    calls = 0

    def transient_once(ctx, source_id, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError(
                "INSERT INTO analytics_datasource_query_leases ...",
                {},
                RuntimeError(1213, "Deadlock found when trying to get lock"),
            )
        return original(ctx, source_id, timeout)

    monkeypatch.setattr(governance, "_reserve_query_lease", transient_once)
    response = run_sql(client, administrator, source)
    assert response.status_code == 200, response.text
    assert calls == 2
    assert db.scalar(select(func.count()).select_from(DataSourceQueryLease).where(
        DataSourceQueryLease.datasource_id == source["id"],
    )) == 0


def test_configuration_export_dry_run_and_credential_safe_batch_import(
    client, administrator, source, db,
):
    exported = client.get(
        API + "/datasources/config-export", headers=administrator,
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/json")
    assert "attachment" in exported.headers["content-disposition"]
    bundle = exported.json()
    assert bundle["formatVersion"] == 1 and len(bundle["items"]) == 1
    serialized = exported.text.lower()
    assert "password" not in serialized and "secret_id" not in serialized
    assert bundle["items"][0]["name"] == source["name"]

    payload = {
        **bundle,
        "conflictMode": "rename",
        "dryRun": True,
    }
    preview = client.post(
        API + "/datasources/config-import", headers=administrator, json=payload,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["dryRun"] is True
    assert preview.json()["data"]["created"] == 1
    assert preview.json()["data"]["renamed"] == 1
    assert len(client.get(API + "/datasources", headers=administrator).json()["data"]) == 1

    payload["dryRun"] = False
    imported = client.post(
        API + "/datasources/config-import", headers=administrator, json=payload,
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()["data"]
    assert result["created"] == 1 and result["updated"] == 0
    assert result["items"][0]["name"].startswith(source["name"] + "（导入")

    sources = client.get(API + "/datasources", headers=administrator).json()["data"]
    assert len(sources) == 2
    copied = next(item for item in sources if item["id"] != source["id"])
    assert copied["hasPassword"] is False
    assert copied["databaseName"] == source["databaseName"]
    assert copied["metadataStatus"] == "ready"
    assert db.scalar(select(DataSourcePolicy).where(
        DataSourcePolicy.datasource_id == copied["id"],
    )) is not None
