from unittest.mock import create_autospec

import pytest
from pydantic import ValidationError
from test_dashboard_refresh_service import principal
from test_dashboard_sql_trial import setup as trial_setup

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_authorization import (
    RegisteredSqlTrialAuthorizer,
    SqlTrialGrant,
    SqlTrialGrants,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput


def setup():
    parts = trial_setup()
    draft = parts[2].get.return_value
    permit = parts[4].authorize.return_value
    grants = create_autospec(SqlTrialGrants, instance=True)
    grants.get.return_value = SqlTrialGrant(
        source=draft.source,
        actor_ids=("actor-1",),
        dashboard_ids=("dashboard-1",),
        limits=permit.limits,
        plan_budget=permit.plan_budget,
        enabled=True,
    )
    return RegisteredSqlTrialAuthorizer(grants), grants, draft


def test_explicit_grant_produces_a_scoped_budgeted_permit_without_sql():
    authorizer, grants, draft = setup()
    permit = authorizer.authorize(principal(), draft, "a")
    assert permit.actor_id == "actor-1"
    assert permit.workspace_id == draft.workspace_id
    assert permit.source == draft.source
    assert permit.draft_id == draft.draft_id
    assert permit.draft_hash == canonical_hash(draft.model_dump(mode="json"))
    assert permit.grant_revision == canonical_hash(grants.get.return_value.model_dump(mode="json"))
    assert permit.limits == grants.get.return_value.limits
    assert permit.plan_budget == grants.get.return_value.plan_budget
    assert "SELECT" not in permit.model_dump_json()
    grants.get.assert_called_once_with(draft.workspace_id, draft.source.source_id)


def test_readonly_actor_is_denied_before_reading_grant_file():
    authorizer, grants, draft = setup()
    with pytest.raises(AccessDenied):
        authorizer.authorize(principal().model_copy(update={"workspace_role": "normal"}), draft, "a")
    grants.get.assert_not_called()


@pytest.mark.parametrize("changes", [{"enabled": False}, {"actor_ids": ("other",)}, {"dashboard_ids": ("other",)}])
def test_missing_explicit_actor_dashboard_or_enabled_grant_is_denied(changes):
    authorizer, grants, draft = setup()
    grants.get.return_value = grants.get.return_value.model_copy(update=changes)
    with pytest.raises(AccessDenied):
        authorizer.authorize(principal(), draft, "a")


@pytest.mark.parametrize("field", ["workspace_id", "source_id"])
def test_foreign_source_grant_is_denied(field):
    authorizer, grants, draft = setup()
    grant = grants.get.return_value
    grants.get.return_value = grant.model_copy(update={"source": grant.source.model_copy(update={field: "other"})})
    with pytest.raises(AccessDenied):
        authorizer.authorize(principal(), draft, "a")


def test_changed_source_revision_requires_a_new_grant():
    authorizer, grants, draft = setup()
    grant = grants.get.return_value
    grants.get.return_value = grant.model_copy(update={"source": grant.source.model_copy(update={"revision": "v2"})})
    with pytest.raises(Conflict):
        authorizer.authorize(principal(), draft, "a")


def test_unknown_slot_is_not_authorized():
    authorizer, grants, draft = setup()
    with pytest.raises(InvalidInput):
        authorizer.authorize(principal(), draft, "missing")
    grants.get.assert_not_called()


def test_policy_changes_are_reflected_without_cache_or_manual_revision_bump():
    authorizer, grants, draft = setup()
    before = authorizer.authorize(principal(), draft, "a")
    grant = grants.get.return_value
    grants.get.return_value = grant.model_copy(update={"actor_ids": ("actor-1", "actor-2")})
    after = authorizer.authorize(principal(), draft, "a")
    assert after.grant_revision != before.grant_revision
    grants.get.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        authorizer.authorize(principal(), draft, "a")


def test_grants_default_to_disabled():
    _, grants, _ = setup()
    data = grants.get.return_value.model_dump()
    data.pop("enabled")
    assert SqlTrialGrant.model_validate(data).enabled is False


@pytest.mark.parametrize(
    "field,values", [("actor_ids", ()), ("dashboard_ids", ()), ("actor_ids", ("a", "a")), ("dashboard_ids", ("a", "a"))]
)
def test_empty_or_duplicate_allowlists_are_rejected(field, values):
    _, grants, _ = setup()
    with pytest.raises(ValidationError):
        SqlTrialGrant.model_validate({**grants.get.return_value.model_dump(), field: values})
