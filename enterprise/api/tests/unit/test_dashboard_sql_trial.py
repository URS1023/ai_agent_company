import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec

import pytest
from test_dashboard_refresh_service import principal, record
from test_dashboard_sql_generation import setup as generation_setup

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_refresh_service import DashboardRepository
from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRecord, SqlDraftRepository
from enterprise_platform.application.dashboard_sql_proposals import SqlProposals
from enterprise_platform.application.dashboard_sql_trial import (
    DashboardSqlTrial,
    SqlTrialAuthorizer,
    SqlTrialCommand,
    SqlTrialExecutor,
    SqlTrialPermit,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput
from enterprise_platform.domain.data_sources import DataSourceError, FrozenRows, PageEvidence, ReadLimits, SourceRef


def setup():
    _, _, schemas, generator, _ = generation_setup()
    draft = SqlDraftRecord(
        draft_id="draft-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        dashboard_id="dashboard-1",
        dashboard_revision=1,
        design_identity=record().design.identity,
        source=SourceRef(workspace_id="workspace-1", source_id="source-1", revision="v1"),
        schema_revision="schema-1",
        prompt="Inspection count",
        proposals=SqlProposals.model_validate_json(generator.generate.return_value),
        created_at=datetime.now(UTC),
    )
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = record()
    drafts = create_autospec(SqlDraftRepository, instance=True)
    drafts.get.return_value = draft
    authorizer = create_autospec(SqlTrialAuthorizer, instance=True)
    permit = SqlTrialPermit(
        actor_id="actor-1",
        workspace_id="workspace-1",
        source=draft.source,
        draft_id=draft.draft_id,
        draft_hash=canonical_hash(draft.model_dump(mode="json")),
        slot_id="a",
        grant_revision="grant-1",
        limits=ReadLimits(max_rows=10, max_bytes=1024, timeout_seconds=2),
        plan_budget=SqlPlanBudget(1000, Decimal("100")),
    )
    authorizer.authorize.return_value = permit
    executor = create_autospec(SqlTrialExecutor, instance=True)

    async def execute(actor, allowed, read):
        return FrozenRows(
            read.source,
            read.read_id,
            read.revision,
            read_fingerprint(read, ()),
            datetime.now(UTC),
            ("count",),
            ((1,),),
            (PageEvidence(1, 1, "a" * 64),),
        )

    executor.execute.side_effect = execute
    command = SqlTrialCommand(expected_revision=1, expected_design_identity=record().design.identity, slot_id="a")
    service = DashboardSqlTrial(repository, drafts, schemas, authorizer, executor)
    return service, repository, drafts, schemas, authorizer, executor, command


def trial(parts, actor=None):
    return asyncio.run(parts[0].run(actor or principal(), "dashboard-1", "draft-1", parts[-1]))


def test_trial_uses_saved_sql_and_rechecks_authorization_without_publishing():
    parts = setup()
    capture = trial(parts)
    _, repository, drafts, schemas, authorizer, executor, _ = parts
    assert capture.rows == ((1,),)
    assert executor.execute.await_count == 1
    assert executor.execute.call_args.args[2].sql == drafts.get.return_value.proposals.slots[0].sql
    assert authorizer.authorize.call_count == 2
    assert schemas.resolve.call_count == 2
    repository.commit.assert_not_called()
    repository.save_bindings.assert_not_called()
    drafts.create.assert_not_called()


def test_readonly_user_is_denied_before_repository_access():
    parts = setup()
    with pytest.raises(AccessDenied):
        trial(parts, principal().model_copy(update={"workspace_role": "normal"}))
    parts[1].get.assert_not_called()
    parts[5].execute.assert_not_called()


def test_trial_requires_explicit_permission_before_external_schema_lookup():
    parts = setup()
    parts[4].authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        trial(parts)
    parts[3].resolve.assert_not_called()
    parts[5].execute.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "other"),
        ("workspace_id", "other"),
        ("draft_id", "other"),
        ("draft_hash", "b" * 64),
        ("slot_id", "other"),
    ],
)
def test_mismatched_trial_permit_never_executes(field, value):
    parts = setup()
    parts[4].authorize.return_value = parts[4].authorize.return_value.model_copy(update={field: value})
    with pytest.raises(AccessDenied):
        trial(parts)
    parts[5].execute.assert_not_called()


@pytest.mark.parametrize("case", ["dashboard", "source", "schema"])
def test_stale_draft_is_rejected_before_execution(case):
    parts = setup()
    if case == "dashboard":
        parts[1].get.return_value = replace(record(), revision=2)
    else:
        field = "source_revision" if case == "source" else "schema_revision"
        parts[3].resolve.return_value = parts[3].resolve.return_value.model_copy(update={field: "changed"})
    with pytest.raises(Conflict):
        trial(parts)
    parts[5].execute.assert_not_called()


def test_permission_revoked_during_read_discards_capture():
    parts = setup()
    parts[4].authorize.side_effect = [parts[4].authorize.return_value, AccessDenied()]
    with pytest.raises(AccessDenied):
        trial(parts)
    assert parts[5].execute.await_count == 1


def test_changed_budget_during_read_discards_capture():
    parts = setup()
    permit = parts[4].authorize.return_value
    parts[4].authorize.side_effect = [permit, permit.model_copy(update={"grant_revision": "changed"})]
    with pytest.raises(Conflict):
        trial(parts)


def test_source_failure_is_opaque_and_not_retried():
    parts = setup()
    parts[5].execute.side_effect = DataSourceError("timeout")
    with pytest.raises(DependencyUnavailable):
        trial(parts)
    assert parts[5].execute.await_count == 1


