from unittest.mock import create_autospec, patch

import pytest
from test_source_contracts import db_draft
from test_source_service import policy, principal, service

from enterprise_platform.application.dashboard_schema_resolver import (
    DashboardSchemaGrant,
    DashboardSchemaGrants,
    RegisteredSqlSchemaResolver,
    SchemaTableGrant,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.application.source_service import StoredReadCatalog
from enterprise_platform.domain.data_sources import SourceRef


def setup(table_names=("measurements",)):
    app, sources, _, cipher = service()
    draft = SourceDraft.model_validate(db_draft())
    draft = draft.model_copy(update={"connection": draft.connection.model_copy(update={"allowed_tables": table_names})})
    view = app.create_source(principal(), draft, request_key="schema-test")
    grants = create_autospec(DashboardSchemaGrants, instance=True)
    grants.get.return_value = DashboardSchemaGrant(
        source=SourceRef(workspace_id=view.workspace_id, source_id=view.source_id, revision=view.source_revision),
        actor_ids=("actor-1",),
        tables=tuple(SchemaTableGrant(name=name, columns=("device_code", "count")) for name in table_names),
    )
    reads = StoredReadCatalog(sources, cipher, policy(), ImmutableReadCatalog(()))
    resolver = RegisteredSqlSchemaResolver(sources, reads, grants)
    return resolver, grants, view, sources


def test_resolves_current_encrypted_source_and_returns_only_granted_metadata():
    resolver, grants, view, _ = setup()
    with patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader:
        reader.return_value.describe_columns.return_value = (("count", "INTEGER"), ("device_code", "VARCHAR"))
        schema = resolver.resolve(principal("editor"), view.source_id)
    assert schema.actor_id == "actor-1"
    assert schema.source_revision == view.source_revision
    assert [c.name for c in schema.tables[0].columns] == ["count", "device_code"]
    assert len(schema.schema_revision) == 64
    for private in ("fixture-password", "collector.test", "connection_url", "SELECT"):
        assert private not in schema.model_dump_json()
    reader.return_value.close.assert_called_once()
    assert grants.get.call_count == 2


@pytest.mark.parametrize("case", ["disabled", "actor", "workspace", "source", "revision", "table"])
def test_unapproved_schema_never_constructs_network_reader(case):
    resolver, grants, view, _ = setup()
    grant = grants.get.return_value
    if case == "disabled":
        grant = grant.model_copy(update={"enabled": False})
    elif case == "actor":
        grant = grant.model_copy(update={"actor_ids": ("other",)})
    elif case in {"workspace", "source", "revision"}:
        key = {"workspace": "workspace_id", "source": "source_id", "revision": "revision"}[case]
        grant = grant.model_copy(update={"source": grant.source.model_copy(update={key: "other"})})
    else:
        grant = grant.model_copy(update={"tables": (SchemaTableGrant(name="private", columns=("secret",)),)})
    grants.get.return_value = grant
    with patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader:
        with pytest.raises((AccessDenied, Conflict)):
            resolver.resolve(principal("editor"), view.source_id)
        reader.assert_not_called()


def test_permission_denial_precedes_grant_access():
    resolver, grants, view, _ = setup()
    with pytest.raises(AccessDenied):
        resolver.resolve(principal("normal"), view.source_id)
    grants.get.assert_not_called()


def test_revocation_during_metadata_read_discards_result_and_closes_reader():
    resolver, grants, view, _ = setup()
    grants.get.side_effect = [grants.get.return_value, AccessDenied()]
    with patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader:
        reader.return_value.describe_columns.return_value = (("count", "INTEGER"), ("device_code", "VARCHAR"))
        with pytest.raises(AccessDenied):
            resolver.resolve(principal("editor"), view.source_id)
        reader.return_value.close.assert_called_once()


def test_all_tables_share_the_source_metadata_deadline():
    resolver, _, view, _ = setup(("measurements", "quality"))
    now = [1000.0]

    def describe(*args, **kwargs):
        now[0] += 1
        return (("count", "INTEGER"), ("device_code", "VARCHAR"))

    with (
        patch("time.monotonic", side_effect=lambda: now[0]),
        patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader,
    ):
        reader.return_value.describe_columns.side_effect = describe
        schema = resolver.resolve(principal("editor"), view.source_id)

    assert len(schema.tables) == 2
    deadlines = [call.kwargs["deadline"] for call in reader.return_value.describe_columns.call_args_list]
    assert deadlines == [1015.0, 1015.0]
    reader.return_value.close.assert_called_once()


@pytest.mark.parametrize("seconds_per_table,expected_calls", [(15.0, 1), (8.0, 2)])
def test_expired_metadata_budget_discards_partial_schema_without_reading_more_tables(seconds_per_table, expected_calls):
    resolver, grants, view, _ = setup(("measurements", "quality"))
    now = [1000.0]

    def describe(*args, **kwargs):
        now[0] += seconds_per_table
        return (("count", "INTEGER"), ("device_code", "VARCHAR"))

    with (
        patch("time.monotonic", side_effect=lambda: now[0]),
        patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader,
    ):
        reader.return_value.describe_columns.side_effect = describe
        with pytest.raises(DependencyUnavailable, match="dashboard_schema_unavailable"):
            resolver.resolve(principal("editor"), view.source_id)

    assert reader.return_value.describe_columns.call_count == expected_calls
    reader.return_value.close.assert_called_once()
    assert grants.get.call_count == 1


def test_reader_initialization_consumes_the_same_budget():
    resolver, _, view, _ = setup()
    now = [1000.0]
    with (
        patch("time.monotonic", side_effect=lambda: now[0]),
        patch("enterprise_platform.application.dashboard_schema_resolver.DatabaseSourceReader") as reader,
    ):

        def initialize(config):
            now[0] += 15
            return reader.return_value

        reader.side_effect = initialize
        with pytest.raises(DependencyUnavailable):
            resolver.resolve(principal("editor"), view.source_id)
        reader.return_value.describe_columns.assert_not_called()
        reader.return_value.close.assert_called_once()
