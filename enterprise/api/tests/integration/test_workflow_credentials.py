"""Credential transactions with explicit opt-in and isolated local fixtures."""

import base64
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from database_environment import ci_public_migration_enabled, database_tests_enabled
from pydantic import SecretStr
from sqlalchemy import inspect, select
from test_repository import repository as repository

from enterprise_platform.adapters.workflow_credential_encryption import (
    AesGcmCredentialCipher,
    CredentialKey,
    CredentialKeyring,
)
from enterprise_platform.application.errors import Conflict, NotFound
from enterprise_platform.application.workflow_credential_contracts import WorkflowCredential
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.workflow_credential_models import CredentialBase, WorkflowCredentialRow
from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


@pytest.fixture
def vault(repository):
    bind = repository._sessions.kw["bind"]
    CredentialBase.metadata.create_all(bind)
    key = CredentialKey(key_id="ci", key=SecretStr(base64.urlsafe_b64encode(b"a" * 32).decode()))
    cipher = AesGcmCredentialCipher(CredentialKeyring(active_key_id="ci", keys=(key,)))
    return SqlAlchemyWorkflowCredentialVault(repository._sessions, cipher), repository._sessions


def command():
    return WorkflowCredential(workspace_id="w", app_id="app", secret_ref="ref", api_key=SecretStr("ci-native-token"))


def test_create_race_atomic_replay_and_no_plaintext(vault):
    repo, sessions = vault
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.register(command(), actor_id="actor"), range(2)))
    assert results[0] == results[1]
    with sessions() as session:
        rows = session.scalars(select(WorkflowCredentialRow)).all()
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_credential")
        ).all()
        assert len(rows) == 1 and len(events) == 1
        assert command().api_key.get_secret_value() not in str(rows[0].__dict__) + str(events[0].__dict__)
    assert repo.resolve("w", "app", "ref") == command().api_key
    with pytest.raises(NotFound):
        repo.resolve("other", "app", "ref")
    with pytest.raises(NotFound):
        repo.resolve("w", "other", "ref")
    with pytest.raises(Conflict):
        repo.register(
            WorkflowCredential(workspace_id="w", app_id="app", secret_ref="ref", api_key=SecretStr("different")),
            actor_id="actor",
        )


def test_revision_race_has_one_audit_and_never_unrevokes(vault):
    repo, sessions = vault
    repo.register(command(), actor_id="actor")

    def revoke(_):
        try:
            return repo.revoke("w", "app", "ref", expected_revision=1, actor_id="actor")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revoke, range(2)))
    assert sum(item is not None for item in results) == 1
    assert not repo.reseal("w", "app", "ref", expected_revision=2, actor_id="actor").active
    with pytest.raises(Conflict, match="workflow_credential_revoked"):
        repo.resolve("w", "app", "ref")
    with pytest.raises(Conflict):
        repo.register(command(), actor_id="actor")
    with sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "workflow_credential")
        ).all()
        assert len(events) == 3


def test_audit_failure_rolls_back_encrypted_insert(vault, monkeypatch):
    import enterprise_platform.persistence.workflow_credentials as module
    from enterprise_platform.application.errors import PersistenceError

    repo, sessions = vault

    def fail(*args, **kwargs):
        raise PersistenceError("test_audit_failure")

    monkeypatch.setattr(module, "_audit", fail)
    with pytest.raises(PersistenceError):
        repo.register(command(), actor_id="actor")
    with sessions() as session:
        assert session.scalar(select(WorkflowCredentialRow)) is None


def test_explicit_ci_0004_public_schema():
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.migrate import validate_target

    if not ci_public_migration_enabled(os.environ, "ENTERPRISE_CI_CREDENTIAL_MIGRATED_PUBLIC"):
        pytest.skip("Public 0004 smoke requires explicit CLI migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated enterprise database is not configured")
    engine = create_engine(
        validate_target(url, "enterprise_test"), hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        reflected = inspect(engine)
        assert {
            column["name"] for column in reflected.get_columns("enterprise_workflow_credentials", schema="public")
        } == set(WorkflowCredentialRow.__table__.c.keys())
        assert reflected.get_pk_constraint("enterprise_workflow_credentials", schema="public")[
            "constrained_columns"
        ] == ["workspace_id", "app_id", "secret_ref"]
    finally:
        engine.dispose()