def test_parent_changed_during_schema_lookup_never_executes():
    parts = setup()
    parts[1].get.side_effect = [record(), replace(record(), revision=2)]
    with pytest.raises(Conflict):
        trial(parts)
    parts[5].execute.assert_not_called()


def test_parent_changed_during_read_discards_capture():
    parts = setup()
    parts[1].get.side_effect = [record(), record(), replace(record(), revision=2)]
    with pytest.raises(Conflict):
        trial(parts)
    assert parts[5].execute.await_count == 1


@pytest.mark.parametrize("field", ["workspace_id", "dashboard_id", "draft_id"])
def test_foreign_draft_never_requests_execution_grant(field):
    parts = setup()
    parts[2].get.return_value = parts[2].get.return_value.model_copy(update={field: "foreign"})
    with pytest.raises(AccessDenied):
        trial(parts)
    parts[4].authorize.assert_not_called()
    parts[5].execute.assert_not_called()


def test_unknown_slot_never_executes():
    parts = setup()
    parts = (*parts[:-1], parts[-1].model_copy(update={"slot_id": "missing"}))
    with pytest.raises(InvalidInput):
        trial(parts)
    parts[4].authorize.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("read_id", "foreign"), ("read_revision", "foreign"), ("read_fingerprint", "b" * 64), ("columns", ("private",))],
)
def test_unrelated_capture_is_not_returned(field, value):
    parts = setup()
    original = parts[5].execute.side_effect

    async def execute(*args):
        return replace(await original(*args), **{field: value})

    parts[5].execute.side_effect = execute
    with pytest.raises(DependencyUnavailable, match="sql_trial_capture_mismatch"):
        trial(parts)


@pytest.mark.parametrize("year", [2000, 3000])
def test_old_or_future_capture_is_not_reported_as_this_trial(year):
    parts = setup()
    original = parts[5].execute.side_effect

    async def execute(*args):
        return replace(await original(*args), captured_at=datetime(year, 1, 1, tzinfo=UTC))

    parts[5].execute.side_effect = execute
    with pytest.raises(DependencyUnavailable, match="sql_trial_capture_mismatch"):
        trial(parts)


def test_saved_sql_referencing_ungranted_columns_never_executes():
    parts = setup()
    draft = parts[2].get.return_value
    slot = draft.proposals.slots[0].model_copy(update={"sql": "SELECT private AS count FROM measurements"})
    draft = draft.model_copy(update={"proposals": SqlProposals(slots=(slot,))})
    parts[2].get.return_value = draft
    parts[4].authorize.return_value = parts[4].authorize.return_value.model_copy(
        update={"draft_hash": canonical_hash(draft.model_dump(mode="json"))}
    )
    with pytest.raises(InvalidInput):
        trial(parts)
    parts[5].execute.assert_not_called()


@pytest.mark.parametrize("values", [((1,),) * 11, (("x" * 1025,),)])
def test_executor_result_exceeding_grant_limits_is_not_returned(values):
    parts = setup()
    original = parts[5].execute.side_effect

    async def execute(*args):
        return replace(await original(*args), rows=values, pages=(PageEvidence(1, len(values), "a" * 64),))

    parts[5].execute.side_effect = execute
    with pytest.raises(DependencyUnavailable, match="sql_trial_capture_limit"):
        trial(parts)


def test_trial_wait_timeout_cancels_executor_without_retrying():
    parts = setup()
    permit = parts[4].authorize.return_value
    parts[4].authorize.return_value = permit.model_copy(update={"limits": ReadLimits(timeout_seconds=0.01)})
    cancelled = []

    async def execute(*args):
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.append(True)

    parts[5].execute.side_effect = execute
    with pytest.raises(DependencyUnavailable, match="sql_trial_read_failed"):
        trial(parts)
    assert cancelled == [True]
    assert parts[5].execute.await_count == 1


@pytest.mark.parametrize("field", ["sql", "source_id", "limits", "plan_budget"])
def test_browser_command_does_not_accept_sql_or_server_grants(field):
    from pydantic import ValidationError

    command = setup()[-1]
    with pytest.raises(ValidationError):
        SqlTrialCommand.model_validate({**command.model_dump(), field: "forged"})


@pytest.mark.parametrize("enabled", [False, True])
def test_dashboard_service_trial_boundary_is_explicit_and_forwards_saved_context(enabled):
    from test_dashboard_sql_trial_result import capture

    from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService
    from enterprise_platform.application.dashboard_service import DashboardService

    parts = setup()
    runner = create_autospec(DashboardSqlTrial, instance=True)
    runner.run.return_value = capture()
    service = DashboardService(
        parts[1], create_autospec(DashboardRefreshService, instance=True), sql_trial=runner if enabled else None
    )
    if enabled:
        result = asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", parts[-1]))
        assert result.row_count == 0
        assert result.status == "trial_only"
        assert result.semantic_status == "not_reviewed"
        assert result.draft_hash == runner.run.return_value.read_revision
        runner.run.assert_awaited_once_with(principal(), "dashboard-1", "draft-1", parts[-1])
    else:
        with pytest.raises(DependencyUnavailable):
            asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", parts[-1]))


def test_dashboard_service_checks_membership_before_trial_availability():
    from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService
    from enterprise_platform.application.dashboard_service import DashboardService

    parts = setup()
    service = DashboardService(parts[1], create_autospec(DashboardRefreshService, instance=True))
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.trial_sql(
                principal().model_copy(update={"workspace_role": "normal"}), "dashboard-1", "draft-1", parts[-1]
            )
        )
