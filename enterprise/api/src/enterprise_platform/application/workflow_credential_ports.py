"""Injected cryptography and resolver boundaries; no credential transport."""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import SecretStr

from .workflow_credential_contracts import CredentialView


@dataclass(frozen=True)
class SealedCredential:
    key_id: str
    nonce: str = field(repr=False)
    ciphertext: str = field(repr=False)


class CredentialCipher(Protocol):
    def seal(self, view: CredentialView, token: SecretStr) -> SealedCredential: ...
    def open(self, view: CredentialView, sealed: SealedCredential) -> SecretStr: ...


class WorkflowCredentialResolver(Protocol):
    def resolve(self, workspace_id: str, app_id: str, secret_ref: str) -> SecretStr: ...
