import asyncio
from dataclasses import replace
from unittest.mock import create_autospec

import pytest
from pydantic import ValidationError
from test_dashboard_query_execution import actor
from test_dashboard_storage import stored_record

from enterprise_platform.application.dashboard_contracts import DashboardCreateCommand
from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService, DashboardRepository
from enterprise_platform.application.dashboard_service import DashboardService, DashboardTemplates
from enterprise_platform.application.errors import AccessDenied, Conflict, NotFound


def fixture():
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.side_effect = NotFound()
    repository.create.side_effect = lambda record, **kwargs: record
    templates = create_autospec(DashboardTemplates, instance=True)
    templates.get.return_value = stored_record().design
    service = DashboardService(repository, create_autospec(DashboardRefreshService), templates=templates)
    command = DashboardCreateCommand(
        template_id=stored_record().design.template_id, expected_design_identity=stored_record().design.identity
    )
    return service, repository, templates, command


def test_creation_copies_only_server_template_with_empty_state_and_scoped_audit_actor() -> None:
    service, repository, templates, command = fixture()
    result = asyncio.run(service.create(actor(), command, request_key="request-1"))
    saved = repository.create.call_args.args[0]
    assert saved.workspace_id == actor().workspace_id
    assert saved.design == stored_record().design
    assert saved.bindings == ()
    assert result.current is None and result.status == "empty" and result.revision == 1
    assert repository.create.call_args.kwargs == {"actor_id": actor().actor_id}
    assert "request-1" not in result.id


def test_named_creation_persists_name_and_request_fingerprint() -> None:
    service, repository, templates, command = fixture()
    named = DashboardCreateCommand(**{**command.model_dump(), "name": "  一车间设备预警  "})
    first = asyncio.run(service.create(actor(), named, request_key="same"))
    saved = repository.create.call_args.args[0]
    assert saved.name == first.name == "一车间设备预警"
    assert len(saved.creation_request_hash) == 64
    repository.get.side_effect = None
    repository.get.return_value = replace(saved, name="重命名后", revision=2)
    replay = asyncio.run(service.create(actor(), named, request_key="same"))
    assert replay.id == first.id and replay.name == "重命名后"
    with pytest.raises(Conflict):
        asyncio.run(service.create(actor(), named.model_copy(update={"name": "另一大屏"}), request_key="same"))
    repository.create.assert_called_once()


@pytest.mark.parametrize("name", ["", "  ", "a" * 201, 42])
def test_creation_rejects_invalid_explicit_names(name) -> None:
    with pytest.raises(ValidationError):
        DashboardCreateCommand(template_id="equipment", expected_design_identity="a" * 64, name=name)


def test_repeated_request_returns_same_dashboard_without_another_write_or_template_read() -> None:
    service, repository, templates, command = fixture()
    first = asyncio.run(service.create(actor(), command, request_key="same"))
    repository.get.side_effect = None
    repository.get.return_value = replace(repository.create.call_args.args[0], revision=2)
    templates.get.reset_mock()
    second = asyncio.run(service.create(actor(), command, request_key="same"))
    assert second.id == first.id and second.revision == 2
    repository.create.assert_called_once()
    templates.get.assert_not_called()
    with pytest.raises(Conflict):
        asyncio.run(
            service.create(
                actor(), command.model_copy(update={"expected_design_identity": "a" * 64}), request_key="same"
            )
        )


def test_racing_duplicate_reads_winner_after_unique_constraint_conflict() -> None:
    service, repository, templates, command = fixture()

    def race(record, **kwargs):
        repository.get.side_effect = None
        repository.get.return_value = record
        raise Conflict()

    repository.create.side_effect = race
    result = asyncio.run(service.create(actor(), command, request_key="same"))
    assert result.status == "empty"
    assert repository.get.call_count == 2


def test_template_revision_conflict_and_read_only_member_do_not_create() -> None:
    service, repository, templates, command = fixture()
    with pytest.raises(Conflict):
        asyncio.run(
            service.create(
                actor(), command.model_copy(update={"expected_design_identity": "a" * 64}), request_key="same"
            )
        )
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.create(actor().model_copy(update={"workspace_role": "normal"}), command, request_key="same")
        )
    repository.create.assert_not_called()


def test_request_identity_is_separate_for_different_actors_and_workspaces() -> None:
    service, repository, templates, command = fixture()
    results = [
        asyncio.run(service.create(principal, command, request_key="same")).id
        for principal in [
            actor(),
            actor().model_copy(update={"actor_id": "other"}),
            actor().model_copy(update={"workspace_id": "other"}),
        ]
    ]
    assert len(set(results)) == 3
