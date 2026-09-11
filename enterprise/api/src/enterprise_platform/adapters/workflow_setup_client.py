"""Native, workspace-guarded default draft imports with no POST retries.

Only bundled DSL is sent. Native login, billing and import permissions remain with
Dify. A capability preflight requires the additive native workspace guard; the
import request then asserts the exact principal workspace on the same Account
used for native creation. A response acknowledgement verifies the returned scope.
Once POST starts, a transport/protocol failure is an uncertain side effect, never
permission to create another app. The caller persists its claim before invoking us.
"""

import asyncio
import math
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from enterprise_platform.application.contracts import Identifier, Principal, Scenario
from enterprise_platform.application.workflow_setup_execution import (
    NativeImportOutcome,
    NativeSetupSession,
    SetupImportRejected,
)

from .dify_identity import _forwarded_headers


class _Capabilities(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    enabled: bool
    workspace_id: Identifier


class _ImportResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str
    app_id: str | None = None
    status: Literal["completed", "completed-with-warnings", "pending", "failed"]

    @field_validator("id", "app_id")
    @classmethod
    def canonical_native_id(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = UUID(value)
            if parsed.int == 0 or str(parsed) != value:
                raise ValueError("Invalid native import identity")
        return value


class DifyWorkflowSetupClient:
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
            not math.isfinite(timeout_seconds)
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
                raise ValueError("native_setup_response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _outcome(status_code: int, content: bytes) -> NativeImportOutcome:
        try:
            result = _ImportResult.model_validate_json(content)
            if result.status in {"completed", "completed-with-warnings"}:
                if status_code != 200 or result.app_id is None:
                    raise ValueError()
                return NativeImportOutcome(
                    "draft_ready",
                    result.app_id,
                    result.id,
                    "native_import_warnings" if result.status == "completed-with-warnings" else None,
                )
            if result.status == "pending":
                if status_code != 202:
                    raise ValueError()
                return NativeImportOutcome(
                    "confirmation_required", result.app_id, result.id, "native_import_confirmation_required"
                )
            if status_code != 400:
                raise ValueError()
            return NativeImportOutcome("failed", result.app_id, result.id, "native_import_failed")
        except (ValidationError, ValueError):
            return NativeImportOutcome("uncertain", reason_code="native_import_protocol_mismatch")

    async def import_default(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        setup_id: str,
        scenario: Scenario,
        name: str,
    ) -> NativeImportOutcome:
        headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
        if scenario not in {"alert", "quality"} or not 1 <= len(name.strip()) <= 200 or not setup_id:
            raise SetupImportRejected()
        try:
            template = (
                files("enterprise_platform")
                .joinpath(f"workflow_templates/default-{scenario}.yml")
                .read_text(encoding="utf-8")
            )
        except OSError:
            raise SetupImportRejected() from None
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
                            raise SetupImportRejected()
                        capabilities = _Capabilities.model_validate_json(await self._body(response))
                        if not capabilities.enabled or capabilities.workspace_id != principal.workspace_id:
                            raise SetupImportRejected()
                    post_started = True
                    async with client.stream(
                        "POST",
                        "apps/imports",
                        headers={"X-Enterprise-Expected-Workspace": principal.workspace_id},
                        json={
                            "mode": "yaml-content",
                            "yaml_content": template,
                            "name": name.strip(),
                            "description": f"enterprise-setup:{setup_id}",
                        },
                    ) as response:
                        if response.headers.get("X-Enterprise-Workspace") != principal.workspace_id:
                            return NativeImportOutcome("uncertain", reason_code="native_import_scope_unconfirmed")
                        return self._outcome(response.status_code, await self._body(response))
        except SetupImportRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            if post_started:
                return NativeImportOutcome("uncertain", reason_code="native_import_transport_uncertain")
            raise SetupImportRejected() from None
