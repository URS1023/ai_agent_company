import base64
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from test_source_contracts import db_draft

from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher, SourceKeyring
from enterprise_platform.application.errors import DependencyUnavailable, PersistenceError
from enterprise_platform.application.input_capture import RegisteredRead
from enterprise_platform.application.source_contracts import DbSourceView, HttpSourceView, SourceDraft, SourceView
from enterprise_platform.domain.data_sources import DatabaseSourceConfig, HttpRead, HttpSourceConfig, SourceRef, SqlRead


def keyring():
    return SourceKeyring.model_validate(
        {
            "active_key_id": "key-1",
            "keys": [
                {"key_id": "key-1", "key": base64.urlsafe_b64encode(b"a" * 32).decode()},
                {"key_id": "key-2", "key": base64.urlsafe_b64encode(b"b" * 32).decode()},
            ],
        }
    )


def test_fingerprint_is_keyed_and_original_key_survives_active_rotation() -> None:
    draft = SourceDraft.model_validate(db_draft())
    first = AesGcmSourceCipher(keyring())
    second = AesGcmSourceCipher(keyring().model_copy(update={"active_key_id": "key-2"}))
    old_id, old_hash = first.fingerprint(draft)
    assert second.fingerprint(draft, key_id=old_id) == (old_id, old_hash)
    assert second.fingerprint(draft)[1] != old_hash
    changed = db_draft()
    changed["name"] = "other"
    assert first.fingerprint(SourceDraft.model_validate(changed))[1] != old_hash
    assert "fixture-password" not in old_hash
    with pytest.raises(DependencyUnavailable):
        second.fingerprint(draft, key_id="removed-key")


@pytest.mark.parametrize("key", ["", "not-base64", base64.urlsafe_b64encode(b"x" * 31).decode()])
def test_keyring_rejects_invalid_key_material_without_echoing_it(key: str) -> None:
    with pytest.raises(ValueError):
        SourceKeyring.model_validate({"active_key_id": "k", "keys": [{"key_id": "k", "key": key}]})


def test_keyring_rejects_duplicate_keys_and_unknown_active_key() -> None:
    data = keyring().model_dump()
    data["keys"] += (data["keys"][0],)
    with pytest.raises(ValueError):
        SourceKeyring.model_validate(data)
    with pytest.raises(ValueError):
        SourceKeyring.model_validate({**keyring().model_dump(), "active_key_id": "missing"})


def registration_pair(http: bool = False):
    draft = SourceDraft.model_validate(db_draft())
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRef(workspace_id="workspace-1", source_id="source-1", revision="source-v1")
    if http:
        config = HttpSourceConfig(
            source=source,
            url="https://collector.test/rows",
            allowed_hosts=frozenset({"collector.test"}),
            headers=(("Authorization", SecretStr("fixture-token")),),
        )
        read = HttpRead(
            source=source,
            read_id="read-1",
            revision="read-v1",
            method="GET",
            read_only=True,
            parameter_names=frozenset({"device"}),
        )
        public = HttpSourceView(url=config.url, header_names=("Authorization",), credentials_configured=True)
    else:
        config = DatabaseSourceConfig(
            source=source,
            dialect="postgresql",
            connection_url=SecretStr("postgresql+psycopg://reader:fixture-password@collector.test:5432/measurements"),
            allowed_tables=frozenset({"measurements"}),
            read_only_role=True,
        )
        read = SqlRead(source=source, read_id="read-1", revision="read-v1", sql=draft.connection.sql)
        public = DbSourceView.model_validate(
            {**draft.connection.model_dump(exclude={"username", "password"}), "credentials_configured": True}
        )
    view = SourceView.model_validate(
        {
            **draft.model_dump(exclude={"connection", "read_only_confirmed"}),
            "workspace_id": source.workspace_id,
            "source_id": source.source_id,
            "source_revision": source.revision,
            "read_id": read.read_id,
            "read_revision": read.revision,
            "revision": 1,
            "connection": public,
            "created_at": now,
            "updated_at": now,
        }
    )
    entry = RegisteredRead(
        connection=config,
        read=read,
        device_ids=frozenset(draft.device_ids),
        device_parameter=draft.device_parameter,
        device_column=draft.device_column,
    )
    return view, entry


@pytest.mark.parametrize("http", [False, True])
def test_sealed_roundtrip_preserves_actual_secrets_and_randomizes_ciphertext(http: bool) -> None:
    view, entry = registration_pair(http)
    cipher = AesGcmSourceCipher(keyring())
    sealed = cipher.seal(view, entry)
    assert cipher.open(view, sealed) == entry
    again = cipher.seal(view, entry)
    assert again.nonce != sealed.nonce and again.ciphertext != sealed.ciphertext
    assert "fixture-password" not in repr(sealed) and "fixture-token" not in repr(sealed)
    with pytest.raises(PersistenceError):
        cipher.open(view.model_copy(update={"name": "tampered"}), sealed)
    with pytest.raises(PersistenceError):
        cipher.open(view.model_copy(update={"workspace_id": "other"}), sealed)
    with pytest.raises(PersistenceError):
        cipher.open(view, replace(sealed, ciphertext="broken"))
    with pytest.raises(PersistenceError):
        cipher.open(view, replace(sealed, key_id="key-2"))
    with pytest.raises(DependencyUnavailable):
        cipher.open(view, replace(sealed, key_id="retired"))


def test_cannot_seal_a_registration_for_another_scope() -> None:
    view, entry = registration_pair()
    with pytest.raises(PersistenceError):
        AesGcmSourceCipher(keyring()).seal(view.model_copy(update={"read_revision": "other"}), entry)
