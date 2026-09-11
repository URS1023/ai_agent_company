import asyncio
from dataclasses import replace

import pytest
from test_dashboard_refresh_service import principal, record
from test_dashboard_sql_generation import generate, setup

from enterprise_platform.application.errors import AccessDenied


def fixture():
    service, repository, schemas, generator, command = setup()
    result = generate(service, command)
    draft = service._drafts.create.call_args.args[0]
    service._drafts.get.return_value = draft
    service._drafts.reset_mock()
    repository.reset_mock()
    schemas.reset_mock()
    generator.reset_mock()
    return service, repository, schemas, generator, result.draft_id


def inspect(service, draft_id, actor=None):
    return asyncio.run(service.inspect_draft(actor or principal(), "dashboard-1", draft_id))


def test_reads_saved_draft_without_generating_saving_or_executing_again():
    service, _, schemas, generator, draft_id = fixture()
    result = inspect(service, draft_id)
    assert result.draft.draft_id == draft_id
    assert not result.requires_regeneration
    assert result.current_dashboard_revision == 1
    assert schemas.resolve.call_count == 1
    service._drafts.get.assert_called_once_with("workspace-1", draft_id)
    service._drafts.create.assert_not_called()
    generator.generate.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dashboard", "source", "schema"])
def test_changed_context_marks_draft_stale_instead_of_rewriting_it(kind):
    service, repository, schemas, _, draft_id = fixture()
    if kind == "dashboard":
        repository.get.return_value = replace(record(), revision=2)
    else:
        field = "source_revision" if kind == "source" else "schema_revision"
        schemas.resolve.return_value = schemas.resolve.return_value.model_copy(update={field: "new"})
    result = inspect(service, draft_id)
    assert result.requires_regeneration
    assert result.draft.dashboard_revision == 1
    assert result.draft.source.revision == "v1"
    service._drafts.create.assert_not_called()


def test_read_only_user_has_no_storage_or_schema_access():
    service, repository, schemas, _, draft_id = fixture()
    with pytest.raises(AccessDenied):
        inspect(service, draft_id, principal().model_copy(update={"workspace_role": "normal"}))
    service._drafts.get.assert_not_called()
    repository.get.assert_not_called()
    schemas.resolve.assert_not_called()


def test_dashboard_changed_during_schema_lookup_is_reported_stale():
    service, repository, _, _, draft_id = fixture()
    repository.get.side_effect = [record(), replace(record(), revision=2)]
    result = inspect(service, draft_id)
    assert result.requires_regeneration
    assert result.current_dashboard_revision == 2


@pytest.mark.parametrize("field", ["workspace_id", "dashboard_id", "draft_id"])
def test_mismatched_stored_scope_is_never_disclosed(field):
    service, _, schemas, _, draft_id = fixture()
    service._drafts.get.return_value = service._drafts.get.return_value.model_copy(update={field: "other"})
    with pytest.raises(AccessDenied):
        inspect(service, draft_id)
    schemas.resolve.assert_not_called()


def test_revoked_source_permission_blocks_old_draft_disclosure():
    service, _, schemas, _, draft_id = fixture()
    schemas.resolve.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        inspect(service, draft_id)


def test_removed_column_grant_blocks_old_sql_disclosure():
    from enterprise_platform.application.dashboard_sql_generation import SqlSchemaColumn, SqlSchemaTable

    service, _, schemas, _, draft_id = fixture()
    schemas.resolve.return_value = schemas.resolve.return_value.model_copy(
        update={
            "tables": (
                SqlSchemaTable(name="measurements", columns=(SqlSchemaColumn(name="other", sql_type="integer"),)),
            ),
        }
    )
    with pytest.raises(AccessDenied):
        inspect(service, draft_id)
