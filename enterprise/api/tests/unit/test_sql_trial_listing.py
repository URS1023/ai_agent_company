from unittest.mock import create_autospec, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from test_sql_trial_evidence import evidence

from enterprise_platform.application.errors import InvalidInput, NotFound, PersistenceError
from enterprise_platform.persistence.dashboard_sql_trial_evidence import SqlAlchemySqlTrialEvidenceRepository


def summary():
    item = evidence()
    return dict(
        workspace_id="w",
        dashboard_id="screen",
        draft_id="draft-1",
        trial_id=item.trial_id,
        actor_id=item.actor_id,
        recorded_at=item.recorded_at,
    )


def test_list_reads_only_metadata_with_exact_scope_and_stable_time_order():
    session = create_autospec(Session, instance=True)
    session.execute.return_value.mappings.return_value.all.return_value = [summary()]
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        items = SqlAlchemySqlTrialEvidenceRepository(lambda: session).list("w", "screen", "draft-1", limit=21)
    assert items[0].model_dump() == summary()
    query = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(query)
    assert "document_json" not in sql and "document_hash" not in sql
    assert "recorded_at DESC" in sql and "trial_id DESC" in sql
    assert set(query.params.values()) == {"w", "screen", "draft-1", 21}


def test_cursor_lookup_is_scoped_and_next_page_uses_time_and_identifier():
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = evidence().recorded_at
    session.execute.return_value.mappings.return_value.all.return_value = []
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        assert (
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).list(
                "w", "screen", "draft-1", after="trial-1", limit=20
            )
            == ()
        )
    anchor = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert set(anchor.params.values()) == {"w", "screen", "draft-1", "trial-1"}
    query = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "recorded_at <" in query and "trial_id <" in query


def test_missing_or_foreign_cursor_does_not_fall_back_to_first_page():
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = None
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(NotFound):
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).list(
                "w", "screen", "draft-1", after="missing", limit=20
            )
    session.execute.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 102, True])
def test_invalid_limit_is_rejected_before_opening_a_transaction(limit):
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        with pytest.raises(InvalidInput):
            SqlAlchemySqlTrialEvidenceRepository(None).list("w", "screen", "draft-1", limit=limit)
    transaction.assert_not_called()


def test_foreign_summary_is_not_returned():
    session = create_autospec(Session, instance=True)
    session.execute.return_value.mappings.return_value.all.return_value = [{**summary(), "workspace_id": "other"}]
    with patch("enterprise_platform.persistence.dashboard_sql_trial_evidence.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(PersistenceError):
            SqlAlchemySqlTrialEvidenceRepository(lambda: session).list("w", "screen", "draft-1", limit=20)
