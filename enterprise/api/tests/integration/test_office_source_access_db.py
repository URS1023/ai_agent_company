"""Two PostgreSQL connections verify revocation after an earlier transaction read."""

import hashlib
import os
from uuid import UUID

import pytest
from database_environment import database_tests_enabled
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_repository import repository as repository
from test_sources import managed_sources as managed_sources

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


@pytest.mark.parametrize("enabled", [True, False])
def test_registered_office_policy_obeys_original_source_lifecycle(managed_sources, enabled):
    import base64
    from io import BytesIO
    from unittest.mock import AsyncMock, patch
    from zipfile import ZipFile

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from enterprise_platform.adapters.office_templates import require_office_template
    from enterprise_platform.adapters.source_encryption import SourceKeyring
    from enterprise_platform.bootstrap import Settings, create_runtime
    from enterprise_platform.persistence.office_creation import (
        RegisteredOfficeCreationAccess,
        SqlAlchemyOfficeFileCreator,
    )
    from enterprise_platform.persistence.office_models import OfficeBase, OfficeFileRow
    from enterprise_platform.persistence.office_source_access import SqlAlchemyRegisteredOfficeSourceAccess

    service, _, devices, cipher, _, principal, draft = managed_sources
    first = service.create_source(principal, draft, request_key="office-original")
    engine = devices._sessions.kw["bind"]
    OfficeSourceBase.metadata.create_all(engine)
    with devices._sessions.begin() as session:
        session.add(
            OfficeSourceRow(
                workspace_id=principal.workspace_id, source_id=first.source_id, enabled=True, acl_revision=1
            )
        )
        session.flush()
        session.add(
            OfficeSourceGrantRow(
                workspace_id=principal.workspace_id,
                source_id=first.source_id,
                actor_id=principal.actor_id,
                can_read=True,
            )
        )
        session.add(
            OfficeSnapshotRow(
                workspace_id=principal.workspace_id,
                snapshot_id="snapshot",
                source_id=first.source_id,
                source_revision=first.source_revision,
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
            )
        )
    grant = OfficeGrant(principal.workspace_id, principal.actor_id, UUID(int=1), "read", 1)
    record = OfficeFileRecord(
        principal.workspace_id,
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
    policy = SqlAlchemyRegisteredOfficeSourceAccess(cipher)
    with devices._sessions() as reader:
        policy.require(reader, grant, record)
    OfficeBase.metadata.create_all(engine)
    creator = SqlAlchemyOfficeFileCreator(
        devices._sessions, RegisteredOfficeCreationAccess(policy, require_office_template)
    )
    assert creator.create(principal, record) == record
    assert creator.create(principal, record) == record
    if not enabled:
        service.update_source(
            principal, first.source_id, draft.model_copy(update={"enabled": False}), expected_revision=1
        )
    with devices._sessions() as reader:
        if enabled:
            policy.require(reader, grant, record)
        else:
            with pytest.raises(AccessDenied, match="source_disabled"):
                policy.require(reader, grant, record)

    if not enabled:
        with pytest.raises(AccessDenied, match="source_disabled"):
            creator.create(principal, record)
    config = Settings(
        database_url=SecretStr("postgresql+psycopg://unused:unused@127.0.0.1:55432/enterprise_test"),
        dify_console_url="http://127.0.0.1/console/api",
        dify_service_url="http://127.0.0.1/v1",
        allowed_origins=("http://127.0.0.1",),
        source_keyring=SourceKeyring.model_validate(
            {
                "active_key_id": "fixture-key",
                "keys": [{"key_id": "fixture-key", "key": base64.urlsafe_b64encode(b"k" * 32).decode()}],
            }
        ),
    )
    with (
        patch("enterprise_platform.bootstrap.create_engine") as engine_factory,
        patch(
            "enterprise_platform.adapters.dify_identity.DifyIdentityClient.resolve",
            new=AsyncMock(return_value=principal),
        ) as identity,
    ):
        engine_factory.return_value.execution_options.return_value = engine
        runtime = create_runtime(config)
        engine_factory.return_value.execution_options.assert_called_once_with(schema_translate_map={None: "public"})
        try:
            with TestClient(runtime.app) as client:
                path = f"/enterprise/api/v1/office/files/{grant.file_id}"
                headers = {"Origin": "http://127.0.0.1"}
                creation = {
                    "expected_actor_id": principal.actor_id,
                    "expected_workspace_id": principal.workspace_id,
                    "file_id": str(UUID(int=9)),
                    "kind": "document",
                    "template_id": record.template_id,
                    "template_revision": "1",
                    "source_snapshot_ids": ["snapshot"],
                    "units": [unit.model_dump(mode="json") for unit in record.content.units],
                }
                create_path = "/enterprise/api/v1/office/files"
                assert client.post(create_path, json=creation).status_code == 403
                assert (
                    client.post(create_path, json={**creation, "workspace_id": "other"}, headers=headers).status_code
                    == 422
                )
                # A transcript has no source bindings: only the expected-scope fence prevents a misplaced write.
                transcript = {**creation, "file_id": str(UUID(int=10)), "source_snapshot_ids": []}
                for changed_scope in ({"workspace_id": "other-workspace"}, {"actor_id": "other-actor"}):
                    identity.return_value = principal.model_copy(update=changed_scope)
                    assert client.post(create_path, json=transcript, headers=headers).status_code == 403
                    with devices._sessions() as check:
                        assert (
                            check.scalar(select(OfficeFileRow).where(OfficeFileRow.file_id == transcript["file_id"]))
                            is None
                        )
                identity.return_value = principal
                identity.return_value = principal.model_copy(update={"workspace_role": "normal"})
                assert client.post(create_path, json=creation, headers=headers).status_code == 403
                identity.return_value = principal
                created = client.post(create_path, json=creation, headers=headers)
                assert created.status_code == (201 if enabled else 403), created.text
                if enabled:
                    assert created.json()["revision"] == "1"
                    assert client.post(create_path, json=creation, headers=headers).json() == created.json()
                    changed = {
                        **creation,
                        "units": [
                            {
                                "unit_id": str(UUID(int=2)),
                                "kind": "paragraph",
                                "content": [{"text": "Different creation"}],
                            }
                        ],
                    }
                    assert client.post(create_path, json=changed, headers=headers).status_code == 409
                    fresh_path = f"{create_path}/{UUID(int=9)}"
                    assert client.get(fresh_path).json() == created.json()
                    fresh_doc = client.get(f"{fresh_path}/document?expected_revision=1")
                    assert fresh_doc.status_code == 200
                    with ZipFile(BytesIO(fresh_doc.content)) as package:
                        assert package.testzip() is None
                        assert b"Report" in package.read("word/document.xml")
                listing = client.get("/enterprise/api/v1/office/files")
                assert listing.status_code == 200, listing.text
                assert len(listing.json()["items"]) == 2 * int(enabled)
                assert client.get(path).status_code == (200 if enabled else 403)
                download = client.get(f"{path}/document?expected_revision=1")
                assert download.status_code == (200 if enabled else 403)
                assert download.headers["cache-control"] == "private, no-store"
                if enabled:
                    with ZipFile(BytesIO(download.content)) as package:
                        assert package.testzip() is None
                        assert b"Report" in package.read("word/document.xml")
                command = {
                    "request_id": str(UUID(int=3)),
                    "expected_revision": "1",
                    "replacements": [{"unit_id": str(UUID(int=2)), "content": [{"text": "Updated report"}]}],
                }
                headers = {"Origin": "http://127.0.0.1"}
                edited = client.post(f"{path}/edits", json=command, headers=headers)
                assert edited.status_code == (200 if enabled else 403), edited.text
                if enabled:
                    assert edited.json()["revision"] == "2"
                    assert edited.json()["units"][0]["unit_id"] == str(UUID(int=2))
                    assert client.post(f"{path}/edits", json=command, headers=headers).json() == edited.json()
                    stale = {**command, "request_id": str(UUID(int=4))}
                    assert client.post(f"{path}/edits", json=stale, headers=headers).status_code == 409
                    assert client.get(f"{path}/document?expected_revision=1").status_code == 409
                    exported = client.get(f"{path}/document?expected_revision=2")
                    assert exported.status_code == 200
                    with ZipFile(BytesIO(exported.content)) as package:
                        assert package.testzip() is None
                        assert b"Updated report" in package.read("word/document.xml")
                identity.return_value = principal.model_copy(update={"actor_id": "another-actor"})
                assert client.get("/enterprise/api/v1/office/files").json()["items"] == []
                assert client.get(path).status_code == 403
                assert client.get(f"{path}/document?expected_revision=2").status_code == 403
        finally:
            runtime.close()


def test_original_source_disable_waits_for_transaction_reader(managed_sources):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event, text

    from enterprise_platform.persistence.sources import require_enabled_source

    service, _, devices, cipher, _, principal, draft = managed_sources
    engine = devices._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Observable PostgreSQL original source update lock")
    first = service.create_source(principal, draft, request_key="original-source")
    ready = Event()
    pids = []

    def before_update(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE") and "enterprise_source_heads" in statement:
            pids.append(connection.scalar(text("SELECT pg_backend_pid()")))
            ready.set()

    event.listen(engine, "before_cursor_execute", before_update)
    try:
        with devices._sessions() as reader, ThreadPoolExecutor(max_workers=1) as pool:
            require_enabled_source(reader, cipher, principal.workspace_id, first.source_id)
            future = pool.submit(
                service.update_source,
                principal,
                first.source_id,
                draft.model_copy(update={"enabled": False}),
                expected_revision=1,
            )
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
                assert observed, "Original source update bypassed the reader lock"
            finally:
                reader.rollback()
            assert future.result(timeout=10).enabled is False
        with devices._sessions() as reader:
            with pytest.raises(AccessDenied, match="source_disabled"):
                require_enabled_source(reader, cipher, principal.workspace_id, first.source_id)
    finally:
        event.remove(engine, "before_cursor_execute", before_update)


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


def test_concurrent_identical_capture_stores_one_snapshot(repository):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from decimal import Decimal
    from threading import Barrier

    from sqlalchemy import func

    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef
    from enterprise_platform.persistence.office_snapshot_capture import capture_office_snapshot

    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL snapshot insert serialization")
    OfficeSourceBase.metadata.create_all(engine)
    with repository._sessions.begin() as session:
        session.add(OfficeSourceRow(workspace_id="ws", source_id="source", enabled=True, acl_revision=1))
        session.flush()
        session.add(OfficeSourceGrantRow(workspace_id="ws", source_id="source", actor_id="actor", can_read=True))
    principal = Principal(workspace_id="ws", actor_id="actor", workspace_role="normal", display_name="Actor")
    data = FrozenRows(
        SourceRef(workspace_id="ws", source_id="source", revision="r1"),
        "read",
        "r1",
        "a" * 64,
        datetime(2026, 9, 14, tzinfo=UTC),
        ("value",),
        ((Decimal("1.2300000000000000001"),),),
        (PageEvidence(1, 1, "b" * 64),),
    )
    ready = Barrier(2)
    identifier = UUID(int=50)

    def capture():
        with repository._sessions.begin() as session:
            ready.wait(timeout=10)
            return capture_office_snapshot(session, principal, identifier, data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(capture), pool.submit(capture)
        assert first.result(timeout=15) == second.result(timeout=15) == str(identifier)
    with repository._sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeSnapshotRow)) == 1
        stored = session.get(OfficeSnapshotRow, ("ws", str(identifier)))
        assert "1.2300000000000000001" in stored.payload_json


def test_cross_source_snapshot_collision_maps_to_conflict_without_overwrite(repository):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from threading import Barrier

    from sqlalchemy import event, func

    from enterprise_platform.application.contracts import Principal
    from enterprise_platform.application.errors import Conflict
    from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef
    from enterprise_platform.persistence.mapping import transaction
    from enterprise_platform.persistence.office_snapshot_capture import capture_office_snapshot

    engine = repository._sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL cross-source unique-key collision")
    OfficeSourceBase.metadata.create_all(engine)
    with repository._sessions.begin() as session:
        for source_id in ("left", "right"):
            session.add(OfficeSourceRow(workspace_id="ws", source_id=source_id, enabled=True, acl_revision=1))
        session.flush()
        for source_id in ("left", "right"):
            session.add(OfficeSourceGrantRow(workspace_id="ws", source_id=source_id, actor_id="actor", can_read=True))
    both_read_missing = Barrier(2)

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "enterprise_office_snapshots" in statement:
            both_read_missing.wait(timeout=10)

    principal = Principal(workspace_id="ws", actor_id="actor", workspace_role="normal", display_name="Actor")
    identifier = UUID(int=50)

    def capture(source_id):
        data = FrozenRows(
            SourceRef(workspace_id="ws", source_id=source_id, revision="r1"),
            "read",
            "r1",
            "a" * 64,
            datetime(2026, 9, 14, tzinfo=UTC),
            ("value",),
            ((source_id,),),
            (PageEvidence(1, 1, "b" * 64),),
        )
        try:
            with transaction(repository._sessions) as session:
                capture_office_snapshot(session, principal, identifier, data)
            return source_id, "committed"
        except Conflict as error:
            assert str(error) == "database_constraint_conflict"
            assert error.status_code == 409
            return source_id, error.code

    event.listen(engine, "after_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(capture, "left"), pool.submit(capture, "right")
            results = [first.result(timeout=20), second.result(timeout=20)]
    finally:
        event.remove(engine, "after_cursor_execute", synchronize)
    assert sorted(result for _, result in results) == ["committed", "conflict"]
    winner = next(source for source, result in results if result == "committed")
    with repository._sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeSnapshotRow)) == 1
        stored = session.get(OfficeSnapshotRow, ("ws", str(identifier)))
        assert stored.source_id == winner
        assert json.loads(stored.payload_json)["source"]["source_id"] == winner
