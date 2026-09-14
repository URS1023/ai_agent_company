"""Real SQLite policy checks; PostgreSQL lock behavior is tested separately."""

import hashlib
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.office_edits import OfficeFileRecord, OfficeGrant
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit
from enterprise_platform.persistence.office_source_access import SqlAlchemyOfficeSourceAccess
from enterprise_platform.persistence.office_source_models import (
    OfficeSnapshotRow,
    OfficeSourceBase,
    OfficeSourceGrantRow,
    OfficeSourceRow,
)


@pytest.fixture
def database():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    OfficeSourceBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(OfficeSourceRow(workspace_id="ws", source_id="source", enabled=True, acl_revision=1))
        session.flush()
        session.add(OfficeSourceGrantRow(workspace_id="ws", source_id="source", actor_id="actor", can_read=True))
        session.add(
            OfficeSnapshotRow(
                workspace_id="ws",
                snapshot_id="snapshot",
                source_id="source",
                source_revision="revision-1",
                payload_json='{"value":1}',
                payload_hash=hashlib.sha256(b'{"value":1}').hexdigest(),
            )
        )
        session.commit()
        yield session
    engine.dispose()


def inputs():
    content = OfficeRevision(
        file_id=UUID(int=1),
        revision=1,
        kind="document",
        units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Report"),)),),
    )
    return OfficeGrant("ws", "actor", UUID(int=1), "read", 1), OfficeFileRecord(
        "ws", "document-default", 1, ("snapshot",), content
    )


def test_current_source_grant_allows_exact_snapshot(database):
    grant, record = inputs()
    SqlAlchemyOfficeSourceAccess().require(database, grant, record)


@pytest.mark.parametrize("change", ["workspace", "actor", "snapshot", "disabled", "revoked", "file", "duplicate"])
def test_missing_or_revoked_source_access_denies(database, change):
    grant, record = inputs()
    if change == "workspace":
        grant = replace(grant, workspace_id="other")
    elif change == "actor":
        grant = replace(grant, actor_id="other")
    elif change == "snapshot":
        record = replace(record, source_snapshot_ids=("missing",))
    elif change == "file":
        grant = replace(grant, file_id=UUID(int=99))
    elif change == "duplicate":
        record = replace(record, source_snapshot_ids=("snapshot", "snapshot"))
    elif change == "disabled":
        database.get(OfficeSourceRow, ("ws", "source")).enabled = False
    else:
        database.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read = False
    database.flush()
    with pytest.raises(AccessDenied):
        SqlAlchemyOfficeSourceAccess().require(database, grant, record)


def test_snapshot_payload_corruption_fails_closed(database):
    database.get(OfficeSnapshotRow, ("ws", "snapshot")).payload_json = '{"value":2}'
    database.flush()
    with pytest.raises(PersistenceError):
        SqlAlchemyOfficeSourceAccess().require(database, *inputs())


def test_no_source_document_still_checks_file_scope(database):
    grant, record = inputs()
    record = replace(record, source_snapshot_ids=())
    SqlAlchemyOfficeSourceAccess().require(database, grant, record)
    with pytest.raises(AccessDenied):
        SqlAlchemyOfficeSourceAccess().require(database, replace(grant, workspace_id="other"), record)


def test_rechecks_permission_after_previously_successful_read(database):
    grant, record = inputs()
    policy = SqlAlchemyOfficeSourceAccess()
    policy.require(database, grant, record)
    permission = database.get(OfficeSourceGrantRow, ("ws", "source", "actor"))
    permission.can_read = False
    database.flush()
    with pytest.raises(AccessDenied):
        policy.require(database, grant, record)


def test_every_snapshot_source_is_required(database):
    grant, record = inputs()
    database.add(OfficeSourceRow(workspace_id="ws", source_id="second", enabled=True, acl_revision=1))
    database.flush()
    database.add(
        OfficeSnapshotRow(
            workspace_id="ws",
            snapshot_id="second-snapshot",
            source_id="second",
            source_revision="r1",
            payload_json="{}",
            payload_hash=hashlib.sha256(b"{}").hexdigest(),
        )
    )
    database.flush()
    record = replace(record, source_snapshot_ids=("snapshot", "second-snapshot"))
    with pytest.raises(AccessDenied):
        SqlAlchemyOfficeSourceAccess().require(database, grant, record)
