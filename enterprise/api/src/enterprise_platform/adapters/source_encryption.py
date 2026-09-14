"""Versioned AES-256-GCM registrations, bound to the complete public view.

Only these explicit serialization boundaries unwrap SecretStr. HMAC fingerprints
use a purpose-derived key and retain its key ID for idempotency across rotation.
Removing an old key intentionally makes its uncollected registrations unavailable.
"""

import base64
import hashlib
import hmac
import secrets
from typing import Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, SecretStr, model_validator

from enterprise_platform.application.contracts import JsonObject, canonical_json
from enterprise_platform.application.errors import DependencyUnavailable, PersistenceError
from enterprise_platform.application.input_capture import RegisteredRead
from enterprise_platform.application.source_contracts import DbSourceDraft, SourceContract, SourceDraft, SourceView
from enterprise_platform.application.source_ports import SealedSource
from enterprise_platform.domain.data_sources import DatabaseSourceConfig


def _decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


class SourceKey(SourceContract):
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    key: SecretStr = Field(repr=False)

    @model_validator(mode="after")
    def valid_material(self) -> Self:
        try:
            value = self.key.get_secret_value()
            if len(value) != 44 or len(_decode(value)) != 32:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid source encryption key") from None
        return self


class SourceKeyring(SourceContract):
    active_key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    keys: tuple[SourceKey, ...] = Field(min_length=1, max_length=16, repr=False)

    @model_validator(mode="after")
    def valid_keys(self) -> Self:
        ids = [item.key_id for item in self.keys]
        if len(ids) != len(set(ids)) or self.active_key_id not in ids:
            raise ValueError("Invalid source encryption keyring")
        return self


def _registration_json(entry: RegisteredRead) -> str:
    document: JsonObject = entry.model_dump(mode="json")
    connection: JsonObject = entry.connection.model_dump(mode="json")
    if isinstance(entry.connection, DatabaseSourceConfig):
        connection["connection_url"] = entry.connection.connection_url.get_secret_value()
    else:
        connection["headers"] = [[name, secret.get_secret_value()] for name, secret in entry.connection.headers]
    document["connection"] = connection
    return canonical_json(document)


def _draft_json(draft: SourceDraft) -> str:
    document: JsonObject = draft.model_dump(mode="json")
    if draft.enabled:
        # Missing and true both mean enabled; retain existing idempotency fingerprints.
        document.pop("enabled", None)
    connection: JsonObject = draft.connection.model_dump(mode="json")
    if isinstance(draft.connection, DbSourceDraft):
        connection["username"] = draft.connection.username.get_secret_value() if draft.connection.username else None
        connection["password"] = draft.connection.password.get_secret_value() if draft.connection.password else None
    else:
        connection["headers"] = (
            [{"name": item.name, "value": item.value.get_secret_value()} for item in draft.connection.headers]
            if draft.connection.headers is not None
            else None
        )
    document["connection"] = connection
    return canonical_json(document)


def _scope(view: SourceView, entry: RegisteredRead) -> None:
    source = entry.read.source
    if (
        (view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision)
        != (source.workspace_id, source.source_id, source.revision, entry.read.read_id, entry.read.revision)
        or frozenset(view.device_ids) != entry.device_ids
        or view.device_parameter != entry.device_parameter
        or view.device_column != entry.device_column
        or view.scope_attribute != entry.scope_attribute
        or view.department_parameter != entry.department_parameter
        or view.parameters != entry.parameters
        or view.limits != entry.connection.limits
    ):
        raise PersistenceError("source_registration_scope_mismatch")


class AesGcmSourceCipher:
    def __init__(self, keyring: SourceKeyring) -> None:
        self._keyring = SourceKeyring.model_validate(keyring.model_dump())

    def _key(self, key_id: str) -> bytes:
        for item in self._keyring.keys:
            if item.key_id == key_id:
                return _decode(item.key.get_secret_value())
        raise DependencyUnavailable("source_encryption_key_unavailable")

    @staticmethod
    def _aad(view: SourceView, key_id: str) -> bytes:
        document: JsonObject = view.model_dump(mode="json")
        if view.enabled:
            # Preserve pre-lifecycle ciphertext; false remains authenticated, not omitted.
            document.pop("enabled", None)
        return ("enterprise-source/aesgcm/v1/" + key_id + "\n" + canonical_json(document)).encode()

    def seal(self, view: SourceView, registration: RegisteredRead) -> SealedSource:
        _scope(view, registration)
        key_id = self._keyring.active_key_id
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key(key_id)).encrypt(
            nonce, _registration_json(registration).encode(), self._aad(view, key_id)
        )
        return SealedSource(
            key_id, base64.urlsafe_b64encode(nonce).decode(), base64.urlsafe_b64encode(ciphertext).decode()
        )

    def open(self, view: SourceView, sealed: SealedSource) -> RegisteredRead:
        key = self._key(sealed.key_id)
        try:
            if len(sealed.ciphertext) > 2 * 1024 * 1024 or len(sealed.nonce) != 16:
                raise ValueError()
            data = AESGCM(key).decrypt(
                _decode(sealed.nonce), _decode(sealed.ciphertext), self._aad(view, sealed.key_id)
            )
            entry = RegisteredRead.model_validate_json(data)
            _scope(view, entry)
            return entry
        except (ValueError, InvalidTag):
            raise PersistenceError("source_decryption_failed") from None

    def fingerprint(self, draft: SourceDraft, *, key_id: str | None = None) -> tuple[str, str]:
        selected = key_id or self._keyring.active_key_id
        key = hmac.digest(self._key(selected), b"enterprise-source/idempotency/v1", "sha256")
        return selected, hmac.new(key, _draft_json(draft).encode(), hashlib.sha256).hexdigest()
