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


def test_database_authorization_drives_real_edit_service(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_edits import OfficeEditCommand, OfficeEditService
    from enterprise_platform.domain.office_revision import UnitReplacement

    store, grant, original, candidate = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    service = OfficeEditService(store, store)
    assert service.read(principal, grant.file_id).fingerprint() == original.fingerprint()
    result = service.edit(
        principal,
        grant.file_id,
        OfficeEditCommand(
            request_id=UUID(int=9),
            expected_revision=1,
            replacements=(UnitReplacement(unit_id=UUID(int=2), content=(OfficeText(text="Changed"),)),),
        ),
    )
    assert result.record.fingerprint() == candidate.fingerprint()
    assert counts(repository) == (2, 1)
    for wrong in [
        principal.model_copy(update={"actor_id": "other"}),
        principal.model_copy(update={"workspace_id": "other-workspace"}),
    ]:
        with pytest.raises(AccessDenied):
            service.read(wrong, grant.file_id)


def test_database_authorization_rechecks_acl_between_grant_and_read(office, repository):
    from enterprise_platform.application.contracts import Principal

    store, grant, _, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    resolved = store.authorize(principal, grant.file_id, "read")
    with repository._sessions.begin() as session:
        head = session.scalar(
            select(OfficeFileRow)
            .where(OfficeFileRow.workspace_id == grant.workspace_id, OfficeFileRow.file_id == str(grant.file_id))
            .with_for_update()
        )
        head.acl_revision += 1
        permission = session.get(OfficeGrantRow, (grant.workspace_id, str(grant.file_id), grant.actor_id))
        permission.can_read = False
        permission.can_edit = False
    with pytest.raises(AccessDenied):
        store.get(resolved)
    with pytest.raises(AccessDenied):
        store.authorize(principal, grant.file_id, "read")
    assert counts(repository) == (1, 0)


class FixtureCreationAccess:
    def require(self, session: Session, principal, record: OfficeFileRecord) -> None:
        if (
            principal.workspace_id != "office-workspace"
            or principal.actor_id != "actor"
            or record.template_id != "template"
            or record.template_revision != 1
            or record.source_snapshot_ids
        ):
            raise AccessDenied()


def test_create_file_then_authorize_and_replay_from_real_database(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator

    store, grant, original, _ = office
    initial = replace(original, content=original.content.model_copy(update={"file_id": UUID(int=42)}))
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    creator = SqlAlchemyOfficeFileCreator(repository._sessions, FixtureCreationAccess())
    first = creator.create(principal, initial)
    second = creator.create(principal, initial)
    assert first.fingerprint() == second.fingerprint() == initial.fingerprint()
    current_grant = store.authorize(principal, initial.content.file_id, "edit")
    assert store.get(current_grant).fingerprint() == initial.fingerprint()
    assert counts(repository) == (2, 0)
    with repository._sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeFileRow)) == 2
        assert session.scalar(select(func.count()).select_from(OfficeGrantRow)) == 2
        assert (
            session.scalar(
                select(func.count()).select_from(AuditEventRow).where(AuditEventRow.event_type == "office_file_created")
            )
            == 1
        )


def test_concurrent_initial_file_creation_replays_one_winner(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator

    _, grant, original, _ = office
    initial = replace(original, content=original.content.model_copy(update={"file_id": UUID(int=42)}))
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    creator = SqlAlchemyOfficeFileCreator(repository._sessions, FixtureCreationAccess())
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: creator.create(principal, initial), range(2)))
    assert results[0].fingerprint() == results[1].fingerprint() == initial.fingerprint()
    assert counts(repository) == (2, 0)


