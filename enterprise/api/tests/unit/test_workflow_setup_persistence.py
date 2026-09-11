from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workflow_setup_contracts import SetupRequest, SetupView
from enterprise_platform.application.workflow_setup_ports import StoredSetup
from enterprise_platform.persistence.workflow_setup_models import SetupBase
from enterprise_platform.persistence.workflow_setups import as_setup, setup_row


def stored():
    now = datetime(2026, 9, 8, tzinfo=UTC)
    return StoredSetup(
        SetupView(
            id="setup",
            workspace_id="workspace",
            device_id="device",
            scenario="alert",
            source_id="source",
            source_revision="immutable-1",
            read_id="read",
            read_revision="read-1",
            expected_source_revision=1,
            expected_binding_revision=None,
            revision=1,
            state="queued",
            name="设备告警",
            created_at=now,
            updated_at=now,
        ),
        "actor",
        "request",
        "a" * 64,
    )


def test_request_revision_and_state_invariants():
    assert SetupRequest(source_id="source", expected_source_revision=1).expected_binding_revision is None
    for value in (0, True, "1"):
        with pytest.raises(ValidationError):
            SetupRequest(source_id="source", expected_source_revision=value)
    for changes in ({"state": "draft_ready"}, {"state": "confirmation_required"}, {"app_id": "app"}):
        with pytest.raises(ValidationError):
            SetupView.model_validate(stored().view.model_dump() | changes)
    with pytest.raises(ValueError):
        replace(stored(), view=SetupView.model_validate(stored().view.model_dump() | {"state": "importing"}))


def test_complete_roundtrip_and_tamper_rejection():
    value = stored()
    assert as_setup(setup_row(value)) == value
    assert "import_nonce" not in value.view.model_dump()
    for key, changed in (
        ("workspace_id", "other"),
        ("source_revision", "other"),
        ("state", "failed"),
        ("revision", 2),
        ("device_id", "other"),
        ("read_revision", "other"),
    ):
        row = setup_row(value)
        setattr(row, key, changed)
        with pytest.raises(PersistenceError):
            as_setup(row)
    row = setup_row(value)
    row.public_json = "{}"
    with pytest.raises(PersistenceError):
        as_setup(row)


def test_metadata_separate_and_compiles_without_native_tables():
    assert set(SetupBase.metadata.tables) == {"enterprise_workflow_setups"}
    table = next(iter(SetupBase.metadata.tables.values()))
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY (workspace_id, setup_id)" in sql
    assert "UNIQUE (workspace_id, request_key)" in sql
    assert "importing" in sql and "revision > 0" in sql


def repository_with(value):
    from unittest.mock import MagicMock, create_autospec

    from sqlalchemy import CursorResult
    from sqlalchemy.orm import Session

    from enterprise_platform.persistence.workflow_setups import SqlAlchemyWorkflowSetupRepository

    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.return_value = setup_row(value)
    changed = create_autospec(CursorResult, instance=True)
    changed.rowcount = 1
    session.execute.return_value = changed
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    return SqlAlchemyWorkflowSetupRepository(sessions), session, sessions


def test_claim_is_revision_and_queued_cas_with_device_lock_and_audit():
    from enterprise_platform.persistence.models import AuditEventRow

    repo, session, _ = repository_with(stored())
    result = repo.claim_import("workspace", "setup", expected_revision=1, nonce="nonce", actor_id="actor")
    assert result.view.state == "importing" and result.view.revision == 2
    assert result.import_nonce == "nonce" and "nonce" not in repr(result)
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    for column in ("workspace_id", "setup_id", "revision", "state"):
        assert column in sql.split("WHERE")[1]
    assert "enterprise_devices" in str(session.execute.call_args_list[0].args[0])
    assert isinstance(session.add.call_args.args[0], AuditEventRow)


@pytest.mark.parametrize("state", ["importing", "uncertain", "failed", "confirmation_required", "draft_ready"])
def test_nonqueued_setup_never_reclaims(state):
    from enterprise_platform.application.errors import Conflict

    data = stored().view.model_dump() | {"state": state}
    if state == "draft_ready":
        data["app_id"] = "app"
    if state in {"draft_ready", "confirmation_required"}:
        data["import_id"] = "import"
    value = replace(
        stored(), view=SetupView.model_validate(data), import_nonce="nonce" if state == "importing" else None
    )
    repo, session, _ = repository_with(value)
    with pytest.raises(Conflict):
        repo.claim_import("workspace", "setup", expected_revision=1, nonce="new", actor_id="actor")
    session.add.assert_not_called()


def test_finish_requires_importer_nonce_and_clears_ownership():
    from enterprise_platform.application.errors import Conflict

    value = replace(
        stored(),
        view=SetupView.model_validate(stored().view.model_dump() | {"state": "importing"}),
        import_nonce="nonce",
    )
    repo, session, _ = repository_with(value)
    with pytest.raises(Conflict):
        repo.finish_import(
            "workspace",
            "setup",
            nonce="wrong",
            state="uncertain",
            app_id=None,
            import_id=None,
            reason_code="timeout",
            actor_id="actor",
        )
    session.add.assert_not_called()
    result = repo.finish_import(
        "workspace",
        "setup",
        nonce="nonce",
        state="draft_ready",
        app_id="app",
        import_id="import",
        reason_code=None,
        actor_id="actor",
    )
    assert result.view.state == "draft_ready" and result.import_nonce is None
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "import_nonce" in sql.split("WHERE")[1]


