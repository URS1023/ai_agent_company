import asyncio
import json

import pytest
from test_dashboard_refresh_service import principal
from test_dashboard_sql_trial import setup as trial_setup
from test_dashboard_sql_trial_authorization import setup

from enterprise_platform.adapters.dashboard_sql_trial_grants import FileSqlTrialGrants
from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.dashboard_sql_trial_authorization import RegisteredSqlTrialAuthorizer
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable


def document():
    _, grants, _ = setup()
    return {"schema_version": 1, "grants": [json.loads(grants.get.return_value.model_dump_json())]}


def test_reads_current_grants_without_creating_files_or_retaining_revoked_values(tmp_path):
    path = tmp_path / "trial-grants.json"
    reader = FileSqlTrialGrants(path)
    assert not path.exists()
    path.write_text(json.dumps(document()), encoding="utf-8")
    grant = reader.get("workspace-1", "source-1")
    assert grant.enabled is True
    assert grant.limits.max_rows == 10
    assert grant.plan_budget.max_estimated_rows == 1000
    path.write_text('{"schema_version":1,"grants":[]}', encoding="utf-8")
    with pytest.raises(AccessDenied):
        reader.get("workspace-1", "source-1")
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        reader.get("workspace-1", "source-1")


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_source",
        "duplicate_key",
        "version",
        "extra",
        "missing",
        "large",
        "wrong_budget",
        "missing_budget",
        "coerced_enable",
    ],
)
def test_invalid_grant_file_fails_closed_without_private_details(tmp_path, case):
    path = tmp_path / "private-grants.json"
    value = document()
    if case == "duplicate_source":
        value["grants"] *= 2
    elif case == "version":
        value["schema_version"] = 2
    elif case == "extra":
        value["grants"][0]["password"] = "private"
    elif case == "wrong_budget":
        value["grants"][0]["plan_budget"]["max_total_cost"] = "NaN"
    elif case == "missing_budget":
        value["grants"][0].pop("limits")
    elif case == "coerced_enable":
        value["grants"][0]["enabled"] = "true"
    content = json.dumps(value)
    if case == "duplicate_key":
        content = content.replace('"enabled": true', '"enabled": false, "enabled": true')
    elif case == "large":
        content = " " * (1024 * 1024 + 1)
    if case != "missing":
        path.write_text(content, encoding="utf-8")
    with pytest.raises(DependencyUnavailable) as error:
        FileSqlTrialGrants(path).get("workspace-1", "source-1")
    assert "private" not in str(error.value)


def test_grant_does_not_cross_workspaces(tmp_path):
    path = tmp_path / "trial-grants.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    with pytest.raises(AccessDenied):
        FileSqlTrialGrants(path).get("other", "source-1")


def test_metadata_grants_are_not_accepted_as_execution_grants(tmp_path):
    path = tmp_path / "schema-grants.json"
    from test_dashboard_schema_grants import document as schema_document

    path.write_text(json.dumps(schema_document()), encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        FileSqlTrialGrants(path).get("workspace-1", "source-1")


def test_file_authorizer_and_trial_service_complete_only_the_scoped_read(tmp_path):
    path = tmp_path / "trial-grants.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    parts = trial_setup()
    service = DashboardSqlTrial(
        parts[1], parts[2], parts[3], RegisteredSqlTrialAuthorizer(FileSqlTrialGrants(path)), parts[5]
    )

    capture = asyncio.run(service.run(principal(), "dashboard-1", "draft-1", parts[-1]))

    assert capture.rows == ((1,),)
    assert parts[5].execute.await_count == 1
    parts[1].commit.assert_not_called()
    parts[1].save_bindings.assert_not_called()


@pytest.mark.parametrize(
    "case,error", [("revoke", AccessDenied), ("corrupt", DependencyUnavailable), ("budget", Conflict)]
)
def test_file_policy_changes_during_execution_discard_the_trial_capture(tmp_path, case, error):
    path = tmp_path / "trial-grants.json"
    policy = document()
    path.write_text(json.dumps(policy), encoding="utf-8")
    parts = trial_setup()
    original = parts[5].execute.side_effect

    async def execute(*args):
        if case == "revoke":
            policy["grants"] = []
        elif case == "budget":
            policy["grants"][0]["limits"]["max_rows"] = 3
        path.write_text("broken" if case == "corrupt" else json.dumps(policy), encoding="utf-8")
        return await original(*args)

    parts[5].execute.side_effect = execute
    service = DashboardSqlTrial(
        parts[1], parts[2], parts[3], RegisteredSqlTrialAuthorizer(FileSqlTrialGrants(path)), parts[5]
    )

    with pytest.raises(error):
        asyncio.run(service.run(principal(), "dashboard-1", "draft-1", parts[-1]))
    assert parts[5].execute.await_count == 1
    parts[1].commit.assert_not_called()
