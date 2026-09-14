"""Two PostgreSQL connections verify revocation after an earlier transaction read."""

import hashlib
import os
from uuid import UUID

import pytest
from database_environment import database_tests_enabled
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_repository import repository as repository

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

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database opt-in required"
)


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"])
def test_source_revocation_after_prior_transaction_read(repository, isolation):
    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction snapshot behavior")
    OfficeSourceBase.metadata.create_all(engine)
    with repository._sessions.begin() as session:
        session.add(OfficeSourceRow(workspace_id="ws", source_id="source", enabled=True, acl_revision=1))
        session.flush()
        session.add(OfficeSourceGrantRow(workspace_id="ws", source_id="source", actor_id="actor", can_read=True))
        session.add(
            OfficeSnapshotRow(
                workspace_id="ws",
                snapshot_id="snapshot",
                source_id="source",
                source_revision="r1",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
            )
        )
    grant = OfficeGrant("ws", "actor", UUID(int=1), "read", 1)
    record = OfficeFileRecord(
        "ws",
        "document-default",
        1,
        ("snapshot",),
        OfficeRevision(
            file_id=grant.file_id,
            revision=1,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Report"),)),),
        ),
    )
    with engine.connect().execution_options(isolation_level=isolation) as connection, Session(connection) as reader:
        # Establish an earlier MVCC snapshot and cache the granted ORM entity.
        assert reader.scalar(select(OfficeSourceGrantRow)).can_read is True
        with repository._sessions.begin() as writer:
            writer.scalar(select(OfficeSourceRow).with_for_update())
            writer.scalar(select(OfficeSourceGrantRow)).can_read = False
        expected = AccessDenied if isolation == "READ COMMITTED" else PersistenceError
        with pytest.raises(expected):
            SqlAlchemyOfficeSourceAccess().require(reader, grant, record)
