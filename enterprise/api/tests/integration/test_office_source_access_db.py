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


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE", "AUTOCOMMIT"])
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


def test_revocation_waits_for_active_source_reader_then_denies_new_reads(repository):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text

    from enterprise_platform.persistence.office_source_revocation import revoke_source_reader

    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Observable PostgreSQL source lock")
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
    ready = Event()
    pids = []

    def revoke():
        with repository._sessions.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '15s'"))
            pids.append(writer.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            return revoke_source_reader(
                writer, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1
            )

    with repository._sessions() as reader, ThreadPoolExecutor(max_workers=1) as pool:
        SqlAlchemyOfficeSourceAccess().require(reader, grant, record)
        future = pool.submit(revoke)
        try:
            assert ready.wait(10)
            observed = False
            deadline = time.monotonic() + 10
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                while time.monotonic() < deadline:
                    if observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pids[0]}):
                        observed = True
                        break
                    time.sleep(0.05)
            assert observed, "Source revocation did not wait for active read lock"
        finally:
            reader.rollback()
        assert future.result(timeout=10) == 2
    with repository._sessions() as reader:
        with pytest.raises(AccessDenied):
            SqlAlchemyOfficeSourceAccess().require(reader, grant, record)


@pytest.mark.parametrize("mode", ["engine", "connection", "driver", "transaction"])
def test_revocation_transaction_mode_prevents_partial_commits(repository, mode):
    from database_environment import local_database_connect_args
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.office_source_revocation import revoke_source_reader

    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL DBAPI autocommit")
    OfficeSourceBase.metadata.create_all(engine)
    with repository._sessions.begin() as session:
        session.add(OfficeSourceRow(workspace_id="ws", source_id="source", enabled=True, acl_revision=1))
        session.flush()
        session.add(OfficeSourceGrantRow(workspace_id="ws", source_id="source", actor_id="actor", can_read=True))
    alternate = None
    if mode == "engine":
        args = (
            {}
            if os.environ.get("CI") == "true"
            else local_database_connect_args(engine.url.render_as_string(hide_password=False))
        )
        alternate = create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args=args)
        selected = alternate.execution_options(**engine.get_execution_options())
    else:
        selected = engine
    try:
        with selected.connect() as connection:
            if mode == "connection":
                connection = connection.execution_options(isolation_level="AUTOCOMMIT")
            driver = connection.connection.dbapi_connection
            if mode == "driver":
                driver.autocommit = True
            try:
                with Session(connection) as writer:
                    if mode == "transaction":
                        assert (
                            revoke_source_reader(
                                writer, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1
                            )
                            == 2
                        )
                        assert writer.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read is False
                    else:
                        with pytest.raises(PersistenceError, match="office_source_isolation_invalid"):
                            revoke_source_reader(
                                writer, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1
                            )
                    writer.rollback()
            finally:
                if mode == "driver":
                    driver.autocommit = False
        with repository._sessions() as check:
            assert check.get(OfficeSourceRow, ("ws", "source")).acl_revision == 1
            assert check.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read is True
    finally:
        if alternate is not None:
            alternate.dispose()