@pytest.mark.parametrize("failure", ["policy", "audit"])
def test_initial_creation_failure_rolls_back_head_grant_history_and_audit(office, repository, failure):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator

    _, grant, original, _ = office
    initial = replace(original, content=original.content.model_copy(update={"file_id": UUID(int=42)}))
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )

    class DeniedCreation:
        def require(self, session, actor, record):
            raise AccessDenied()

    def fail_audit(mapper, connection, target):
        if target.event_type == "office_file_created":
            raise RuntimeError("injected_creation_audit_failure")

    creator = SqlAlchemyOfficeFileCreator(
        repository._sessions, DeniedCreation() if failure == "policy" else FixtureCreationAccess()
    )
    event.listen(AuditEventRow, "before_insert", fail_audit)
    try:
        with pytest.raises(AccessDenied if failure == "policy" else RuntimeError):
            creator.create(principal, initial)
    finally:
        event.remove(AuditEventRow, "before_insert", fail_audit)
    assert counts(repository) == (1, 0)
    with repository._sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeFileRow)) == 1
        assert session.scalar(select(func.count()).select_from(OfficeGrantRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0


def test_creation_replay_waits_for_edit_head_before_taking_source_lock(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator

    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Observable lock ordering requires PostgreSQL")
    _, grant, original, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    locked, release, attempted, creation_policy_entered = Event(), Event(), Event(), Event()
    waiting_pid = []

    class LockedSources:
        def require(self, session, actor_grant, record):
            session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(1162761499)")
            locked.set()
            assert release.wait(45), "Edit lock release timed out"

    class CreationSources(FixtureCreationAccess):
        def require(self, session, actor, record):
            creation_policy_entered.set()
            session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(1162761499)")
            super().require(session, actor, record)

    def observe_waiter(connection, cursor, statement, parameters, context, executemany):
        if locked.is_set() and "enterprise_office_files" in statement and "FOR UPDATE" in statement:
            waiting_pid.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            attempted.set()

    writer = (SqlAlchemyOfficeEditRepository(repository._sessions, LockedSources()), *office[1:])
    creator = SqlAlchemyOfficeFileCreator(repository._sessions, CreationSources())
    event.listen(engine, "before_cursor_execute", observe_waiter)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            edit = workers.submit(save, writer)
            try:
                assert locked.wait(10)
                replay = workers.submit(creator.create, principal, original)
                assert attempted.wait(10)
                deadline, observed = time.monotonic() + 10, False
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while time.monotonic() < deadline:
                        observed = (
                            observer.exec_driver_sql(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = %s",
                                (waiting_pid[0],),
                            ).scalar_one_or_none()
                            is True
                        )
                        if observed:
                            break
                        release.wait(0.02)
                assert observed, "Creation replay did not wait on the existing file head"
                assert not creation_policy_entered.is_set(), "Source lock attempted before file head"
            finally:
                release.set()
            assert edit.result(timeout=10).record.content.revision == 2
            assert replay.result(timeout=10).fingerprint() == original.fingerprint()
            assert creation_policy_entered.is_set()
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe_waiter)
    assert counts(repository) == (2, 1)


class FixtureOfficeManagement:
    def require(self, session, principal, head, command):
        if principal.workspace_id != head.workspace_id or principal.actor_id != head.created_by:
            raise AccessDenied()
        if command.actor_id != "actor":
            raise AccessDenied()


def test_acl_adapter_revocation_invalidates_existing_grant(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_permissions import OfficePermissionChange
    from enterprise_platform.persistence.office_acl import SqlAlchemyOfficeAcl

    store, grant, _, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    previous = store.authorize(principal, grant.file_id, "read")
    acl = SqlAlchemyOfficeAcl(repository._sessions, FixtureOfficeManagement())
    assert (
        acl.change(
            principal,
            grant.file_id,
            OfficePermissionChange(actor_id="actor", expected_acl_revision=1, can_read=False, can_edit=False),
        )
        == 2
    )
    with pytest.raises(AccessDenied):
        store.get(previous)
    with pytest.raises(AccessDenied):
        store.authorize(principal, grant.file_id, "read")
    assert counts(repository) == (1, 0)


def test_concurrent_acl_changes_have_one_version_winner(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_permissions import OfficePermissionChange
    from enterprise_platform.persistence.office_acl import SqlAlchemyOfficeAcl

    _, grant, _, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    acl = SqlAlchemyOfficeAcl(repository._sessions, FixtureOfficeManagement())

    def attempt(can_read):
        try:
            return acl.change(
                principal,
                grant.file_id,
                OfficePermissionChange(actor_id="actor", expected_acl_revision=1, can_read=can_read, can_edit=False),
            )
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, [True, False]))
    assert results.count(2) == 1
    assert results.count("conflict") == 1
    with repository._sessions() as session:
        head = session.get(OfficeFileRow, (grant.workspace_id, str(grant.file_id)))
        assert (head.acl_revision, head.current_revision) == (2, 1)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "office_permissions_changed")
            )
            == 1
        )


def test_acl_audit_failure_rolls_back_permission_and_acl_version(office, repository):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_permissions import OfficePermissionChange
    from enterprise_platform.persistence.office_acl import SqlAlchemyOfficeAcl

    store, grant, original, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="Actor"
    )
    acl = SqlAlchemyOfficeAcl(repository._sessions, FixtureOfficeManagement())

    def fail_audit(mapper, connection, target):
        if target.event_type == "office_permissions_changed":
            raise RuntimeError("injected_acl_audit_failure")

    event.listen(AuditEventRow, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="injected_acl_audit_failure"):
            acl.change(
                principal,
                grant.file_id,
                OfficePermissionChange(actor_id="actor", expected_acl_revision=1, can_read=False, can_edit=False),
            )
    finally:
        event.remove(AuditEventRow, "before_insert", fail_audit)
    assert store.get(grant).fingerprint() == original.fingerprint()
    with repository._sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0


def test_directory_reads_only_current_actor_grants_and_omits_revoked_files(office):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_directory import OfficeDirectoryService
    from enterprise_platform.application.office_edits import OfficeEditService
    from enterprise_platform.persistence.office_directory import SqlAlchemyOfficeDirectory

    store, grant, original, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="User"
    )
    directory = OfficeDirectoryService(SqlAlchemyOfficeDirectory(store._sessions), OfficeEditService(store, store))
    page = directory.list_files(principal)
    assert [item.file_id for item in page.items] == [original.content.file_id]
    assert page.next_offset is None
    assert directory.list_files(principal.model_copy(update={"actor_id": "other"})).items == ()
    assert directory.list_files(principal.model_copy(update={"workspace_id": "other"})).items == ()
    with store._sessions.begin() as session:
        permission = session.get(OfficeGrantRow, (grant.workspace_id, str(grant.file_id), grant.actor_id))
        permission.can_read = False
        permission.can_edit = False
    assert directory.list_files(principal).items == ()


def test_directory_checks_sources_after_candidate_scan_without_returning_content(office):
    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.office_directory import OfficeDirectoryService
    from enterprise_platform.application.office_edits import OfficeEditService
    from enterprise_platform.persistence.office_directory import SqlAlchemyOfficeDirectory

    class DeniedSources:
        def require(self, session, grant, record):
            raise AccessDenied()

    store, grant, _, _ = office
    principal = Principal(
        workspace_id=grant.workspace_id, actor_id=grant.actor_id, workspace_role="normal", display_name="User"
    )
    denied_store = SqlAlchemyOfficeEditRepository(store._sessions, DeniedSources())
    directory = OfficeDirectoryService(
        SqlAlchemyOfficeDirectory(store._sessions), OfficeEditService(denied_store, denied_store)
    )
    assert directory.list_files(principal).items == ()
