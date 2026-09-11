"""Resolve business identity through native Dify login, CSRF and license checks.

The console base URL is server configuration, never a request parameter. Only
access/CSRF cookies, Bearer Authorization and X-CSRF-Token cross this boundary;
refresh tokens, caller-supplied workspace headers and business bodies do not.

Native account/profile omits status, so the current account's member row supplies
the active-state check. Current workspace is checked again after member lookup
to detect a concurrent native workspace switch. This is not an atomic Dify
snapshot: application services must still scope every operation to Principal.
Each lookup has an isolated cookie jar and an overall deadline. No identity,
credentials or other members' data are cached or logged here.
"""

import asyncio
import hmac
import math
import re
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from enterprise_platform.application.contracts import Identifier, Principal, WorkspaceRole
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable, Unauthenticated

_ACCESS_COOKIES = frozenset({"access_token", "__Host-access_token"})
_CSRF_COOKIES = frozenset({"csrf_token", "__Host-csrf_token"})
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*\Z")
_ROLE_ADAPTER: TypeAdapter[WorkspaceRole] = TypeAdapter(WorkspaceRole)


class _ResponseProjection(BaseModel):
    # Upstream responses contain unrelated profile/member data; do not retain it.
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _Profile(_ResponseProjection):
    id: Identifier
    name: str


class _Workspace(_ResponseProjection):
    id: Identifier
    status: str
    role: str


class _Member(_ResponseProjection):
    id: Identifier
    role: str
    status: str


class _Members(_ResponseProjection):
    accounts: list[_Member]


def _forwarded_headers(cookie_header: str | None, authorization: str | None, csrf_token: str | None) -> dict[str, str]:
    """Keep the native cookie/Bearer precedence; Dify verifies the actual tokens."""
    for value in (cookie_header, authorization, csrf_token):
        if value is not None and (
            len(value) > 16384 or any(ord(character) < 32 or ord(character) > 126 for character in value)
        ):
            raise Unauthenticated("Invalid authentication headers.")
    cookies: dict[str, str] = {}
    for segment in (cookie_header or "").split(";"):
        name, separator, value = segment.strip().partition("=")
        if name not in _ACCESS_COOKIES | _CSRF_COOKIES:
            continue
        if not separator or name in cookies or not _TOKEN_PATTERN.fullmatch(value):
            raise Unauthenticated("Invalid authentication cookies.")
        cookies[name] = value

    bearer: str | None = None
    if authorization:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not _TOKEN_PATTERN.fullmatch(parts[1]):
            raise Unauthenticated("Invalid Authorization header.")
        bearer = f"Bearer {parts[1]}"
    if not bearer and not _ACCESS_COOKIES.intersection(cookies):
        raise Unauthenticated("A native Dify session is required.")
    if not csrf_token or not any(
        hmac.compare_digest(value, csrf_token) for name, value in cookies.items() if name in _CSRF_COOKIES
    ):
        raise Unauthenticated("Native CSRF credentials are required.")

    headers = {"Cookie": "; ".join(f"{name}={value}" for name, value in cookies.items()), "X-CSRF-Token": csrf_token}
    if bearer:
        headers["Authorization"] = bearer
    return headers


class DifyIdentityClient:
    """An async identity adapter; business read/manage/run/review policy lives elsewhere.

    The optional transport is an injection seam for isolated tests. A transport
    passed here is closed with each per-lookup client and must support that
    lifecycle (httpx.MockTransport does).
    """

    _base_url: str
    _transport: httpx.AsyncBaseTransport | None
    _timeout_seconds: float
    _max_response_bytes: int

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        try:
            url = urlsplit(base_url)
            normalized_url = httpx.URL(base_url)
        except (ValueError, httpx.InvalidURL):
            raise ValueError("base_url must be a valid server-configured console API endpoint.") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not url.path.rstrip("/").endswith("/console/api")
            or any(character.isspace() for character in base_url)
            or any(segment in {".", ".."} for segment in url.path.split("/"))
        ):
            raise ValueError("base_url must be a server-configured console API endpoint without credentials or query.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("Identity deadline and response-size limit must be positive and finite.")
        self._base_url = f"{str(normalized_url).rstrip('/')}/"
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def _read[T: BaseModel](
        self,
        client: httpx.AsyncClient,
        method: Literal["GET", "POST"],
        path: str,
        model: type[T],
    ) -> T:
        async with client.stream(method, path) as response:
            if response.status_code == 401:
                raise Unauthenticated("Dify rejected the session or CSRF credentials.")
            if response.status_code == 403:
                raise AccessDenied("Dify denied access.")
            if response.status_code != 200:
                raise DependencyUnavailable("Dify identity lookup is unavailable.")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise DependencyUnavailable("Dify identity response exceeded its size limit.")
                chunks.append(chunk)
        try:
            return model.model_validate_json(b"".join(chunks))
        except ValidationError:
            raise DependencyUnavailable("Dify returned an invalid identity response.") from None

    async def resolve(
        self,
        *,
        cookie_header: str | None,
        authorization: str | None,
        csrf_token: str | None,
    ) -> Principal:
        """Authenticate a single request without changing its chosen workspace.

        Dify's current-workspace endpoint may itself select another available
        workspace when the previous one was archived. We only accept its final
        confirmed scope, never a tenant or actor supplied by a business command.
        """
        headers = _forwarded_headers(cookie_header, authorization, csrf_token)
        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                profile = await self._read(client, "GET", "account/profile", _Profile)
                workspace = await self._read(client, "POST", "workspaces/current", _Workspace)
                if workspace.status != "normal":
                    raise AccessDenied("An active workspace is required.")
                try:
                    role = _ROLE_ADAPTER.validate_python(workspace.role)
                except ValidationError:
                    raise AccessDenied("The workspace role is not supported.") from None
                members = await self._read(client, "GET", "workspaces/current/members", _Members)
                matching_members = [member for member in members.accounts if member.id == profile.id]
                if len(matching_members) != 1:
                    raise AccessDenied("Current workspace membership could not be confirmed.")
                member = matching_members[0]
                if member.status != "active":
                    raise Unauthenticated("An active account is required.")
                if member.role != workspace.role:
                    raise AccessDenied("Workspace membership changed during authentication.")
                final_workspace = await self._read(client, "POST", "workspaces/current", _Workspace)
                if final_workspace != workspace:
                    raise AccessDenied("Current workspace changed during authentication.")
                return Principal(
                    actor_id=profile.id,
                    workspace_id=workspace.id,
                    workspace_role=role,
                    display_name=profile.name,
                )
        except (TimeoutError, httpx.RequestError):
            raise DependencyUnavailable("Dify identity lookup timed out or was interrupted.") from None
