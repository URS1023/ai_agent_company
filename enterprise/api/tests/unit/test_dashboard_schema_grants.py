import json

import pytest

from enterprise_platform.adapters.dashboard_schema_grants import FileDashboardSchemaGrants
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable


def document():
    return {
        "schema_version": 1,
        "grants": [
            {
                "source": {"workspace_id": "workspace-1", "source_id": "source-1", "revision": "v1"},
                "actor_ids": ["actor-1"],
                "tables": [{"name": "measurements", "columns": ["count"]}],
                "enabled": True,
            }
        ],
    }


def test_file_lookup_reads_current_bytes_and_does_not_fall_back_after_revocation(tmp_path):
    path = tmp_path / "grants.json"
    reader = FileDashboardSchemaGrants(path)
    assert not path.exists()
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert reader.get("workspace-1", "source-1").tables[0].columns == ("count",)
    path.write_text('{"schema_version":1,"grants":[]}', encoding="utf-8")
    with pytest.raises(AccessDenied):
        reader.get("workspace-1", "source-1")
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        reader.get("workspace-1", "source-1")


@pytest.mark.parametrize("case", ["duplicates", "extra", "version", "oversized", "duplicate_key", "missing"])
def test_invalid_authority_file_is_unavailable_without_exposing_path(tmp_path, case):
    path = tmp_path / "private-grants.json"
    value = document()
    if case == "duplicates":
        value["grants"] *= 2
    elif case == "extra":
        value["grants"][0]["password"] = "private"
    elif case == "version":
        value["schema_version"] = 2
    text = json.dumps(value)
    if case == "oversized":
        text = " " * (1024 * 1024 + 1)
    elif case == "duplicate_key":
        text = text.replace('"enabled": true', '"enabled": false, "enabled": true')
    if case != "missing":
        path.write_text(text, encoding="utf-8")
    with pytest.raises(DependencyUnavailable) as error:
        FileDashboardSchemaGrants(path).get("workspace-1", "source-1")
    assert "private" not in str(error.value)


def test_other_workspace_has_no_grant(tmp_path):
    path = tmp_path / "grants.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    with pytest.raises(AccessDenied):
        FileDashboardSchemaGrants(path).get("workspace-other", "source-1")
