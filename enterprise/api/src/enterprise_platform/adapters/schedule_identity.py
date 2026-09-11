"""Fresh native scheduler identity from an explicitly supplied session source.

The optional file provider reads only its configured path, never discovers browser
cookies or creates credentials. Operators must restrict file access and rotate the
document atomically. There is no login, token refresh, workspace switch, or cache.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import SecretStr, TypeAdapter, ValidationError

from enterprise_platform.application.contracts import Contract, Identifier, Principal
from enterprise_platform.application.errors import AccessDenied, Unauthenticated
from enterprise_platform.application.service import BusinessService
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import DifyIdentityClient


class _SessionDocument(Contract):
    cookie_header: SecretStr | None
    authorization: SecretStr | None
    csrf_token: SecretStr | None


class FileScheduleSession:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> NativeSetupSession:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(32769)
            if len(content) > 32768:
                raise ValueError("session_document_too_large")
            document = _SessionDocument.model_validate_json(content)
        except (OSError, ValueError, ValidationError):
            raise Unauthenticated("scheduler_session_unavailable") from None
        return NativeSetupSession(
            document.cookie_header.get_secret_value() if document.cookie_header else None,
            document.authorization.get_secret_value() if document.authorization else None,
            document.csrf_token.get_secret_value() if document.csrf_token else None,
        )

    async def __call__(self) -> NativeSetupSession:
        return await asyncio.to_thread(self._read)


class DifyScheduleIdentity:
    def __init__(
        self,
        client: DifyIdentityClient,
        sessions: Callable[[], Awaitable[NativeSetupSession]],
        *,
        workspace_id: str,
        actor_id: str,
    ) -> None:
        self._client, self._sessions = client, sessions
        self._workspace_id = TypeAdapter(Identifier).validate_python(workspace_id)
        self._actor_id = TypeAdapter(Identifier).validate_python(actor_id)

    async def __call__(self) -> Principal:
        session = await self._sessions()
        principal = await self._client.resolve(
            cookie_header=session.cookie_header, authorization=session.authorization, csrf_token=session.csrf_token
        )
        principal = Principal.model_validate(principal.model_dump())
        if (principal.workspace_id, principal.actor_id) != (self._workspace_id, self._actor_id):
            raise AccessDenied("scheduler_identity_changed")
        BusinessService.require(principal, "run")
        return principal
