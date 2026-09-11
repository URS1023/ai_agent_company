"""CI-only real source transactions, ciphertext persistence and 0002 DDL/reflection.

Local collection skips every case. No production URL, key, source or table is used.
"""

import base64
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, select
from test_repository import repository as repository

from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher, SourceKeyring
from enterprise_platform.application.contracts import DeviceCreate, Principal
from enterprise_platform.application.errors import Conflict, NotFound
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.application.source_registration import EndpointPolicy, SourceEndpoint
from enterprise_platform.application.source_service import SourceService, StoredReadCatalog
from enterprise_platform.persistence.migrate_sources import require_initial_schema, source_statements
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.source_models import SourceBase, SourceVersionRow
from enterprise_platform.persistence.sources import SqlAlchemySourceRepository

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration tests are CI-only")


@pytest.fixture
def managed_sources(repository):
    bind = repository._sessions.kw["bind"]
    if bind.dialect.name == "postgresql":
        schema = bind.get_execution_options()["schema_translate_map"][None]
        # Exercise actual PG reflection in this disposable schema only. Production's
        # global object inventory remains covered by the guarded CLI unit tests.
        real = inspect(bind)

        class ScopedInspector:
            def get_schema_names(self):
                return [schema]

            def __getattr__(self, name):
                return getattr(real, name)

        require_initial_schema(ScopedInspector(), schema=schema)
        with bind.begin() as connection:
            for statement in source_statements():
                connection.exec_driver_sql(statement.replace("public.", f'"{schema}".'))
    else:
        SourceBase.metadata.create_all(bind)
    source_repo = SqlAlchemySourceRepository(repository._sessions)
    keyring = SourceKeyring.model_validate(
        {
            "active_key_id": "fixture-key",
            "keys": [{"key_id": "fixture-key", "key": base64.urlsafe_b64encode(b"k" * 32).decode()}],
        }
    )
    cipher = AesGcmSourceCipher(keyring)
    policy = EndpointPolicy((SourceEndpoint(kind="db", host="collector.test", port=5432, dialect="postgresql"),))
    service = SourceService(source_repo, repository, cipher=cipher, endpoints=policy)
    principal = Principal(workspace_id="w", actor_id="a", workspace_role="owner", display_name="Owner")
    device = repository.create_device("w", DeviceCreate(device_code="0001", name="Pump"), actor_id="a")
    draft = SourceDraft.model_validate(
        {
            "name": "Collected records",
            "device_ids": [device.id],
            "device_parameter": "device",
            "device_column": "device_code",
            "read_only_confirmed": True,
            "connection": {
                "kind": "db",
                "dialect": "postgresql",
                "host": "collector.test",
                "port": 5432,
                "database": "records",
                "username": "readonly-fixture-user",
                "password": "fixture-private-password",
                "allowed_tables": ["measurements"],
                "sql": "SELECT * FROM measurements WHERE device_code = :device",
            },
        }
    )
    yield service, source_repo, repository, cipher, policy, principal, draft


def test_source_history_ciphertext_audit_and_cross_workspace(managed_sources) -> None:
    service, repo, devices, cipher, policy, principal, draft = managed_sources
    first = service.create_source(principal, draft, request_key="create-1")
    update = draft.model_copy(
        update={
            "name": "renamed",
            "connection": draft.connection.model_copy(update={"username": None, "password": None}),
        }
    )
    second = service.update_source(principal, first.source_id, update, expected_revision=1)
    assert second.revision == 2
    assert service.create_source(principal, draft, request_key="create-1") == first
    assert repo.get("w", first.source_id).view == second
    assert repo.list("w", offset=0, limit=1).total == 1
    assert repo.list("other", offset=0, limit=1).total == 0
    with pytest.raises(NotFound):
        repo.get("other", first.source_id)
    catalog = StoredReadCatalog(repo, cipher, policy, ImmutableReadCatalog(()))
    old = catalog.resolve("w", first.source_id, first.source_revision, first.read_id, first.read_revision)
    assert "fixture-private-password" in old.connection.connection_url.get_secret_value()
    assert repo.find_read("w", first.source_id, first.source_revision, first.read_id, second.read_revision) is None
    with devices._sessions() as session:
        rows = session.scalars(select(SourceVersionRow)).all()
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "source")).all()
        assert len(rows) == 2 and len(events) == 2
        assert {row.revision for row in rows} == {1, 2}
        for row in rows:
            assert "fixture-private-password" not in row.public_json + row.ciphertext
            assert "readonly-fixture-user" not in row.public_json
        assert all("password" not in event.detail_json for event in events)


def test_source_cas_loser_never_appends_and_create_race_is_idempotent(managed_sources) -> None:
    service, repo, devices, cipher, policy, principal, draft = managed_sources
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.create_source(principal, draft, request_key="same"), range(2)))
    assert results[0] == results[1]
    first = results[0]

    def update(name):
        try:
            return service.update_source(
                principal, first.source_id, draft.model_copy(update={"name": name}), expected_revision=1
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(pool.map(update, ["one", "two"]))
    assert sum(result is not None for result in updates) == 1
    with devices._sessions() as session:
        assert len(session.scalars(select(SourceVersionRow)).all()) == 2
        assert len(session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "source")).all()) == 2


def test_deleted_device_rolls_back_head_version_and_audit_together(managed_sources) -> None:
    service, repo, devices, cipher, policy, principal, draft = managed_sources
    prepared = service._build(
        principal,
        draft,
        previous=None,
        cipher=cipher,
        request_key="rollback",
        request_hash="a" * 64,
        fingerprint_key_id="fixture-key",
    )
    devices.delete_device("w", draft.device_ids[0], expected_revision=1, actor_id="a")
    with pytest.raises(NotFound):
        repo.create(prepared, actor_id="a")
    assert repo.find_request("w", "rollback") is None
    assert repo.list("w", offset=0, limit=20).total == 0
    with devices._sessions() as session:
        assert not session.scalars(select(SourceVersionRow)).all()
        assert not session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "source")).all()


def test_explicit_ci_migrations_created_source_tables_in_public() -> None:
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.migrate import validate_target

    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated ENTERPRISE_TEST_DATABASE_URL is not configured")
    engine = create_engine(
        validate_target(url, "enterprise_test"), hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names(schema="public")) == {
            "enterprise_devices",
            "enterprise_bindings",
            "enterprise_runs",
            "enterprise_audit_events",
            "enterprise_source_heads",
            "enterprise_source_versions",
        }
        assert {
            column["name"] for column in inspector.get_columns("enterprise_source_versions", schema="public")
        } == set(SourceVersionRow.__table__.columns.keys())
        assert inspector.get_pk_constraint("enterprise_source_versions", schema="public")["constrained_columns"] == [
            "workspace_id",
            "source_id",
            "revision",
        ]
        fks = inspector.get_foreign_keys("enterprise_source_versions", schema="public")
        assert len(fks) == 1 and fks[0]["constrained_columns"] == ["workspace_id", "source_id"]
        assert fks[0]["referred_table"] == "enterprise_source_heads"
    finally:
        engine.dispose()
