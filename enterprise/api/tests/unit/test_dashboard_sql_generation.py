import asyncio
import json
from dataclasses import replace
from unittest.mock import create_autospec

import pytest
from test_dashboard_refresh_service import principal, record

from enterprise_platform.application.dashboard_refresh_service import DashboardRepository
from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRepository
from enterprise_platform.application.dashboard_sql_generation import (
    AuthorizedSqlSchema,
    DashboardSqlGeneration,
    GenerateSqlCommand,
    SqlDraftGenerator,
    SqlSchemaColumn,
    SqlSchemaResolver,
    SqlSchemaTable,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, PersistenceError


def setup():
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = record()
    schemas = create_autospec(SqlSchemaResolver, instance=True)
    schemas.resolve.return_value = AuthorizedSqlSchema(
        workspace_id="workspace-1",
        actor_id="actor-1",
        source_id="source-1",
        source_revision="v1",
        schema_revision="schema-1",
        dialect="postgresql",
        tables=(SqlSchemaTable(name="measurements", columns=(SqlSchemaColumn(name="count", sql_type="integer"),)),),
    )
    generator = create_autospec(SqlDraftGenerator, instance=True)
    generator.generate.return_value = json.dumps(
        {
            "slots": [
                {
                    "slot_id": "a",
                    "sql": "SELECT count FROM measurements",
                    "field_map": {"value": "count"},
                    "metric_definition": "Number of inspections",
                    "time_definition": "All retained records",
                }
            ]
        }
    )
    command = GenerateSqlCommand(
        expected_revision=1,
        expected_design_identity=record().design.identity,
        source_id="source-1",
        prompt="Show inspection count",
    )
    drafts = create_autospec(SqlDraftRepository, instance=True)
    drafts.create.side_effect = lambda record: record
    return DashboardSqlGeneration(repository, schemas, generator, drafts), repository, schemas, generator, command


def generate(service, command, actor=None):
    return asyncio.run(service.generate(actor or principal(), "dashboard-1", command))


def test_generation_uses_only_authorized_schema_and_slots_without_saving_or_running_queries():
    service, repository, schemas, generator, command = setup()
    result = generate(service, command)
    request = generator.generate.call_args.args[1]
    assert request.prompt == command.prompt
    assert request.tables == schemas.resolve.return_value.tables
    assert request.slots == record().design.slots
    assert set(request.model_dump()) == {"prompt", "dialect", "tables", "slots"}
    assert result.dashboard_id == "dashboard-1"
    assert result.source_revision == "v1"
    assert result.proposals.slots[0].field_map == {"value": "count"}
    assert schemas.resolve.call_count == 2
    assert repository.get.call_count == 2
    repository.save_bindings.assert_not_called()
    repository.commit.assert_not_called()
    saved = service._drafts.create.call_args.args[0]
    assert saved.draft_id == result.draft_id
    assert saved.prompt == command.prompt
    assert saved.proposals == result.proposals


def test_read_only_actor_does_not_read_schema_or_call_model():
    service, repository, schemas, generator, command = setup()
    with pytest.raises(AccessDenied):
        generate(service, command, principal().model_copy(update={"workspace_role": "normal"}))
    repository.get.assert_not_called()
    schemas.resolve.assert_not_called()
    generator.generate.assert_not_called()


@pytest.mark.parametrize("field,value", [("workspace_id", "other"), ("actor_id", "other"), ("source_id", "other")])
def test_mismatched_schema_scope_never_reaches_model(field, value):
    service, _, schemas, generator, command = setup()
    schemas.resolve.return_value = schemas.resolve.return_value.model_copy(update={field: value})
    with pytest.raises(AccessDenied):
        generate(service, command)
    generator.generate.assert_not_called()


def test_stale_dashboard_does_not_call_model():
    service, _, schemas, generator, command = setup()
    with pytest.raises(Conflict):
        generate(service, command.model_copy(update={"expected_revision": 2}))
    schemas.resolve.assert_not_called()
    generator.generate.assert_not_called()


@pytest.mark.parametrize("change", ["schema", "dashboard", "revoked"])
def test_changes_during_generation_discard_draft(change):
    service, repository, schemas, _, command = setup()
    schema = schemas.resolve.return_value
    if change == "schema":
        schemas.resolve.side_effect = [schema, schema.model_copy(update={"schema_revision": "schema-2"})]
    elif change == "revoked":
        schemas.resolve.side_effect = [schema, AccessDenied()]
    else:
        repository.get.side_effect = [record(), replace(record(), revision=2)]
    with pytest.raises((Conflict, AccessDenied)):
        generate(service, command)
    repository.save_bindings.assert_not_called()


def test_invalid_model_output_never_becomes_a_draft_receipt():
    service, _, _, generator, command = setup()
    generator.generate.return_value = '{"style": "red"}'
    with pytest.raises(InvalidInput):
        generate(service, command)


def test_storage_failure_does_not_return_an_unsaved_draft_or_repeat_generation():
    service, _, _, generator, command = setup()
    service._drafts.create.side_effect = PersistenceError()
    with pytest.raises(PersistenceError):
        generate(service, command)
    generator.generate.assert_awaited_once()


def test_storage_receipt_must_match_the_exact_generated_snapshot():
    service, _, _, _, command = setup()
    service._drafts.create.side_effect = lambda record: record.model_copy(update={"actor_id": "other"})
    with pytest.raises(PersistenceError, match="sql_draft_receipt_mismatch"):
        generate(service, command)


@pytest.mark.parametrize(
    "sql", ["DELETE FROM measurements", "SELECT secret AS count FROM measurements", "SELECT count FROM private"]
)
def test_unapproved_sql_is_rejected_before_returning_generated_draft(sql):
    service, repository, _, generator, command = setup()
    output = json.loads(generator.generate.return_value)
    output["slots"][0]["sql"] = sql
    generator.generate.return_value = json.dumps(output)
    with pytest.raises(InvalidInput, match="dashboard_sql_validation_failed"):
        generate(service, command)
    repository.save_bindings.assert_not_called()
