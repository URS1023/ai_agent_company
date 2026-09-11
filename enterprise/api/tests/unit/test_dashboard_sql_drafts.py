from datetime import UTC, datetime
from unittest.mock import create_autospec, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRecord
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.persistence.dashboard_models import DashboardRow
from enterprise_platform.persistence.dashboard_sql_drafts import SqlAlchemySqlDraftRepository, SqlDraftBase, SqlDraftRow


def draft():
    return SqlDraftRecord.model_validate_json(
        '''{
      "draft_id":"draft-1","workspace_id":"w","actor_id":"actor","dashboard_id":"screen",
      "dashboard_revision":3,"design_identity":"'''
        + "a" * 64
        + '''",
      "source":{"workspace_id":"w","source_id":"source","revision":"v1"},
      "schema_revision":"'''
        + "b" * 64
        + """","prompt":"Count inspections",
      "created_at":"2026-09-11T00:00:00Z",
      "proposals":{"slots":[{"slot_id":"count","sql":"SELECT COUNT(*) AS count FROM measurements",
      "field_map":{"value":"count"},"metric_definition":"Inspection count","time_definition":"All time"}]}
    }"""
    )


def parent():
    return DashboardRow(workspace_id="w", dashboard_id="screen", revision=3, design_identity="a" * 64)


def test_draft_storage_is_separate_and_workspace_keyed():
    assert set(SqlDraftBase.metadata.tables) == {"enterprise_dashboard_sql_drafts"}
    assert list(SqlDraftRow.__table__.primary_key.columns.keys()) == ["workspace_id", "draft_id"]
    ddl = str(CreateTable(SqlDraftRow.__table__).compile(dialect=postgresql.dialect()))
    assert "document_hash" in ddl
    assert "created_at" in ddl


def test_create_locks_parent_revision_and_stores_detached_audited_document():
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = parent()
    source = draft()
    with patch("enterprise_platform.persistence.dashboard_sql_drafts.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        saved = SqlAlchemySqlDraftRepository(lambda: session).create(source)
    assert saved == source
    source.proposals.slots[0].field_map["value"] = "changed"
    assert saved.proposals.slots[0].field_map["value"] == "count"
    rows = [call.args[0] for call in session.add.call_args_list]
    stored = next(row for row in rows if isinstance(row, SqlDraftRow))
    assert stored.created_at == datetime(2026, 9, 11, tzinfo=UTC)
    assert len(stored.document_hash) == 64
    assert len(rows) == 2
    query = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in query


@pytest.mark.parametrize("change", [{"revision": 4}, {"design_identity": "c" * 64}])
def test_stale_draft_never_saves_or_audits(change):
    session = create_autospec(Session, instance=True)
    row = parent()
    for key, value in change.items():
        setattr(row, key, value)
    session.scalar.return_value = row
    with patch("enterprise_platform.persistence.dashboard_sql_drafts.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(Conflict):
            SqlAlchemySqlDraftRepository(lambda: session).create(draft())
    session.add.assert_not_called()


def test_missing_parent_never_creates_orphan_draft():
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = None
    with patch("enterprise_platform.persistence.dashboard_sql_drafts.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(NotFound):
            SqlAlchemySqlDraftRepository(lambda: session).create(draft())
    session.add.assert_not_called()


def test_get_rejects_corrupt_hash_and_foreign_scope():
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = parent()
    with patch("enterprise_platform.persistence.dashboard_sql_drafts.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        repo = SqlAlchemySqlDraftRepository(lambda: session)
        repo.create(draft())
        stored = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], SqlDraftRow))
        session.scalar.return_value = stored
        assert repo.get("w", "draft-1") == draft()
        stored.created_at = stored.created_at.replace(tzinfo=None)
        assert repo.get("w", "draft-1") == draft()
        with pytest.raises(PersistenceError):
            repo.get("other", "draft-1")
        stored.document_hash = "0" * 64
        with pytest.raises(PersistenceError):
            repo.get("w", "draft-1")
