from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from test_workflow_enrollment_contracts import (
    APP,
    NOW,
    TOKEN,
    claimed_token,
    claimed_verify,
    credential,
    enrollment,
    transition_args,
    verified,
)

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workflow_enrollment_contracts import (
    finish_enrollment_token,
    finish_enrollment_verification,
)
from enterprise_platform.persistence.workflow_enrollment_mapping import as_enrollment, enrollment_row
from enterprise_platform.persistence.workflow_enrollment_models import EnrollmentBase, WorkflowEnrollmentRow


@pytest.mark.parametrize("factory", [enrollment, claimed_verify, verified, claimed_token])
def test_roundtrip(factory):
    value = factory()
    row = enrollment_row(value)
    assert as_enrollment(row) == value
    assert '"claim_nonce"' not in row.public_json
    assert '"secret_ref"' not in row.public_json


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", APP),
        ("app_id", APP[::-1]),
        ("provisioning_id", APP),
        ("enrollment_id", APP),
        ("actor_id", "other"),
        ("state", "verified"),
        ("revision", 9),
        ("secret_ref", "invalid"),
        ("claim_nonce", APP),
        ("public_json", "{}"),
        ("created_at", NOW - timedelta(days=1)),
        ("updated_at", NOW + timedelta(days=1)),
    ],
)
def test_corrupt_storage_rejected(field, value):
    row = enrollment_row(enrollment())
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        as_enrollment(row)


def test_isolated_metadata_and_unique_enrollment():
    assert set(EnrollmentBase.metadata.tables) == {"enterprise_workflow_enrollments"}
    ddl = str(CreateTable(WorkflowEnrollmentRow.__table__).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (workspace_id, provisioning_id)" in ddl
    assert "UNIQUE (workspace_id, app_id, secret_ref)" in ddl
    assert "ck_workflow_enrollment_nonce" in ddl
    assert "ck_workflow_enrollment_state" in ddl
    assert "token TEXT" not in ddl


@pytest.mark.parametrize("state", ["token_stored", "rejected", "uncertain"])
def test_terminal_token_roundtrip(state):
    stored = claimed_token()
    result = finish_enrollment_token(
        stored,
        state=state,
        native_token_id=TOKEN if state == "token_stored" else None,
        credential=credential() if state == "token_stored" else None,
        reason_code=None if state == "token_stored" else "native_token_unconfirmed",
        **transition_args(stored),
    )
    assert as_enrollment(enrollment_row(result)) == result
    if state == "token_stored":
        row = enrollment_row(result)
        row.secret_ref = APP
        with pytest.raises(PersistenceError):
            as_enrollment(row)


def test_rejected_verification_roundtrip():
    stored = claimed_verify()
    result = finish_enrollment_verification(stored, reason_code="read_rejected", **transition_args(stored))
    assert as_enrollment(enrollment_row(result)) == result


def test_naive_database_timestamps_normalize_to_utc():
    row = enrollment_row(enrollment())
    row.created_at = row.created_at.replace(tzinfo=None)
    row.updated_at = row.updated_at.replace(tzinfo=None)
    assert as_enrollment(row) == enrollment()
