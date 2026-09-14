"""Real setup transactions and reviewed DDL with explicit disposable-fixture opt-in."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from database_environment import ci_public_migration_enabled, database_tests_enabled
from sqlalchemy import inspect, select
from test_repository import repository as repository

from enterprise_platform.application.contracts import DeviceCreate
from enterprise_platform.application.errors import Conflict, NotFound
from enterprise_platform.application.workflow_setup_contracts import SetupView
from enterprise_platform.application.workflow_setup_ports import StoredSetup
from enterprise_platform.persistence.migrate_sources import source_statements
from enterprise_platform.persistence.migrate_workflow_setups import (
    require_setup_prerequisites,
    workflow_setup_statements,
)
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.source_models import SourceBase
from enterprise_platform.persistence.workflow_setup_models import SetupBase, WorkflowSetupRow
from enterprise_platform.persistence.workflow_setups import SqlAlchemyWorkflowSetupRepository

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


@pytest.fixture
def setups(repository):
    bind = repository._sessions.kw["bind"]
    if bind.dialect.name == "postgresql":
        schema = bind.get_execution_options()["schema_translate_map"][None]
        with bind.begin() as connection:
            for statement in source_statements():
                connection.exec_driver_sql(statement.replace("public.", f'"{schema}".'))
        real = inspect(bind)

        class ScopedInspector:
            def get_schema_names(self):
                return [schema]

            def __getattr__(self, name):
                return getattr(real, name)

        require_setup_prerequisites(ScopedInspector(), schema=schema)
        with bind.begin() as connection:
            for statement in workflow_setup_statements():
                connection.exec_driver_sql(statement.replace("public.", f'"{schema}".'))
    else:
        SourceBase.metadata.create_all(bind)
        SetupBase.metadata.create_all(bind)
    device = repository.create_device("w", DeviceCreate(device_code="0001", name="Pump"), actor_id="a")
    now = datetime.now(UTC)
    command = StoredSetup(
        SetupView(
            id=uuid4().hex,
            workspace_id="w",
            device_id=device.id,
            scenario="alert",
            source_id="source",
            source_revision="source-v1",
            read_id="read",
            read_revision="read-v1",
            expected_source_revision=1,
            revision=1,
            state="queued",
            name="Pump alert",
            created_at=now,
            updated_at=now,
        ),
        "a",
        "request",
        "a" * 64,
    )
    return SqlAlchemyWorkflowSetupRepository(repository._sessions), repository, command


def test_setup_create_replay_and_workspace_isolation(setups):
    repo, devices, command = setups
    first = repo.create(command, actor_id="a")
    assert repo.create(command, actor_id="a") == first
    with pytest.raises(Conflict):
        repo.create(replace(command, request_hash="b" * 64), actor_id="a")
    with pytest.raises(NotFound):
        repo.get("other", first.view.id)
    assert repo.find_request("other", command.request_key) is None
    assert repo.list("other", device_id=first.view.device_id, scenario="alert", offset=0, limit=20).total == 0
    assert repo.list("w", device_id=first.view.device_id, scenario="alert", offset=0, limit=20).items == (first.view,)
    with devices._sessions() as session:
        assert len(session.scalars(select(WorkflowSetupRow)).all()) == 1
        assert (
            len(session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_setup")).all())
            == 1
        )


def test_setup_concurrent_create_and_claim_has_single_owner(setups):
    repo, devices, command = setups
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(lambda _: repo.create(command, actor_id="a"), range(2)))
    assert created[0] == created[1]

    def claim(nonce):
        try:
            return repo.claim_import("w", command.view.id, expected_revision=1, nonce=nonce, actor_id="a")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["one", "two"]))
    assert sum(item is not None for item in claimed) == 1
    assert repo.get("w", command.view.id).view.state == "importing"
    with devices._sessions() as session:
        assert (
            len(session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_setup")).all())
            == 2
        )


def test_setup_finish_uses_nonce_once_and_never_reclaims_uncertainty(setups):
    repo, devices, command = setups
    repo.create(command, actor_id="a")
    repo.claim_import("w", command.view.id, expected_revision=1, nonce="owner", actor_id="a")
    kwargs = dict(state="uncertain", app_id=None, import_id=None, reason_code="response_lost", actor_id="a")
    with pytest.raises(Conflict):
        repo.finish_import("w", command.view.id, nonce="other", **kwargs)
    result = repo.finish_import("w", command.view.id, nonce="owner", **kwargs)
    assert result.view.revision == 3 and result.import_nonce is None
    with pytest.raises(Conflict):
        repo.finish_import("w", command.view.id, nonce="owner", **kwargs)
    with pytest.raises(Conflict):
        repo.claim_import("w", command.view.id, expected_revision=3, nonce="new", actor_id="a")
    with devices._sessions() as session:
        assert (
            len(session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_setup")).all())
            == 3
        )


def test_deleted_device_rolls_back_new_setup_and_preserves_existing_audit(setups):
    repo, devices, command = setups
    repo.create(command, actor_id="a")
    devices.delete_device("w", command.view.device_id, expected_revision=1, actor_id="a")
    new = replace(command, view=command.view.model_copy(update={"id": uuid4().hex}), request_key="new")
    with pytest.raises(NotFound):
        repo.create(new, actor_id="a")
    with pytest.raises(NotFound):
        repo.claim_import("w", command.view.id, expected_revision=1, nonce="owner", actor_id="a")
    assert repo.find_request("w", "new") is None
    assert repo.get("w", command.view.id) == command
    with devices._sessions() as session:
        assert len(session.scalars(select(WorkflowSetupRow)).all()) == 1
        assert (
            len(session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_setup")).all())
            == 1
        )


def test_explicit_ci_migration_created_setup_in_public():
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.migrate import validate_target
    from enterprise_platform.persistence.migrate_workflow_setups import prerequisite_metadata

    if not ci_public_migration_enabled(os.environ, "ENTERPRISE_CI_SETUP_MIGRATED_PUBLIC"):
        pytest.skip("Public 0003 smoke is opt-in after explicit CLI migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated ENTERPRISE_TEST_DATABASE_URL is not configured")
    engine = create_engine(
        validate_target(url, "enterprise_test"), hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        reflected = inspect(engine)
        assert set(reflected.get_table_names(schema="public")) == set(prerequisite_metadata().tables) | {
            "enterprise_workflow_setups"
        }
        assert {c["name"] for c in reflected.get_columns("enterprise_workflow_setups", schema="public")} == set(
            WorkflowSetupRow.__table__.c.keys()
        )
        assert reflected.get_pk_constraint("enterprise_workflow_setups", schema="public")["constrained_columns"] == [
            "workspace_id",
            "setup_id",
        ]
    finally:
        engine.dispose()


def test_claim_delete_finish_retains_native_identifiers_without_new_claim(setups):
    repo, devices, command = setups
    repo.create(command, actor_id="a")
    repo.claim_import("w", command.view.id, expected_revision=1, nonce="owner", actor_id="a")
    devices.delete_device("w", command.view.device_id, expected_revision=1, actor_id="a")
    completed = repo.finish_import(
        "w",
        command.view.id,
        nonce="owner",
        state="draft_ready",
        app_id="native-app",
        import_id="native-import",
        reason_code=None,
        actor_id="a",
    )
    assert repo.get("w", command.view.id) == completed
    assert completed.view.app_id == "native-app" and completed.view.import_id == "native-import"
    assert completed.view.revision == 3 and completed.import_nonce is None
    with pytest.raises(NotFound):
        repo.claim_import("w", command.view.id, expected_revision=3, nonce="new", actor_id="a")
    with pytest.raises(NotFound):
        repo.create(command, actor_id="a")
    with devices._sessions() as session:
        rows = session.scalars(select(WorkflowSetupRow)).all()
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_setup")).all()
        assert len(rows) == 1 and len(events) == 3
        assert rows[0].app_id == "native-app" and rows[0].import_id == "native-import"
