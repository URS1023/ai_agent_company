"""Bounded authenticated read of native scheduling ownership, never a lease.

Only a positive, exact scoped receipt may return False. Unknown/error responses
raise; redirects, ambient proxy settings and cached ownership are not used.
"""

import asyncio
import math
from urllib.parse import urlsplit

import httpx
from pydantic import Field, StrictBool, field_validator

from enterprise_platform.application.contracts import Contract, Principal
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError
from enterprise_platform.application.workflow_draft_read import canonical_native_uuid
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers


class _Identity(Contract):
    workspace_id: str
    app_id: str
    workflow_id: str
    graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("workspace_id", "app_id", "workflow_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        return canonical_native_uuid(value)


class _Receipt(_Identity):
    native_owner_present: StrictBool


class DifyScheduleOwnerClient:
    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15,
        max_response_bytes: int = 16384,
    ) -> None:
        try:
            parsed, normalized = urlsplit(base_url), httpx.URL(base_url)
            valid = (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and parsed.port != 0
                and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
                and parsed.path.rstrip("/").endswith("/console/api")
                and not any(part in {".", ".."} for part in parsed.path.split("/"))
                and not any(character.isspace() for character in base_url)
                and "\\" not in base_url
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
            or not 1 <= max_response_bytes <= 65536
        ):
            raise ValueError("Positive finite deadline and bounded response size required")
        self._base_url = str(normalized).rstrip("/") + "/"
        self._transport, self._timeout_seconds, self._max_response_bytes = (
            transport,
            timeout_seconds,
            max_response_bytes,
        )

    async def read(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        workflow_id: str,
        expected_hash: str,
    ) -> bool:
        try:
            command = _Identity(
                workspace_id=principal.workspace_id, app_id=app_id, workflow_id=workflow_id, graph_hash=expected_hash
            )
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers.update(
                {
                    "X-Enterprise-Expected-Workspace": command.workspace_id,
                    "X-Enterprise-Expected-Workflow": command.workflow_id,
                    "X-Enterprise-Expected-Graph-Hash": command.graph_hash,
                    "X-Enterprise-Schedule-Operation": "inspect-native-owner",
                }
            )
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    async with client.stream("GET", f"apps/{command.app_id}/enterprise/schedule-owner") as response:
                        cache_policy = {
                            part.strip().lower() for part in response.headers.get("Cache-Control", "").split(",")
                        }
                        if (
                            response.status_code != 200
                            or response.headers.get("X-Enterprise-Workspace") != command.workspace_id
                            or not {"private", "no-store"}.issubset(cache_policy)
                        ):
                            raise ValueError("native_owner_response_invalid")
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self._max_response_bytes:
                                raise ValueError("native_owner_response_too_large")
                            chunks.append(chunk)
                        receipt = _Receipt.model_validate_json(b"".join(chunks))
                        if receipt.model_dump(exclude={"native_owner_present"}) != command.model_dump():
                            raise ValueError("native_owner_scope_mismatch")
                        return receipt.native_owner_present
        except (ValueError, EnterpriseError, httpx.HTTPError, TimeoutError):
            raise DependencyUnavailable("native_schedule_owner_unavailable") from None
