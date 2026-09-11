"""Bounded exact-publication metadata read; no latest fallback or snapshot exposure."""

import asyncio
import math
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.workflow_draft_read import canonical_native_uuid
from enterprise_platform.application.workflow_publication_read import (
    NativePublicationMetadata,
    PublicationReadRejected,
)
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers


class _Command(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    app_id: str
    workflow_id: str
    expected_graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_id: str

    @field_validator("workspace_id", "app_id", "workflow_id", "credential_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return canonical_native_uuid(value)


class _Capabilities(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    enabled: bool
    publication_read_enabled: bool
    workspace_id: str


class _Metadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    app_id: str
    workflow_id: str
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_id: Literal["enterprise/enterprise_device_assessment/enterprise_device"]
    tool_name: Literal["evaluate_device"]
    credential_id: str
    node_id: Literal["assessment"]


class DifyWorkflowPublicationReadClient:
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
                raise ValueError("native_publication_read_response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    async def read(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        workflow_id: str,
        expected_graph_hash: str,
        credential_id: str,
    ) -> NativePublicationMetadata:
        try:
            command = _Command(
                workspace_id=principal.workspace_id,
                app_id=app_id,
                workflow_id=workflow_id,
                expected_graph_hash=expected_graph_hash,
                credential_id=credential_id,
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
                            raise PublicationReadRejected()
                        capabilities = _Capabilities.model_validate_json(await self._body(response))
                        if (
                            not capabilities.enabled
                            or not capabilities.publication_read_enabled
                            or capabilities.workspace_id != command.workspace_id
                        ):
                            raise PublicationReadRejected()
                    async with client.stream(
                        "GET",
                        f"apps/{command.app_id}/workflows/publish",
                        headers={
                            "X-Enterprise-Expected-Workspace": command.workspace_id,
                            "X-Enterprise-Expected-Workflow": command.workflow_id,
                            "X-Enterprise-Expected-Graph-Hash": command.expected_graph_hash,
                            "X-Enterprise-Publication-Operation": "read-assessment-publication",
                        },
                    ) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("X-Enterprise-Workspace") != command.workspace_id
                        ):
                            raise PublicationReadRejected()
                        result = _Metadata.model_validate_json(await self._body(response))
                        if (
                            result.app_id != command.app_id
                            or result.workflow_id != command.workflow_id
                            or result.hash != command.expected_graph_hash
                            or result.credential_id != command.credential_id
                        ):
                            raise PublicationReadRejected()
                        return NativePublicationMetadata(
                            workspace_id=command.workspace_id,
                            app_id=result.app_id,
                            workflow_id=result.workflow_id,
                            graph_hash=result.hash,
                            provider_id=result.provider_id,
                            tool_name=result.tool_name,
                            credential_id=result.credential_id,
                            node_id=result.node_id,
                        )
        except PublicationReadRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError):
            raise PublicationReadRejected() from None
