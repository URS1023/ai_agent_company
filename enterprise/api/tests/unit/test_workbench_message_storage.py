from hashlib import sha256

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from test_workbench_messages import intent, receipt

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workbench_messages import acknowledge_message, claim_message
from enterprise_platform.persistence.workbench_messages import MessageIntentRow, as_message_intent, message_intent_row


def test_primary_key_scopes_client_id_to_user_app_and_branch() -> None:
    table = MessageIntentRow.__table__
    assert list(table.primary_key.columns.keys()) == [
        "workspace_id",
        "actor_id",
        "installed_app_id",
        "branch_id",
        "client_message_id",
    ]
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "CHECK (revision > 0)" in sql
    assert "'queued', 'dispatched', 'uncertain', 'accepted'" in sql


def test_storage_roundtrip_is_detached_and_preserves_native_receipt() -> None:
    saved = acknowledge_message(claim_message(intent()), receipt())
    row = message_intent_row(saved)
    decoded = as_message_intent(row, saved.scope, saved.client_message_id)
    assert decoded == saved
    assert decoded is not saved
    assert row.document_hash == sha256(row.document_json.encode("utf-8")).hexdigest()
    assert row.payload_hash == saved.payload_hash


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("actor_id", "other"),
        ("installed_app_id", "other"),
        ("branch_id", "other"),
        ("client_message_id", "other"),
        ("revision", 99),
        ("status", "accepted"),
        ("payload_hash", "0" * 64),
        ("document_hash", "0" * 64),
    ],
)
def test_index_document_disagreement_is_rejected(field: str, value: str | int) -> None:
    saved = intent()
    row = message_intent_row(saved)
    setattr(row, field, value)
    with pytest.raises(PersistenceError, match="message_intent_storage_invalid"):
        as_message_intent(row, saved.scope, saved.client_message_id)


def test_matching_hash_does_not_allow_an_invalid_persisted_state() -> None:
    saved = intent()
    row = message_intent_row(saved)
    row.document_json = row.document_json.replace('"status":"queued"', '"status":"accepted"')
    row.document_hash = sha256(row.document_json.encode("utf-8")).hexdigest()
    with pytest.raises(PersistenceError):
        as_message_intent(row, saved.scope, saved.client_message_id)


def test_requested_scope_is_checked_separately_from_stored_scope() -> None:
    saved = intent()
    scope = saved.scope.model_copy(update={"actor_id": "different-user"})
    with pytest.raises(PersistenceError):
        as_message_intent(message_intent_row(saved), scope, saved.client_message_id)


def test_oversized_document_is_rejected_without_echoing_contents() -> None:
    saved = intent()
    row = message_intent_row(saved)
    row.document_json = "private-content" * 100000
    row.document_hash = sha256(row.document_json.encode("utf-8")).hexdigest()
    with pytest.raises(PersistenceError, match="^message_intent_storage_invalid$"):
        as_message_intent(row, saved.scope, saved.client_message_id)
