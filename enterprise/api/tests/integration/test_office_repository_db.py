"""CI-only real Office transactions, using the existing disposable-schema fixture."""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_repository import repository as repository
from test_sql_drafts_db import apply_test_schema_ddl

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.office_edits import OfficeFileRecord, OfficeGrant
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit
from enterprise_platform.persistence.mapping import utc_now
from enterprise_platform.persistence.migrate_office import office_statements, read_office_sql
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.office_documents import encode_office_record
from enterprise_platform.persistence.office_models import (
    OfficeBase,
    OfficeEditReceiptRow,
    OfficeFileRow,
    OfficeGrantRow,
    OfficeRevisionRow,
)
from enterprise_platform.persistence.office_repository import SqlAlchemyOfficeEditRepository

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration runs in CI only")


class NoSourceReferences:
    def require(self, session: Session, grant: OfficeGrant, record: OfficeFileRecord) -> None:
        if record.source_snapshot_ids:
            raise AccessDenied()


@pytest.fixture(params=["model", "migration-ddl"])
def office(repository, request):
    if request.param == "migration-ddl":
        if repository._sessions.kw["bind"].dialect.name != "postgresql":
            pytest.skip("Release Office DDL targets PostgreSQL; SQLite retains ORM coverage")
        read_office_sql()
        apply_test_schema_ddl(repository, office_statements())
    else:
        with repository._sessions() as session:
            OfficeBase.metadata.create_all(session.get_bind())
    grant = OfficeGrant("office-workspace", "actor", UUID(int=1), "edit", 1)
    original = OfficeFileRecord(
        "office-workspace",
        "template",
        1,
        (),
        OfficeRevision(
            file_id=grant.file_id,
            revision=1,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Original"),)),),
        ),
    )
    candidate = replace(
        original,
        content=OfficeRevision(
            file_id=grant.file_id,
            revision=2,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Changed"),)),),
        ),
    )
    document, now = encode_office_record(original), utc_now()
    with repository._sessions.begin() as session:
        session.add(
            OfficeFileRow(
                workspace_id=grant.workspace_id,
                file_id=str(grant.file_id),
                current_revision=1,
                acl_revision=1,
                created_by=grant.actor_id,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            OfficeGrantRow(
                workspace_id=grant.workspace_id,
                file_id=str(grant.file_id),
                actor_id=grant.actor_id,
                can_read=True,
                can_edit=True,
            )
        )
        session.add(
            OfficeRevisionRow(
                workspace_id=grant.workspace_id,
                file_id=str(grant.file_id),
                revision=1,
                document_json=document,
                document_hash=hashlib.sha256(document.encode()).hexdigest(),
                actor_id=grant.actor_id,
                created_at=now,
            )
        )
    return SqlAlchemyOfficeEditRepository(repository._sessions, NoSourceReferences()), grant, original, candidate


def save(office, request_id: UUID | None = None):
    store, grant, original, candidate = office
    return store.commit(
        grant=grant,
        expected=original,
        candidate=candidate,
        request_id=request_id if request_id is not None else UUID(int=3),
        command_hash="a" * 64,
    )


def counts(repository) -> tuple[int, int]:
    with repository._sessions() as session:
        return (
            session.scalar(select(func.count()).select_from(OfficeRevisionRow)),
            session.scalar(select(func.count()).select_from(OfficeEditReceiptRow)),
        )


def test_commit_preserves_history_and_replay_returns_saved_revision(office, repository):
    first, second = save(office), save(office)
    assert first.record.fingerprint() == second.record.fingerprint()
    assert counts(repository) == (2, 1)
    store, grant, _, candidate = office
    assert store.get(grant).fingerprint() == candidate.fingerprint()


def test_concurrent_identical_requests_create_one_revision_and_receipt(office, repository):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: save(office), range(2)))
    assert results[0].record.fingerprint() == results[1].record.fingerprint()
    assert counts(repository) == (2, 1)


def test_concurrent_distinct_requests_do_not_overwrite_each_other(office, repository):
    def attempt(number: int) -> str:
        try:
            save(office, UUID(int=number))
            return "saved"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, (3, 4)))
    assert sorted(results) == ["conflict", "saved"]
    assert counts(repository) == (2, 1)


def test_stale_acl_and_wrong_actor_are_denied(office, repository):
    store, grant, original, candidate = office
    with repository._sessions.begin() as session:
        head = session.scalar(select(OfficeFileRow).with_for_update())
        head.acl_revision = 2
    with pytest.raises(AccessDenied):
        save(office)
    with pytest.raises(AccessDenied):
        store.get(replace(grant, acl_revision=2, actor_id="other"))
    assert counts(repository) == (1, 0)


def test_audit_failure_rolls_back_revision_receipt_and_head(office, repository):
    def fail_audit(*args: object) -> None:
        raise RuntimeError("injected office audit failure")

    event.listen(AuditEventRow, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="injected office audit failure"):
            save(office)
    finally:
        event.remove(AuditEventRow, "before_insert", fail_audit)
    assert counts(repository) == (1, 0)
    with repository._sessions() as session:
        assert session.scalar(select(OfficeFileRow.current_revision)) == 1


def test_postgresql_second_writer_waits_on_the_file_head_lock(office, repository):
    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock-wait observation requires PostgreSQL")
    locked, release, second_attempt = Event(), Event(), Event()
    second_pid: list[int] = []

    class PausingSources(NoSourceReferences):
        def require(self, session: Session, grant: OfficeGrant, record: OfficeFileRecord) -> None:
            super().require(session, grant, record)
            if not locked.is_set():
                locked.set()
                if not release.wait(45):
                    raise RuntimeError("Timed out waiting to release first Office writer")

    def observe_second(connection, cursor, statement, parameters, context, executemany):
        if locked.is_set() and "enterprise_office_files" in statement and "FOR UPDATE" in statement:
            second_pid.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            second_attempt.set()

    first = (SqlAlchemyOfficeEditRepository(repository._sessions, PausingSources()), *office[1:])
    event.listen(engine, "before_cursor_execute", observe_second)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first_result = workers.submit(save, first, UUID(int=3))
            try:
                assert locked.wait(10)
                second_result = workers.submit(save, office, UUID(int=4))
                assert second_attempt.wait(10)
                deadline, observed_wait = time.monotonic() + 10, False
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while time.monotonic() < deadline:
                        observed_wait = (
                            observer.exec_driver_sql(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = %s",
                                (second_pid[0],),
                            ).scalar_one_or_none()
                            is True
                        )
                        if observed_wait:
                            break
                        release.wait(0.02)
                assert observed_wait, "Second writer never reached an observable PostgreSQL lock wait"
            finally:
                release.set()
            assert first_result.result(timeout=10).record.content.revision == 2
            with pytest.raises(Conflict):
                second_result.result(timeout=10)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe_second)
    assert counts(repository) == (2, 1)