def test_create_replay_matches_hash_and_does_not_duplicate_audit():
    from enterprise_platform.application.errors import Conflict

    repo, session, _ = repository_with(stored())
    assert repo.create(stored(), actor_id="actor") == stored()
    session.add.assert_not_called()
    with pytest.raises(Conflict):
        repo.create(replace(stored(), request_hash="b" * 64), actor_id="actor")


def test_invalid_stored_internal_fields_fail_closed():
    for key, value in (("import_nonce", "unexpected"), ("request_hash", "bad"), ("actor_id", "")):
        row = setup_row(stored())
        setattr(row, key, value)
        with pytest.raises(PersistenceError):
            as_setup(row)


def test_create_inserts_setup_and_audit_in_same_transaction():
    from enterprise_platform.persistence.models import AuditEventRow
    from enterprise_platform.persistence.workflow_setup_models import WorkflowSetupRow

    repo, session, sessions = repository_with(stored())
    session.scalar.return_value = None
    assert repo.create(stored(), actor_id="actor") == stored()
    assert [type(call.args[0]) for call in session.add.call_args_list] == [WorkflowSetupRow, AuditEventRow]
    session.flush.assert_called_once()
    sessions.begin.return_value.__exit__.assert_called_once_with(None, None, None)


def test_deleted_device_aborts_before_setup_or_audit_write():
    from enterprise_platform.application.errors import NotFound

    repo, session, sessions = repository_with(stored())
    session.execute.return_value.rowcount = 0
    with pytest.raises(NotFound):
        repo.create(stored(), actor_id="actor")
    session.add.assert_not_called()
    assert sessions.begin.return_value.__exit__.call_args.args[0] is NotFound


def test_lost_claim_cas_rolls_back_without_audit():
    from unittest.mock import create_autospec

    from sqlalchemy import CursorResult

    from enterprise_platform.application.errors import Conflict

    repo, session, sessions = repository_with(stored())
    success = create_autospec(CursorResult, instance=True)
    success.rowcount = 1
    loser = create_autospec(CursorResult, instance=True)
    loser.rowcount = 0
    session.execute.side_effect = [success, loser]
    with pytest.raises(Conflict):
        repo.claim_import("workspace", "setup", expected_revision=1, nonce="nonce", actor_id="actor")
    session.add.assert_not_called()
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict


def test_list_and_get_scope_without_deleting_archived_history():
    repo, session, _ = repository_with(stored())
    assert repo.get("workspace", "setup").view == stored().view
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert set(compiled.params.values()) == {"workspace", "setup"}
    session.scalar.return_value = 1
    session.scalars.return_value.all.return_value = [setup_row(stored())]
    page = repo.list("workspace", device_id="device", scenario="alert", offset=0, limit=20)
    assert page.total == 1 and page.items == (stored().view,)
    compiled = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert {"workspace", "device", "alert", 0, 20} == set(compiled.params.values())
    assert "ORDER BY" in str(compiled)


def test_constraint_collision_rolls_back_then_returns_only_hash_matched_request():
    from sqlalchemy.exc import IntegrityError

    repo, session, sessions = repository_with(stored())
    session.scalar.side_effect = [None, setup_row(stored())]
    session.flush.side_effect = IntegrityError("private driver text", {}, Exception("private"))
    assert repo.create(stored(), actor_id="actor") == stored()
    assert sessions.begin.call_count == 2
    assert sessions.begin.return_value.__exit__.call_args_list[0].args[0] is IntegrityError
    assert session.add.call_count == 1


def test_migration_matches_metadata_without_touching_earlier_metadata():
    from pathlib import Path

    import enterprise_platform.persistence.workflow_setup_models as models
    from enterprise_platform.persistence.models import Base
    from enterprise_platform.persistence.source_models import SourceBase

    sql = (Path(models.__file__).parent / "migrations" / "0003_workflow_setups.sql").read_text(encoding="utf-8")
    from enterprise_platform.persistence.migrate_workflow_setups import render_workflow_setups_sql

    assert sql == render_workflow_setups_sql()
    assert len(Base.metadata.tables) == 4
    assert len(SourceBase.metadata.tables) == 2


def test_finish_retains_owned_native_outcome_after_device_deletion_without_device_mutation():
    from unittest.mock import create_autospec

    from sqlalchemy import CursorResult

    from enterprise_platform.persistence.models import AuditEventRow

    value = replace(
        stored(),
        view=SetupView.model_validate(stored().view.model_dump() | {"state": "importing", "revision": 2}),
        import_nonce="owner",
    )
    repo, session, sessions = repository_with(value)

    def execute(statement):
        result = create_autospec(CursorResult, instance=True)
        result.rowcount = 0 if "enterprise_devices" in str(statement) else 1
        return result

    session.execute.side_effect = execute
    result = repo.finish_import(
        "workspace",
        "setup",
        nonce="owner",
        state="draft_ready",
        app_id="native-app",
        import_id="native-import",
        reason_code=None,
        actor_id="actor",
    )
    assert result.view.app_id == "native-app" and result.view.import_id == "native-import"
    assert result.view.revision == 3 and result.import_nonce is None
    assert session.execute.call_count == 1
    statement = session.execute.call_args.args[0]
    assert "enterprise_devices" not in str(statement)
    compiled = statement.compile(dialect=postgresql.dialect())
    assert {"workspace", "setup", "owner", "importing", 2} <= set(compiled.params.values())
    assert isinstance(session.add.call_args.args[0], AuditEventRow)
    sessions.begin.return_value.__exit__.assert_called_once_with(None, None, None)
