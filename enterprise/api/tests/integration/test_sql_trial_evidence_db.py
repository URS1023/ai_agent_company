"""Trial evidence transactions against ORM and release DDL; disposable CI databases only."""

import os
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select
from test_dashboards import dashboards as dashboards
from test_repository import repository as repository
from test_sql_drafts_db import apply_test_schema_ddl, record
from test_sql_drafts_db import drafts as drafts
from test_sql_drafts_db import sql_storage_mode as sql_storage_mode

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.domain.data_sources import SqlRead
from enterprise_platform.persistence.dashboard_models import DashboardRow
from enterprise_platform.persistence.dashboard_sql_trial_evidence import (
    SqlAlchemySqlTrialEvidenceRepository,
    SqlTrialEvidenceBase,
    SqlTrialEvidenceRow,
)
from enterprise_platform.persistence.migrate_sql_trials import read_sql_trials_sql, sql_trials_statements
from enterprise_platform.persistence.models import AuditEventRow

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration runs in CI only")


@pytest.fixture
def trials(repository, drafts, sql_storage_mode):
    drafts.create(record())
    if sql_storage_mode == "migration-ddl":
        read_sql_trials_sql()
        apply_test_schema_ddl(repository, sql_trials_statements())
    else:
        with repository._sessions() as session:
            SqlTrialEvidenceBase.metadata.create_all(session.get_bind())
    return SqlAlchemySqlTrialEvidenceRepository(repository._sessions)


def test_deployed_trial_identity_and_listing_index(trials, repository):
    bind = repository._sessions.kw["bind"]
    schema = bind.get_execution_options().get("schema_translate_map", {}).get(None)
    inspector = inspect(bind)
    assert inspector.get_pk_constraint("enterprise_dashboard_sql_trials", schema=schema)["constrained_columns"] == [
        "workspace_id",
        "trial_id",
    ]
    indexes = inspector.get_indexes("enterprise_dashboard_sql_trials", schema=schema)
    assert any(
        index["name"] == "ix_sql_trial_draft"
        and index["column_names"] == ["workspace_id", "dashboard_id", "draft_id", "recorded_at", "trial_id"]
        for index in indexes
    )


def evidence():
    draft = record()
    digest = canonical_hash(draft.model_dump(mode="json"))
    read = SqlRead(source=draft.source, read_id=draft.draft_id, revision=digest, sql=draft.proposals.slots[0].sql)
    return SqlTrialEvidence(
        trial_id="trial-1",
        actor_id="runner",
        recorded_at=draft.created_at + timedelta(seconds=2),
        result=SqlTrialResult(
            workspace_id=draft.workspace_id,
            dashboard_id=draft.dashboard_id,
            draft_id=draft.draft_id,
            dashboard_revision=draft.dashboard_revision,
            design_identity=draft.design_identity,
            source=draft.source,
            draft_hash=digest,
            read_fingerprint=read_fingerprint(read, ()),
            slot_id="count",
            captured_at=draft.created_at + timedelta(seconds=1),
            columns=("n",),
            rows=(),
            row_count=0,
        ),
    )


def test_roundtrip_duplicate_and_scope_leave_one_capture_and_audit(trials, repository):
    assert trials.create(evidence()) == evidence()
    assert trials.get("w", "trial-1") == evidence()
    with pytest.raises(NotFound):
        trials.get("other", "trial-1")
    with pytest.raises(Conflict):
        trials.create(evidence())
    with repository._sessions() as session:
        assert len(session.scalars(select(SqlTrialEvidenceRow)).all()) == 1
        assert (
            len(
                session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "dashboard_sql_trial")).all()
            )
            == 1
        )


def test_changed_parent_rolls_back_capture_and_audit(trials, repository):
    with repository._sessions.begin() as session:
        row = session.scalar(select(DashboardRow).where(DashboardRow.workspace_id == "w"))
        row.revision += 1
    with pytest.raises(Conflict):
        trials.create(evidence())
    with repository._sessions() as session:
        assert not session.scalars(select(SqlTrialEvidenceRow)).all()
        assert not session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "dashboard_sql_trial")
        ).all()


def test_corrupt_trial_document_is_not_returned(trials, repository):
    trials.create(evidence())
    with repository._sessions.begin() as session:
        row = session.scalar(select(SqlTrialEvidenceRow))
        row.document_json += " "
    with pytest.raises(PersistenceError):
        trials.get("w", "trial-1")


def test_history_keyset_handles_tied_timestamps_and_new_insertions(trials):
    for trial_id in ("trial-1", "trial-2", "trial-3"):
        trials.create(evidence().model_copy(update={"trial_id": trial_id}))
    first = trials.list("w", "dashboard-1", "draft-1", limit=2)
    assert [item.trial_id for item in first] == ["trial-3", "trial-2"]
    trials.create(evidence().model_copy(update={"trial_id": "trial-4"}))
    second = trials.list("w", "dashboard-1", "draft-1", after=first[-1].trial_id, limit=2)
    assert [item.trial_id for item in second] == ["trial-1"]
    assert trials.list("other", "dashboard-1", "draft-1", limit=2) == ()
    with pytest.raises(NotFound):
        trials.list("w", "other", "draft-1", after="trial-2", limit=2)
    assert set(first[0].model_dump()) == {
        "workspace_id",
        "dashboard_id",
        "draft_id",
        "trial_id",
        "actor_id",
        "recorded_at",
    }
