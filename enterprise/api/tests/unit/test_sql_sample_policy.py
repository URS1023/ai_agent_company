from decimal import Decimal

import pytest
from test_sql_trial_assessment import fixture

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_sample_policy import SqlSamplePolicy, apply_sample_policy
from enterprise_platform.application.dashboard_sql_trial_semantics import NumericRule, SqlSlotSampleRules
from enterprise_platform.application.errors import AccessDenied, Conflict


def setup():
    evidence, draft, design = fixture([{"kind": "integer", "value": "1"}])
    policy = SqlSamplePolicy(
        policy_id="device-count",
        revision="v1",
        source=draft.source,
        schema_revision=draft.schema_revision,
        proposal_hash=canonical_hash(draft.proposals.slots[0].model_dump(mode="json")),
        rules=SqlSlotSampleRules(
            design_identity=design.identity,
            slot_id="a",
            min_rows=1,
            max_rows=1,
            numeric=(NumericRule(field="value", minimum=Decimal(0)),),
        ),
        enabled=True,
    )
    return evidence, draft, design, policy


def test_policy_checks_the_exact_registered_metric_subject_and_retains_provenance():
    evidence, draft, design, policy = setup()
    checked = apply_sample_policy(evidence, draft, design, policy)
    assert checked.policy_id == "device-count"
    assert checked.policy_revision == "v1"
    assert checked.policy_hash == canonical_hash(policy.model_dump(mode="json"))
    assert checked.result.status == "sample_constraints_passed"
    assert checked.result.semantic_status == "not_reviewed"


def test_disabled_policy_cannot_be_used():
    evidence, draft, design, policy = setup()
    with pytest.raises(AccessDenied):
        apply_sample_policy(evidence, draft, design, policy.model_copy(update={"enabled": False}))


@pytest.mark.parametrize(
    "change",
    [
        {"schema_revision": "other"},
        {"proposal_hash": "f" * 64},
    ],
)
def test_wrong_metric_or_schema_policy_is_rejected(change):
    evidence, draft, design, policy = setup()
    with pytest.raises(Conflict):
        apply_sample_policy(evidence, draft, design, policy.model_copy(update=change))


@pytest.mark.parametrize("field,value", [("workspace_id", "other"), ("source_id", "other"), ("revision", "other")])
def test_policy_is_bound_to_the_exact_source(field, value):
    evidence, draft, design, policy = setup()
    with pytest.raises(Conflict):
        apply_sample_policy(
            evidence,
            draft,
            design,
            policy.model_copy(update={"source": policy.source.model_copy(update={field: value})}),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("metric_definition", "Pass rate instead of count"),
        ("time_definition", "Another shift"),
        ("sql", "SELECT 1 AS count"),
        ("field_map", {"value": "other"}),
    ],
)
def test_metric_time_sql_and_mapping_changes_invalidate_policy(field, value):
    evidence, draft, design, policy = setup()
    changed_slot = draft.proposals.slots[0].model_copy(update={field: value})
    changed_hash = canonical_hash(changed_slot.model_dump(mode="json"))
    assert changed_hash != policy.proposal_hash
    changed_draft = draft.model_copy(
        update={"proposals": draft.proposals.model_copy(update={"slots": (changed_slot, *draft.proposals.slots[1:])})}
    )
    with pytest.raises(Conflict, match="sql_sample_policy_subject_mismatch"):
        apply_sample_policy(evidence, changed_draft, design, policy)
