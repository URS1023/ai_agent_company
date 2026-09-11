"""Dedicated server credential authenticates exact, secret-free native registration reads.

The operator must mount a randomly generated credential shared only with the native
service and use a private authenticated transport. This credential is neither a
browser session nor a workflow execution signing key and grants no execution itself.
"""

import asyncio
import hmac
import re
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr, TypeAdapter, ValidationError

from .errors import InvalidInput, PersistenceError, Unauthenticated
from .workflow_provisioning_contracts import NativeUUID
from .workflow_publication_read import NativePublicationMetadata


class NativeRegistrationLookup(Protocol):
    def find_registration(
        self, workspace_id: str, app_id: str, *, workflow_id: UUID
    ) -> NativePublicationMetadata | None: ...


def validate_registration_token(token: SecretStr) -> SecretStr:
    value = token.get_secret_value()
    if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value) is None:
        raise ValueError("Invalid native registration service credential")
    return SecretStr(value)


class NativeRegistrationService:
    def __init__(self, lookup: NativeRegistrationLookup, token: SecretStr) -> None:
        self._lookup, self._token = lookup, validate_registration_token(token)

    def authenticate(self, token: str, *, browser_origin: bool) -> None:
        if browser_origin or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) is None:
            raise Unauthenticated()
        if not hmac.compare_digest(token, self._token.get_secret_value()):
            raise Unauthenticated()

    async def read(self, workspace_id: str, app_id: str, workflow_id: str) -> NativePublicationMetadata | None:
        """Invoke only after authenticate at the private transport boundary."""
        try:
            for identity in (workspace_id, app_id, workflow_id):
                TypeAdapter(NativeUUID).validate_python(identity)
        except ValidationError:
            raise InvalidInput("invalid_registration_scope") from None
        result = await asyncio.to_thread(
            self._lookup.find_registration, workspace_id, app_id, workflow_id=UUID(workflow_id)
        )
        if result is None:
            return None
        try:
            result = NativePublicationMetadata.model_validate(result.model_dump())
        except ValidationError:
            raise PersistenceError("invalid_registration_receipt") from None
        if (result.workspace_id, result.app_id, result.workflow_id) != (workspace_id, app_id, workflow_id):
            raise PersistenceError("registration_scope_mismatch")
        return result
