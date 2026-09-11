from unittest.mock import create_autospec, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from test_dashboard_sql_drafts import draft, parent
from test_sql_trial_evidence import evidence

from enterprise_platform.application.contracts import canonical_hash, canonical_json
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.persistence.dashboard_sql_drafts import SqlDraftRow
from enterprise_platform.persistence.dashboard_sql_trial_evidence import (
    SqlAlchemySqlTrialEvidenceRepository,
    SqlTrialEvidenceRow,
)


def draft_row():
    item = draft()
    return SqlDraftRow(
        workspace_id=item.workspace_id,
        draft_id=item.draft_id,
        dashboard_id=item.dashboard_id,
        dashboard_revision=item.dashboard_revision,
        actor_id=item.actor_id,
        created_at=item.created_at,
        document_json=canonical_json(item.model_dump(mode="json")),
        document_hash=canonical_hash(item.model_dump(mode="json")),
    )


def test_create_stores_detached_capture_and_audits_metadata_under_parent_lock():
    session = create_autospec(Session, instance=True)
    session.scalar.side_effect = [parent(), draft_row()]
    source = evidence()
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        saved = SqlAlchemySqlTrialEvidenceRepository(lambda: session).create(source)
    assert saved == source
    assert saved is not source
    added = [call.args[0] for call in session.add.call_args_list]
    assert len(added) == 2
    row = next(row for row in added if isinstance(row, SqlTrialEvidenceRow))
    assert row.trial_id == "trial-1"
    assert "18446744073709551616" in row.document_json
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    assert list(SqlTrialEvidenceRow.__table__.primary_key.columns.keys()) == ["workspace_id", "trial_id"]


@pytest.mark.parametrize("missing", [0, 1])
def test_missing_parent_or_draft_never_saves_evidence(missing):
    session = create_autospec(Session, instance=True)
    session.scalar.side_effect = [None] if missing == 0 else [parent(), None]
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(NotFound):
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).create(evidence())
    session.add.assert_not_called()


def test_stale_parent_never_saves_evidence():
    session = create_autospec(Session, instance=True)
    current = parent()
    current.revision += 1
    session.scalar.return_value = current
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(Conflict):
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).create(evidence())
    session.add.assert_not_called()


def test_changed_draft_never_saves_evidence():
    session = create_autospec(Session, instance=True)
    changed = draft()
    changed.proposals.slots[0].field_map["value"] = "other"
    row = draft_row()
    row.document_json = canonical_json(changed.model_dump(mode="json"))
    row.document_hash = canonical_hash(changed.model_dump(mode="json"))
    session.scalar.side_effect = [parent(), row]
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(Conflict):
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).create(evidence())
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"document_hash": "0" * 64},
        {"workspace_id": "other"},
        {"trial_id": "other"},
        {"actor_id": "other"},
        {"dashboard_id": "other"},
        {"draft_id": "other"},
    ],
)
def test_get_rejects_corruption_or_foreign_metadata(change):
    session = create_autospec(Session, instance=True)
    session.scalar.side_effect = [parent(), draft_row()]
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        repo = SqlAlchemySqlTrialEvidenceRepository(lambda: session)
        repo.create(evidence())
        row = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], SqlTrialEvidenceRow))
        session.scalar.side_effect = None
        session.scalar.return_value = row
        assert repo.get("w", "trial-1") == evidence()
        for key, value in change.items():
            setattr(row, key, value)
        with pytest.raises(PersistenceError):
            repo.get("w", "trial-1")
