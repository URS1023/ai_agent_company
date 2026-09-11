import json

import pytest
from test_sql_sample_policy import setup

from enterprise_platform.adapters.dashboard_sql_sample_policies import FileSqlSamplePolicies
from enterprise_platform.application.errors import DependencyUnavailable


def write(path, policies):
    path.write_text(
        json.dumps({"schema_version": 1, "policies": [p.model_dump(mode="json") for p in policies]}), encoding="utf-8"
    )


def test_matching_returns_all_exact_candidates_in_stable_order_without_choosing_latest(tmp_path):
    _, draft, _, policy = setup()
    path = tmp_path / "policies.json"
    newer = policy.model_copy(update={"revision": "v2"})
    other = policy.model_copy(update={"policy_id": "another"})
    write(path, [newer, policy, other])
    matches = FileSqlSamplePolicies(path).match(draft, "a")
    assert [(p.policy_id, p.revision) for p in matches] == [
        ("another", "v1"),
        ("device-count", "v1"),
        ("device-count", "v2"),
    ]


@pytest.mark.parametrize("field,value", [("workspace_id", "other"), ("source_id", "other"), ("revision", "other")])
def test_source_scope_is_exact(tmp_path, field, value):
    _, draft, _, policy = setup()
    policy = policy.model_copy(update={"source": policy.source.model_copy(update={field: value})})
    path = tmp_path / "policies.json"
    write(path, [policy])
    assert FileSqlSamplePolicies(path).match(draft, "a") == ()


@pytest.mark.parametrize("field,value", [("schema_revision", "other"), ("proposal_hash", "f" * 64), ("enabled", False)])
def test_disabled_or_different_subject_is_not_a_candidate(tmp_path, field, value):
    _, draft, _, policy = setup()
    path = tmp_path / "policies.json"
    write(path, [policy.model_copy(update={field: value})])
    assert FileSqlSamplePolicies(path).match(draft, "a") == ()


@pytest.mark.parametrize("field,value", [("design_identity", "f" * 64), ("slot_id", "b")])
def test_rules_match_the_exact_fixed_design_slot(tmp_path, field, value):
    _, draft, _, policy = setup()
    path = tmp_path / "policies.json"
    write(path, [policy.model_copy(update={"rules": policy.rules.model_copy(update={field: value})})])
    assert FileSqlSamplePolicies(path).match(draft, "a") == ()


def test_missing_slot_has_no_candidates(tmp_path):
    _, draft, _, policy = setup()
    path = tmp_path / "policies.json"
    write(path, [policy])
    assert FileSqlSamplePolicies(path).match(draft, "unknown") == ()


def test_matching_rereads_configuration_and_never_treats_corruption_as_no_match(tmp_path):
    _, draft, _, policy = setup()
    path = tmp_path / "policies.json"
    write(path, [policy])
    reader = FileSqlSamplePolicies(path)
    assert len(reader.match(draft, "a")) == 1
    write(path, [])
    assert reader.match(draft, "a") == ()
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        reader.match(draft, "a")
