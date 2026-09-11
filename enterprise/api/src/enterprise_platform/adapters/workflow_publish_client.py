"""One exact, scope-acknowledged native publish; never GET-latest or POST retry.

The normal native endpoint has neither a draft-hash guard nor a returned version
ID. Preflight therefore requires the managed publish capability explicitly. Once
POST starts, any unconfirmed response is uncertain and must be reconciled by the
durable caller. The server-created operation marker is diagnostic, not native
idempotency. A published graph is not proof of plugin readiness or enrollment.
"""

import asyncio
import math
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_platform.application.contracts import Identifier, Principal
from enterprise_platform.application.workflow_publish_execution import NativePublishOutcome, PublishRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers


class _PublishCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    app_id: str
    operation_id: str
    workspace_id: str
    expected_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("app_id", "operation_id", "workspace_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.int == 0 or str(parsed) != value:
            raise ValueError("A canonical native UUID is required")
        return value


class _Capabilities(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    enabled: bool
    publish_enabled: bool
    workspace_id: Identifier

    @field_validator("workspace_id")
    @classmethod
    def canonical_workspace(cls, value: str) -> str:
        return _PublishCommand.canonical_uuid(value)


class _PublishResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    result: Literal["success"]
    created_at: int = Field(ge=0)
    app_id: str
    workflow_id: str
    accepted_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("app_id", "workflow_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        return _PublishCommand.canonical_uuid(value)


class DifyWorkflowPublishClient:
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
            raise ValueError("Bounded response size and a positive deadline are required")
        self._base_url = f"{str(normalized).rstrip('/')}/"
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def _body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ValueError("native_publish_response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    async def publish(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        expected_draft_hash: str,
        operation_id: str,
    ) -> NativePublishOutcome:
        try:
            command = _PublishCommand(
                app_id=app_id,
                expected_draft_hash=expected_draft_hash,
                operation_id=operation_id,
                workspace_id=principal.workspace_id,
            )
        except ValueError:
            raise PublishRejected() from None
        headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
        post_started = False
        try:
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
                            raise PublishRejected()
                        capabilities = _Capabilities.model_validate_json(await self._body(response))
                        if (
                            not capabilities.enabled
                            or not capabilities.publish_enabled
                            or capabilities.workspace_id != principal.workspace_id
                        ):
                            raise PublishRejected()
                    post_started = True
                    async with client.stream(
                        "POST",
                        f"apps/{command.app_id}/workflows/publish",
                        headers={
                            "X-Enterprise-Expected-Workspace": principal.workspace_id,
                            "X-Enterprise-Expected-Draft-Hash": command.expected_draft_hash,
                        },
                        json={
                            "marked_name": f"managed-{UUID(command.operation_id).hex[:12]}",
                            "marked_comment": f"enterprise-provisioning:{command.operation_id}",
                        },
                    ) as response:
                        if response.headers.get("X-Enterprise-Workspace") != principal.workspace_id:
                            return NativePublishOutcome("uncertain", reason_code="native_publish_scope_unconfirmed")
                        if response.status_code != 200:
                            return NativePublishOutcome("uncertain", reason_code="native_publish_unconfirmed")
                        result = _PublishResult.model_validate_json(await self._body(response))
                        if result.app_id != command.app_id or result.accepted_draft_hash != command.expected_draft_hash:
                            return NativePublishOutcome("uncertain", reason_code="native_publish_protocol_mismatch")
                        return NativePublishOutcome(
                            "published", result.app_id, result.workflow_id, result.accepted_draft_hash
                        )
        except PublishRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            if post_started:
                return NativePublishOutcome("uncertain", reason_code="native_publish_transport_uncertain")
            raise PublishRejected() from None
