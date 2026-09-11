"""CI-only enrollment transactions; collection performs no database I/O."""

import base64
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import inspect, select
from test_repository import repository as repository
from test_workflow_provisioning import APP, NONCE, WS
from test_workflow_provisioning import journal as journal

from enterprise_platform.adapters.workflow_credential_encryption import (
    AesGcmCredentialCipher,
    CredentialKey,
    CredentialKeyring,
)
from enterprise_platform.application.errors import Conflict, PersistenceError
from enterprise_platform.application.workflow_enrollment_contracts import initialize_enrollment
from enterprise_platform.application.workflow_provisioning_contracts import (
    BindCredentialCommand,
    BindCredentialReceipt,
    PhaseOutcome,
    PrepareCredentialCommand,
    PrepareCredentialReceipt,
    PublishCommand,
    PublishReceipt,
    ReadDraftCommand,
    ReadDraftReceipt,
    claim_provisioning,
    finish_provisioning,
)
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.workflow_credential_models import CredentialBase, WorkflowCredentialRow
from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault
from enterprise_platform.persistence.workflow_enrollment_models import EnrollmentBase, WorkflowEnrollmentRow
from enterprise_platform.persistence.workflow_enrollment_repository import SqlAlchemyWorkflowEnrollmentRepository
from enterprise_platform.persistence.workflow_provisioning import provisioning_row

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration tests are CI-only")
DRAFT, CREDENTIAL, WORKFLOW, ENROLLMENT, SECRET, TOKEN = [str(UUID(int=i)) for i in range(21, 27)]
HASH = "a" * 64


@pytest.fixture
def enrolled(journal):
    _, base, provisioning = journal
    sessions = base._sessions
    for metadata in (CredentialBase.metadata, EnrollmentBase.metadata):
        metadata.create_all(sessions.kw["bind"])
    pairs = [
        (
            ReadDraftCommand(kind="read_draft", operation_id=str(UUID(int=31)), app_id=APP),
            ReadDraftReceipt(kind="read_draft", workspace_id=WS, app_id=APP, draft_id=DRAFT, draft_hash=HASH),
        ),
        (
            PrepareCredentialCommand(
                kind="prepare_credential",
                operation_id=str(UUID(int=32)),
                app_id=APP,
                config_ref="config",
                config_revision=1,
            ),
            PrepareCredentialReceipt(
                kind="prepare_credential",
                app_id=APP,
                credential_id=CREDENTIAL,
                plugin_unique_identifier="enterprise/plugin:1@digest",
            ),
        ),
        (
            BindCredentialCommand(
                kind="bind_credential",
                operation_id=str(UUID(int=33)),
                app_id=APP,
                draft_id=DRAFT,
                credential_id=CREDENTIAL,
                expected_draft_hash=HASH,
            ),
            BindCredentialReceipt(
                kind="bind_credential",
                app_id=APP,
                draft_id=DRAFT,
                credential_id=CREDENTIAL,
                accepted_draft_hash=HASH,
                draft_hash=HASH,
            ),
        ),
        (
            PublishCommand(kind="publish", operation_id=str(UUID(int=34)), app_id=APP, expected_draft_hash=HASH),
            PublishReceipt(kind="publish", app_id=APP, workflow_id=WORKFLOW, accepted_draft_hash=HASH),
        ),
    ]
    for command, receipt in pairs:
        provisioning = claim_provisioning(
            provisioning,
            command=command,
            nonce=NONCE,
            expected_revision=provisioning.view.revision,
            actor_id="actor",
            now=datetime.now(UTC),
        )
        provisioning = finish_provisioning(
            provisioning,
            outcome=PhaseOutcome(state="succeeded", receipt=receipt),
            nonce=NONCE,
            expected_revision=provisioning.view.revision,
            actor_id="actor",
            now=datetime.now(UTC),
        )
    with sessions.begin() as session:
        session.add(provisioning_row(provisioning))
    key = CredentialKey(key_id="ci", key=SecretStr(base64.urlsafe_b64encode(b"a" * 32).decode()))
    cipher = AesGcmCredentialCipher(CredentialKeyring(active_key_id="ci", keys=(key,)))
    repo = SqlAlchemyWorkflowEnrollmentRepository(sessions, cipher)
    value = initialize_enrollment(provisioning.view, enrollment_id=ENROLLMENT, secret_ref=SECRET, now=datetime.now(UTC))
    return repo, base, value, SqlAlchemyWorkflowCredentialVault(sessions, cipher)


