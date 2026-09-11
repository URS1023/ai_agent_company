import json

import pytest
from test_sql_sample_policy import setup

from enterprise_platform.adapters.dashboard_sql_sample_policies import FileSqlSamplePolicies
from enterprise_platform.application.dashboard_sql_sample_policy import apply_sample_policy
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable


def document():
    return {"schema_version": 1, "policies": [setup()[3].model_dump(mode="json")]}


def test_current_policy_is_applied_and_disabled_update_is_not_cached(tmp_path):
    path = tmp_path / "policies.json"
    reader = FileSqlSamplePolicies(path)
    assert not path.exists()
    value = document()
    path.write_text(json.dumps(value), encoding="utf-8")
    evidence, draft, design, _ = setup()
    policy = reader.get("workspace-1", "device-count", "v1")
    assert apply_sample_policy(evidence, draft, design, policy).result.status == "sample_constraints_passed"
    value["policies"][0]["enabled"] = False
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AccessDenied, match="sql_sample_policy_disabled"):
        apply_sample_policy(evidence, draft, design, reader.get("workspace-1", "device-count", "v1"))
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        reader.get("workspace-1", "device-count", "v1")


@pytest.mark.parametrize(
    "subject", [("other", "device-count", "v1"), ("workspace-1", "other", "v1"), ("workspace-1", "device-count", "v2")]
)
def test_policy_lookup_is_exact_and_workspace_scoped(tmp_path, subject):
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    with pytest.raises(AccessDenied):
        FileSqlSamplePolicies(path).get(*subject)


@pytest.mark.parametrize(
    "case", ["duplicate", "duplicate_key", "version", "extra", "missing", "large", "coercion", "too_many"]
)
def test_invalid_policy_document_has_opaque_failure(tmp_path, case):
    path = tmp_path / "private-policies.json"
    value = document()
    if case == "duplicate":
        value["policies"] *= 2
    elif case == "version":
        value["schema_version"] = 2
    elif case == "extra":
        value["policies"][0]["private"] = "secret"
    elif case == "coercion":
        value["policies"][0]["enabled"] = "true"
    elif case == "too_many":
        value["policies"] = [dict(value["policies"][0], policy_id=f"p-{i}") for i in range(1025)]
    content = json.dumps(value)
    if case == "duplicate_key":
        content = content.replace('"enabled": true', '"enabled": false, "enabled": true')
    elif case == "large":
        content = " " * (1024 * 1024 + 1)
    if case != "missing":
        path.write_text(content, encoding="utf-8")
    with pytest.raises(DependencyUnavailable, match="^sql_sample_policies_unavailable$"):
        FileSqlSamplePolicies(path).get("workspace-1", "device-count", "v1")


def test_removed_policy_has_no_cached_fallback(tmp_path):
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    reader = FileSqlSamplePolicies(path)
    reader.get("workspace-1", "device-count", "v1")
    path.write_text('{"schema_version":1,"policies":[]}', encoding="utf-8")
    with pytest.raises(AccessDenied):
        reader.get("workspace-1", "device-count", "v1")
