"""One native credential delta POST; no snapshot GET, graph upload, or retry."""

import asyncio
import math
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.workflow_draft_execution import (
    DraftCredentialRejected,
    NativeDraftCredentialOutcome,
)
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    app_id: str
    draft_id: str
    credential_id: str
    workspace_id: str
    expected_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("app_id", "draft_id", "credential_id", "workspace_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.int == 0 or str(parsed) != value:
            raise ValueError("Canonical native identity required")
        return value


class _Capabilities(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    enabled: bool
    draft_credential_bind_enabled: bool
    workspace_id: str


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    result: Literal["success"]
    app_id: str
    draft_id: str
    credential_id: str
    accepted_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DifyWorkflowDraftClient:
    _base_url: str
    _transport: httpx.AsyncBaseTransport | None
    _timeout_seconds: float
    _max_response_bytes: int

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        try:
            parsed = urlsplit(base_url)
            normalized = httpx.URL(base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port != 0
                and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
                and parsed.path.rstrip("/").endswith("/console/api")
                and not any(c.isspace() for c in base_url)
                and not any(segment in {".", ".."} for segment in parsed.path.split("/"))
            )
        except (ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ValueError("A fixed native console API endpoint is required")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 2 * 1024 * 1024
        ):
            raise ValueError("Bounded response size and positive deadline required")
        self._base_url = str(normalized).rstrip("/") + "/"
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def _body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ValueError("native_draft_response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    async def bind_credential(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        draft_id: str,
        expected_draft_hash: str,
        credential_id: str,
    ) -> NativeDraftCredentialOutcome:
        post_started = False
        try:
            command = _Command(
                app_id=app_id,
                draft_id=draft_id,
                credential_id=credential_id,
                workspace_id=principal.workspace_id,
                expected_draft_hash=expected_draft_hash,
            )
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=self._timeout_seconds,
                ) as client:
                    async with client.stream("GET", "enterprise/workflow-setup/capabilities") as response:
                        if response.status_code != 200:
                            raise DraftCredentialRejected()
                        capabilities = _Capabilities.model_validate_json(await self._body(response))
                        if (
                            not capabilities.enabled
                            or not capabilities.draft_credential_bind_enabled
                            or capabilities.workspace_id != command.workspace_id
                        ):
                            raise DraftCredentialRejected()
                    post_started = True
                    async with client.stream(
                        "POST",
                        f"apps/{command.app_id}/workflows/draft",
                        headers={
                            "X-Enterprise-Expected-Workspace": command.workspace_id,
                            "X-Enterprise-Draft-Operation": "bind-assessment-credential",
                        },
                        json={
                            "draft_id": command.draft_id,
                            "hash": command.expected_draft_hash,
                            "credential_id": command.credential_id,
                        },
                    ) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("X-Enterprise-Workspace") != command.workspace_id
                        ):
                            return NativeDraftCredentialOutcome(
                                "uncertain", reason_code="native_draft_credential_unconfirmed"
                            )
                        result = _Result.model_validate_json(await self._body(response))
                        if (result.app_id, result.draft_id, result.credential_id, result.accepted_draft_hash) != (
                            command.app_id,
                            command.draft_id,
                            command.credential_id,
                            command.expected_draft_hash,
                        ):
                            return NativeDraftCredentialOutcome(
                                "uncertain", reason_code="native_draft_credential_protocol_mismatch"
                            )
                        return NativeDraftCredentialOutcome(
                            "draft_bound",
                            result.app_id,
                            result.draft_id,
                            result.credential_id,
                            result.accepted_draft_hash,
                            result.hash,
                        )
        except DraftCredentialRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError):
            if post_started:
                return NativeDraftCredentialOutcome(
                    "uncertain", reason_code="native_draft_credential_transport_uncertain"
                )
            raise DraftCredentialRejected() from None