def token_claim(repo, value):
    repo.create(value, actor_id="actor")
    repo.claim(WS, ENROLLMENT, phase="verify_publication", nonce=NONCE, expected_revision=1, actor_id="actor")
    metadata = NativePublicationMetadata(
        workspace_id=WS,
        app_id=APP,
        workflow_id=WORKFLOW,
        graph_hash=HASH,
        credential_id=CREDENTIAL,
        provider_id="enterprise/enterprise_device_assessment/enterprise_device",
        tool_name="evaluate_device",
        node_id="assessment",
    )
    repo.finish_verification(WS, ENROLLMENT, publication=metadata, nonce=NONCE, expected_revision=2, actor_id="actor")
    repo.claim(WS, ENROLLMENT, phase="issue_token", nonce=NONCE, expected_revision=3, actor_id="actor")


def issued():
    return NativeWorkflowTokenOutcome(
        state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN, token=SecretStr("app-" + "a" * 24)
    )


def store(repo):
    return repo.store_issued_token(WS, ENROLLMENT, issued=issued(), nonce=NONCE, expected_revision=4, actor_id="actor")


def test_concurrent_create_and_claim_have_one_owner(enrolled):
    repo, base, value, _ = enrolled
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(lambda _: repo.create(value, actor_id="actor"), range(2)))
    assert created == [value, value]

    def claim(_):
        try:
            return repo.claim(
                WS, ENROLLMENT, phase="verify_publication", nonce=NONCE, expected_revision=1, actor_id="actor"
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(result is not None for result in pool.map(claim, range(2))) == 1
    with base._sessions() as session:
        assert len(session.scalars(select(WorkflowEnrollmentRow)).all()) == 1


def test_token_race_commits_one_vault_entry_and_no_plaintext(enrolled):
    repo, base, value, vault = enrolled
    token_claim(repo, value)

    def finish(_):
        try:
            return store(repo)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(result is not None for result in pool.map(finish, range(2))) == 1
    assert vault.resolve(WS, APP, SECRET) == issued().token
    with base._sessions() as session:
        rows = session.scalars(select(WorkflowCredentialRow)).all()
        events = session.scalars(select(AuditEventRow)).all()
        assert len(rows) == 1
        assert sum(event.event_type == "workflow_credential_registered" for event in events) == 1
        assert issued().token.get_secret_value() not in str([row.__dict__ for row in rows + events])


@pytest.mark.parametrize("fail_at", [1, 2])
def test_audit_failure_rolls_back_token_and_enrollment(enrolled, monkeypatch, fail_at):
    import enterprise_platform.persistence.workflow_enrollment_repository as module

    repo, base, value, _ = enrolled
    token_claim(repo, value)
    original = module.audit
    count = 0

    def fail(*args, **kwargs):
        nonlocal count
        count += 1
        if count == fail_at:
            raise PersistenceError("injected_audit_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        store(repo)
    assert repo.get(WS, ENROLLMENT).view.state == "token_claimed"
    with base._sessions() as session:
        assert session.scalar(select(WorkflowCredentialRow)) is None


def test_owned_token_finish_survives_device_deletion(enrolled):
    repo, base, value, vault = enrolled
    token_claim(repo, value)
    base.delete_device(WS, value.view.provisioning.device_id, expected_revision=1, actor_id="actor")
    assert store(repo).view.state == "token_stored"
    assert vault.resolve(WS, APP, SECRET) == issued().token
    with pytest.raises(Conflict):
        store(repo)


def test_explicit_ci_0006_public_schema():
    from sqlalchemy import create_engine

    from enterprise_platform.persistence.migrate import validate_target

    if os.environ.get("ENTERPRISE_CI_ENROLLMENT_MIGRATED_PUBLIC") != "1":
        pytest.skip("Requires explicit CI 0006 migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated database not configured")
    engine = create_engine(validate_target(url, "enterprise_test"), hide_parameters=True)
    try:
        reflected = inspect(engine)
        assert len(reflected.get_table_names(schema="public")) == 10
        assert {c["name"] for c in reflected.get_columns("enterprise_workflow_enrollments", schema="public")} == set(
            WorkflowEnrollmentRow.__table__.c.keys()
        )
    finally:
        engine.dispose()
