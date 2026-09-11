from datetime import timedelta
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy import CursorResult, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from test_scheduling import START, schedule
from test_workflow_activation_contracts import create as activation

from enterprise_platform.application.contracts import BindingWrite
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.persistence.mapping import serialized
from enterprise_platform.persistence.models import BindingRow
from enterprise_platform.persistence.schedule_mapping import as_schedule, schedule_row
from enterprise_platform.persistence.schedule_models import ScheduleBase, ScheduleRow
from enterprise_platform.persistence.schedules import SqlAlchemyScheduleRepository


def fixture():
    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    result = create_autospec(CursorResult, instance=True)
    result.rowcount = 1
    session.execute.return_value = result
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    repo = SqlAlchemyScheduleRepository(sessions, clock=lambda: START + timedelta(seconds=1))
    row = schedule_row(schedule(), START, START)
    session.scalar.return_value = row
    return repo, session, row


def test_schedule_metadata_is_independent_and_binding_owner_is_unique() -> None:
    assert set(ScheduleBase.metadata.tables) == {"enterprise_schedules"}
    uniques = [set(c.columns.keys()) for c in ScheduleRow.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert {"workspace_id", "binding_id"} in uniques
    ddl = str(CreateTable(ScheduleRow.__table__).compile(dialect=postgresql.dialect()))
    assert "revision > 0" in ddl
    assert "updated_at >= created_at" in ddl


def test_roundtrip_preserves_scope_cursor_and_frozen_configuration() -> None:
    value = schedule()
    assert as_schedule(schedule_row(value, START, START)) == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("schedule_id", "other"),
        ("binding_id", "other"),
        ("binding_revision", 99),
        ("device_id", "other"),
        ("scenario", "quality"),
        ("revision", 99),
        ("enabled", False),
        ("service_actor_id", "other"),
        ("next_due_at", START + timedelta(seconds=60)),
        ("public_json", "{}"),
        ("updated_at", START - timedelta(seconds=1)),
    ],
)
def test_corrupt_schedule_mirrors_are_rejected(field: str, value: object) -> None:
    row = schedule_row(schedule(), START, START)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        as_schedule(row)


def test_read_is_workspace_scoped_and_checks_the_returned_identity() -> None:
    repo, session, row = fixture()
    assert repo.get("workspace-1", "schedule-1") == schedule()
    statement = session.scalar.call_args.args[0]
    assert statement.compile().params == {"workspace_id_1": "workspace-1", "schedule_id_1": "schedule-1"}
    with pytest.raises(PersistenceError):
        repo.get("wrong-workspace", row.schedule_id)
    session.scalar.return_value = None
    with pytest.raises(NotFound):
        repo.get("workspace-1", "missing")


def test_pause_is_revision_fenced_and_preserves_cursor_with_atomic_audit() -> None:
    repo, session, _ = fixture()
    result = repo.set_enabled("workspace-1", "schedule-1", expected_revision=1, enabled=False, actor_id="admin")
    assert result.enabled is False
    assert result.revision == 2
    assert result.next_due_at == START
    assert "revision" in str(session.execute.call_args.args[0])
    assert session.add.call_count == 1


def test_stale_pause_and_lost_cas_do_not_write_an_audit() -> None:
    repo, session, _ = fixture()
    with pytest.raises(Conflict):
        repo.set_enabled("workspace-1", "schedule-1", expected_revision=2, enabled=False, actor_id="admin")
    session.execute.assert_not_called()
    session.execute.return_value.rowcount = 0
    with pytest.raises(Conflict):
        repo.set_enabled("workspace-1", "schedule-1", expected_revision=1, enabled=False, actor_id="admin")
    session.add.assert_not_called()


def test_same_enabled_state_does_not_increment_revision_or_emit_an_audit() -> None:
    repo, session, _ = fixture()
    assert (
        repo.set_enabled("workspace-1", "schedule-1", expected_revision=1, enabled=True, actor_id="admin") == schedule()
    )
    session.execute.assert_not_called()
    session.add.assert_not_called()


def test_create_checks_exact_live_binding_and_starts_paused() -> None:
    repo, session, _ = fixture()
    binding = activation().binding
    value = schedule(
        workspace_id=binding.workspace_id,
        binding_id=binding.id,
        binding_revision=binding.revision,
        device_id=binding.device_id,
        scenario=binding.scenario,
        enabled=False,
    )
    payload = BindingWrite.model_validate(
        binding.model_dump(
            exclude={
                "id",
                "workspace_id",
                "device_id",
                "scenario",
                "revision",
                "created_at",
                "updated_at",
                "active_run_id",
            }
        )
    )
    row = BindingRow(
        workspace_id=binding.workspace_id,
        binding_id=binding.id,
        binding_json=serialized(payload),
        device_id=binding.device_id,
        scenario=binding.scenario,
        revision=binding.revision,
        active_run_id=None,
        created_at=START,
        updated_at=START,
    )
    session.scalar.return_value = row
    result = repo.create(value, actor_id="admin")
    assert result == value
    assert session.add.call_count == 2
    assert isinstance(session.add.call_args_list[0].args[0], ScheduleRow)
    session.add.reset_mock()
    row.revision += 1
    with pytest.raises(Conflict):
        repo.create(value, actor_id="admin")
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "changes", [{"enabled": True}, {"revision": 2}, {"next_due_at": START + timedelta(seconds=60)}]
)
def test_creation_requires_a_new_paused_schedule(changes: dict[str, object]) -> None:
    repo, session, _ = fixture()
    with pytest.raises(Conflict):
        repo.create(schedule(**({"enabled": False} | changes)), actor_id="admin")
    session.execute.assert_not_called()
    session.add.assert_not_called()
