import asyncio
from unittest.mock import Mock

import pytest
from test_dashboard_refresh_service import principal
from test_sql_sample_policy import setup as policy_setup
from test_sql_trial_history import fixture

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable


def setup():
    _, parts, store, evidence = fixture()
    draft = parts[2].get.return_value
    policy = policy_setup()[3].model_copy(
        update={
            "source": draft.source,
            "schema_revision": draft.schema_revision,
            "proposal_hash": canonical_hash(draft.proposals.slots[0].model_dump(mode="json")),
            "rules": policy_setup()[3].rules.model_copy(update={"design_identity": evidence.result.design_identity}),
        }
    )
    policies = Mock()
    policies.get.return_value = policy
    trial = DashboardSqlTrial(*parts[1:6], sample_policies=policies)
    service = DashboardService(parts[1], Mock(), sql_trial=trial, sql_trial_evidence=store)
    return service, parts, store, policies, policy


def check(service, actor=None):
    return asyncio.run(
        service.check_sql_trial_policy(actor or principal(), "dashboard-1", "draft-1", "trial-1", "device-count", "v1")
    )


def test_saved_policy_check_retains_provenance_without_execution_or_publish():
    service, parts, store, policies, policy = setup()
    result = check(service)
    assert result.policy_hash == canonical_hash(policy.model_dump(mode="json"))
    assert result.result.status == "sample_constraints_passed"
    assert result.result.semantic_status == "not_reviewed"
    assert policies.get.call_count == 2
    policies.get.assert_called_with("workspace-1", "device-count", "v1")
    parts[5].execute.assert_not_awaited()
    store.create.assert_not_called()
    parts[1].save_bindings.assert_not_called()
    parts[1].commit.assert_not_called()


def test_readonly_actor_never_reads_evidence_or_configuration():
    service, parts, store, policies, policy = setup()
    with pytest.raises(AccessDenied):
        check(service, principal().model_copy(update={"workspace_role": "normal"}))
    store.get.assert_not_called()
    policies.get.assert_not_called()


def test_revoked_source_grant_is_checked_before_configuration():
    service, parts, store, policies, policy = setup()
    parts[4].authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        check(service)
    policies.get.assert_not_called()


@pytest.mark.parametrize("field,value", [("policy_id", "other"), ("revision", "other")])
def test_configuration_reader_receipt_is_exact(field, value):
    service, parts, store, policies, policy = setup()
    policies.get.return_value = policy.model_copy(update={field: value})
    with pytest.raises(Conflict):
        check(service)


@pytest.mark.parametrize("change", ["disable", "rules", "missing", "corrupt"])
def test_configuration_changes_during_check_discard_result(change):
    service, parts, store, policies, policy = setup()
    if change == "disable":
        updated = policy.model_copy(update={"enabled": False})
    elif change == "rules":
        updated = policy.model_copy(update={"rules": policy.rules.model_copy(update={"min_rows": 0})})
    elif change == "missing":
        updated = AccessDenied()
    else:
        updated = DependencyUnavailable()
    policies.get.side_effect = [policy, updated]
    with pytest.raises((AccessDenied, Conflict, DependencyUnavailable)):
        check(service)


def test_grant_revoked_after_check_discards_result():
    service, parts, store, policies, policy = setup()
    permit = parts[4].authorize.return_value
    parts[4].authorize.side_effect = [permit, permit, AccessDenied()]
    with pytest.raises(AccessDenied):
        check(service)


def test_foreign_evidence_is_rejected_before_configuration():
    service, parts, store, policies, policy = setup()
    store.get.return_value = store.get.return_value.model_copy(update={"trial_id": "other"})
    with pytest.raises(AccessDenied):
        check(service)
    policies.get.assert_not_called()


def test_absent_configuration_is_not_implicitly_enabled():
    service, parts, store, evidence = fixture()
    with pytest.raises(DependencyUnavailable, match="sql_sample_policies_unavailable"):
        check(service)
    parts[5].execute.assert_not_awaited()


def test_disabled_configured_policy_does_not_produce_a_success():
    service, parts, store, policies, policy = setup()
    policies.get.return_value = policy.model_copy(update={"enabled": False})
    with pytest.raises(AccessDenied, match="sql_sample_policy_disabled"):
        check(service)


def test_changed_metric_subject_is_not_validated_by_old_configuration():
    service, parts, store, policies, policy = setup()
    policies.get.return_value = policy.model_copy(update={"proposal_hash": "f" * 64})
    with pytest.raises(Conflict, match="sql_sample_policy_subject_mismatch"):
        check(service)


def auto_check(service):
    return asyncio.run(service.auto_check_sql_trial_policy(principal(), "dashboard-1", "draft-1", "trial-1"))


def test_unique_matching_policy_runs_without_caller_selecting_ids():
    service, parts, store, policies, policy = setup()
    policies.match.return_value = (policy,)
    result = auto_check(service)
    assert result.policy_id == "device-count"
    assert result.result.status == "sample_constraints_passed"
    assert policies.match.call_count == 2
    parts[5].execute.assert_not_awaited()
    store.create.assert_not_called()


@pytest.mark.parametrize("count", [0, 2])
def test_absent_or_ambiguous_matches_never_select_arbitrary_policy(count):
    service, parts, store, policies, policy = setup()
    policies.match.return_value = (policy,) * count
    with pytest.raises(Conflict, match="sql_sample_policy_missing" if count == 0 else "sql_sample_policy_ambiguous"):
        auto_check(service)
    policies.get.assert_not_called()


def test_new_ambiguity_during_check_discards_automatic_result():
    service, parts, store, policies, policy = setup()
    policies.match.side_effect = [(policy,), (policy, policy.model_copy(update={"policy_id": "second"}))]
    with pytest.raises(Conflict, match="sql_sample_policy_matches_changed"):
        auto_check(service)


def test_revoked_source_never_discovers_candidate_policies():
    service, parts, store, policies, policy = setup()
    parts[4].authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        auto_check(service)
    policies.match.assert_not_called()
