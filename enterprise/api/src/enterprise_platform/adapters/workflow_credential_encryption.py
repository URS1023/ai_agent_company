"""AES-256-GCM credentials authenticated against their entire public reference."""

import base64
import secrets
from typing import Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ConfigDict, Field, SecretStr, model_validator

from enterprise_platform.application.contracts import Contract, canonical_json
from enterprise_platform.application.errors import DependencyUnavailable, PersistenceError
from enterprise_platform.application.workflow_credential_contracts import CredentialView, validate_token
from enterprise_platform.application.workflow_credential_ports import SealedCredential


def _decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


class CredentialKey(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    key: SecretStr = Field(repr=False)

    @model_validator(mode="after")
    def valid_material(self) -> Self:
        try:
            if len(self.key.get_secret_value()) != 44 or len(_decode(self.key.get_secret_value())) != 32:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid workflow encryption key") from None
        return self


class CredentialKeyring(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    active_key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    keys: tuple[CredentialKey, ...] = Field(min_length=1, max_length=16, repr=False)

    @model_validator(mode="after")
    def valid_keys(self) -> Self:
        ids = [item.key_id for item in self.keys]
        if len(ids) != len(set(ids)) or self.active_key_id not in ids:
            raise ValueError("Invalid workflow encryption keyring")
        return self


class AesGcmCredentialCipher:
    _keyring: CredentialKeyring

    def __init__(self, keyring: CredentialKeyring) -> None:
        self._keyring = CredentialKeyring.model_validate(keyring.model_dump())

    def _key(self, key_id: str) -> bytes:
        for item in self._keyring.keys:
            if item.key_id == key_id:
                return _decode(item.key.get_secret_value())
        raise DependencyUnavailable("workflow_credential_key_unavailable")

    @staticmethod
    def _aad(view: CredentialView, key_id: str) -> bytes:
        return (
            "enterprise-workflow-credential/aesgcm/v1/" + key_id + "\n" + canonical_json(view.model_dump(mode="json"))
        ).encode()

    def seal(self, view: CredentialView, token: SecretStr) -> SealedCredential:
        validate_token(token)
        key_id = self._keyring.active_key_id
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self._key(key_id)).encrypt(
            nonce, token.get_secret_value().encode("ascii"), self._aad(view, key_id)
        )
        return SealedCredential(
            key_id, base64.urlsafe_b64encode(nonce).decode(), base64.urlsafe_b64encode(encrypted).decode()
        )

    def open(self, view: CredentialView, sealed: SealedCredential) -> SecretStr:
        key = self._key(sealed.key_id)
        try:
            if len(sealed.nonce) != 16 or not 24 <= len(sealed.ciphertext) <= 5484:
                raise ValueError()
            raw = AESGCM(key).decrypt(_decode(sealed.nonce), _decode(sealed.ciphertext), self._aad(view, sealed.key_id))
            return validate_token(SecretStr(raw.decode("ascii")))
        except (ValueError, InvalidTag):
            raise PersistenceError("workflow_credential_corrupt") from None
