"""Explicitly enabled disposable transactional provisioning journal tests; local collection performs no I/O."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from database_environment import ci_public_migration_enabled, database_tests_enabled
from sqlalchemy import inspect, select
from test_repository import repository as repository

from enterprise_platform.application.contracts import DeviceCreate
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workflow_provisioning_contracts import (
    PhaseOutcome,
    ReadDraftCommand,
    initialize_provisioning,
)
from enterprise_platform.application.workflow_setup_contracts import SetupView
from enterprise_platform.application.workflow_setup_ports import StoredSetup
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.workflow_provisioning import SqlAlchemyWorkflowProvisioningRepository
from enterprise_platform.persistence.workflow_provisioning_models import ProvisioningBase, WorkflowProvisioningRow
from enterprise_platform.persistence.workflow_setup_models import SetupBase
from enterprise_platform.persistence.workflow_setups import setup_row

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)
WS = str(UUID(int=1))
APP = str(UUID(int=2))
NONCE = str(UUID(int=3))


@pytest.fixture
def journal(repository):
    sessions = repository._sessions
    bind = sessions.kw["bind"]
    SetupBase.metadata.create_all(bind)
    ProvisioningBase.metadata.create_all(bind)
    device = repository.create_device(WS, DeviceCreate(device_code="provision", name="Provision"), actor_id="actor")
    now = datetime.now(UTC)
    setup = SetupView(
        id="setup",
        workspace_id=WS,
        device_id=device.id,
        scenario="alert",
        source_id="source",
        source_revision="s1",
        read_id="read",
        read_revision="r1",
        expected_source_revision=1,
        expected_binding_revision=None,
        revision=3,
        state="draft_ready",
        name="Draft",
        app_id=APP,
        import_id="import",
        created_at=now,
        updated_at=now,
    )
    with sessions.begin() as session:
        session.add(setup_row(StoredSetup(setup, "creator", "setup-key", "a" * 64)))
    value = initialize_provisioning(
        setup,
        actor_id="actor",
        operation_id=str(UUID(int=4)),
        config_ref="config",
        config_revision=1,
        request_key="provision-key",
        now=now,
    )
    return SqlAlchemyWorkflowProvisioningRepository(sessions), repository, value


def command():
    return ReadDraftCommand(kind="read_draft", operation_id=str(UUID(int=5)), app_id=APP)


def test_request_and_claim_races_have_one_owner(journal):
    repo, base, value = journal
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: repo.create(value, actor_id="actor"), range(2)))
    assert values == [value, value]

    def claim(_):
        try:
            return repo.claim(WS, value.view.id, command=command(), nonce=NONCE, expected_revision=1, actor_id="actor")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert sum(item is not None for item in claimed) == 1
    with base._sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_provisioning")
        ).all()
        assert len(events) == 2


def test_owned_finish_survives_device_delete_and_never_reclaims(journal):
    repo, base, value = journal
    repo.create(value, actor_id="actor")
    repo.claim(WS, value.view.id, command=command(), nonce=NONCE, expected_revision=1, actor_id="actor")
    base.delete_device(WS, value.view.device_id, expected_revision=1, actor_id="actor")
    outcome = PhaseOutcome(state="uncertain", reason_code="transport_unknown")
    finished = repo.finish(WS, value.view.id, outcome=outcome, nonce=NONCE, expected_revision=2, actor_id="actor")
    assert finished.view.state == "uncertain" and finished.claim_nonce is None
    with pytest.raises(Conflict):
        repo.finish(WS, value.view.id, outcome=outcome, nonce=NONCE, expected_revision=2, actor_id="actor")
    with pytest.raises(NotFound):
        repo.claim(WS, value.view.id, command=command(), nonce=NONCE, expected_revision=3, actor_id="actor")
    assert repo.list(WS, setup_id="setup").total == 1


def test_audit_failure_rolls_back_create(journal, monkeypatch):
    import enterprise_platform.persistence.workflow_provisioning as module

    repo, base, value = journal

    def fail(*args, **kwargs):
        raise PersistenceError("test_audit_failure")

    monkeypatch.setattr(module, "_audit", fail)
    with pytest.raises(PersistenceError):
        repo.create(value, actor_id="actor")
    with base._sessions() as session:
        assert session.scalar(select(WorkflowProvisioningRow)) is None


def test_explicit_ci_0005_public_schema():
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.migrate import validate_target

    if not ci_public_migration_enabled(os.environ, "ENTERPRISE_CI_PROVISIONING_MIGRATED_PUBLIC"):
        pytest.skip("Public0005 smoke requires explicit CLI migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated enterprise database is not configured")
    engine = create_engine(
        validate_target(url, "enterprise_test"), hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        reflected = inspect(engine)
        assert len(reflected.get_table_names(schema="public")) == 9
        assert {
            column["name"] for column in reflected.get_columns("enterprise_workflow_provisioning", schema="public")
        } == set(WorkflowProvisioningRow.__table__.c.keys())
    finally:
        engine.dispose()
