"""CI-only verification of the explicitly migrated activation schema; no import I/O."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select
from test_repository import repository as repository
from test_workflow_enrollment import enrolled as enrolled
from test_workflow_enrollment import store, token_claim
from test_workflow_provisioning import WS
from test_workflow_provisioning import journal as journal

from enterprise_platform.application.assessments import AlertSpecification, AlertVariable, ImmutableSpecificationCatalog
from enterprise_platform.application.contracts import Binding
from enterprise_platform.application.errors import Conflict, PersistenceError
from enterprise_platform.application.source_contracts import SourceView
from enterprise_platform.application.source_ports import SealedSource, StoredSource
from enterprise_platform.application.workflow_activation_contracts import create_activation
from enterprise_platform.application.workflow_plugin_profiles import (
    PluginCredentialProfile,
    WorkflowPluginProfileRegistry,
)
from enterprise_platform.domain.rules import AlertRule
from enterprise_platform.persistence.migrate import validate_target
from enterprise_platform.persistence.migrate_sources import require_schema
from enterprise_platform.persistence.migrate_workflow_activation import prerequisite_metadata
from enterprise_platform.persistence.models import AuditEventRow, BindingRow
from enterprise_platform.persistence.source_models import SourceBase, SourceHeadRow
from enterprise_platform.persistence.sources import source_version_row
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup
from enterprise_platform.persistence.workflow_activation_models import ActivationBase, WorkflowActivationRow
from enterprise_platform.persistence.workflow_activation_repository import SqlAlchemyWorkflowActivationRepository

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration runs in CI only")


@pytest.fixture
def activation_ready(enrolled):
    enrollment_repo, base, initial, _ = enrolled
    token_claim(enrollment_repo, initial)
    enrollment = store(enrollment_repo).view
    p = enrollment.provisioning
    sessions = base._sessions
    for metadata in (ActivationBase.metadata, SourceBase.metadata):
        metadata.create_all(sessions.kw["bind"])
    now = datetime.now(UTC)
    source = SourceView.model_validate(
        dict(
            workspace_id=WS,
            source_id=p.source_id,
            source_revision=p.source_revision,
            read_id=p.read_id,
            read_revision=p.read_revision,
            revision=p.expected_source_revision,
            name="Activation source",
            device_ids=(p.device_id,),
            device_parameter="device",
            device_column="device_code",
            connection=dict(
                kind="db",
                dialect="postgresql",
                host="collector.test",
                port=5432,
                database="records",
                allowed_tables=("measurements",),
                sql="SELECT * FROM measurements WHERE device_code = :device",
                credentials_configured=True,
            ),
            created_at=now,
            updated_at=now,
        )
    )
    with sessions.begin() as session:
        session.add(
            SourceHeadRow(workspace_id=WS, source_id=p.source_id, read_id=p.read_id, revision=1, request_key="source")
        )
        session.flush()
        # Activation reads public source metadata only; source decryption is a separate suite.
        session.add(
            source_version_row(StoredSource(source, SealedSource("ci", "unused", "unused"), "source", "a" * 64, "ci"))
        )
    profile = PluginCredentialProfile(
        workspace_id=WS,
        config_ref=p.config_ref,
        config_revision=p.config_revision,
        origin="https://enterprise.internal",
        expected_plugin_unique_identifier="enterprise/plugin:1@digest",
        master_key_id="ci",
        master_secret=SecretStr("fixture-master-material-12345678901234567890"),
    )
    profiles = WorkflowPluginProfileRegistry((profile,))
    specifications = ImmutableSpecificationCatalog(
        (
            AlertSpecification(
                workspace_id=WS,
                specification_revision="spec-1",
                variables=(AlertVariable(name="temperature", column="temp", kind="decimal"),),
                rules=(AlertRule("hot", "rule-1", "temperature > 85", "high", "Too hot"),),
            ),
        )
    )
    assert enrollment.publication and enrollment.credential
    binding = Binding(
        id="activation-binding",
        workspace_id=WS,
        device_id=p.device_id,
        scenario=p.scenario,
        revision=1,
        app_id=p.app_id,
        workflow_id=UUID(enrollment.publication.workflow_id),
        specification_revision="spec-1",
        secret_ref=enrollment.credential.secret_ref,
        source_id=p.source_id,
        source_revision=p.source_revision,
        read_id=p.read_id,
        read_revision=p.read_revision,
        created_at=now,
        updated_at=now,
    )
    value = create_activation(enrollment, binding, profile, activation_id=str(UUID(int=51)), actor_id="actor", now=now)
    return SqlAlchemyWorkflowActivationRepository(sessions, profiles, specifications), base, value, profiles


def test_activation_and_revocation_each_have_one_concurrent_winner(activation_ready):
    repo, base, value, profiles = activation_ready

    def create(_):
        try:
            return repo.create(value, actor_id="actor")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(result is not None for result in pool.map(create, range(2))) == 1
    assert repo.find_enrollment(WS, value.enrollment.id) == value
    resolver = SqlAlchemyActiveExecutionKeyLookup(base._sessions, profiles)
    assert resolver.resolve(value.key_id, workflow_id=value.binding.workflow_id) is not None

    def revoke(_):
        try:
            return repo.revoke(WS, value.id, expected_revision=1, actor_id="actor")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(result is not None for result in pool.map(revoke, range(2))) == 1
    assert resolver.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None
    assert repo.get(WS, value.id).revision == 2
    with base._sessions() as session:
        assert len(session.scalars(select(BindingRow)).all()) == 1
        assert len(session.scalars(select(WorkflowActivationRow)).all()) == 1
        events = session.scalars(select(AuditEventRow)).all()
        for event in ("binding.created", "workflow_activation_created", "workflow_activation_revoked"):
            assert sum(row.event_type == event for row in events) == 1


@pytest.mark.parametrize("fail_at", [1, 2])
def test_activation_audit_failure_rolls_back_binding_and_grant(activation_ready, monkeypatch, fail_at):
    import enterprise_platform.persistence.workflow_activation_repository as module

    repo, base, value, profiles = activation_ready
    original = module.audit
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise PersistenceError("injected_audit_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        repo.create(value, actor_id="actor")
    with base._sessions() as session:
        assert session.scalar(select(BindingRow)) is None
        assert session.scalar(select(WorkflowActivationRow)) is None
    assert repo.find_enrollment(WS, value.enrollment.id) is None
    assert (
        SqlAlchemyActiveExecutionKeyLookup(base._sessions, profiles).resolve(
            value.key_id, workflow_id=value.binding.workflow_id
        )
        is None
    )


def test_revocation_audit_failure_preserves_active_grant(activation_ready, monkeypatch):
    import enterprise_platform.persistence.workflow_activation_repository as module

    repo, base, value, profiles = activation_ready
    repo.create(value, actor_id="actor")

    def fail(*args, **kwargs):
        raise PersistenceError("injected_audit_failure")

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        repo.revoke(WS, value.id, expected_revision=1, actor_id="actor")
    assert repo.get(WS, value.id) == value
    assert (
        SqlAlchemyActiveExecutionKeyLookup(base._sessions, profiles).resolve(
            value.key_id, workflow_id=value.binding.workflow_id
        )
        is not None
    )


def test_explicit_ci_0007_public_schema():
    if os.environ.get("ENTERPRISE_CI_ACTIVATION_MIGRATED_PUBLIC") != "1":
        pytest.skip("Requires explicit CI 0007 migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    assert url, "Explicit migration gate requires its dedicated database"
    metadata = prerequisite_metadata()
    for table in ActivationBase.metadata.sorted_tables:
        table.to_metadata(metadata)
    assert len(metadata.tables) == 11
    engine = create_engine(validate_target(url, "enterprise_test"), hide_parameters=True)
    try:
        require_schema(inspect(engine), metadata, schema="public")
    finally:
        engine.dispose()
