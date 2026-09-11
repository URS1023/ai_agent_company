"""Bounded, uncached native lookup against an operator-owned enterprise origin.

No environment is read at import. Factory composition supplies the dedicated
credential and server-derived scope. Transport failures fail the lookup rather than
falling back to an unregistered managed invocation. Redirects and proxy environment
inheritance are disabled so the service credential stays at the configured origin.
"""

import json
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from core.workflow.enterprise_execution import ManagedToolRegistry

_FIELDS = {
    "workspace_id",
    "app_id",
    "workflow_id",
    "graph_hash",
    "provider_id",
    "tool_name",
    "credential_id",
    "node_id",
}


class RegistrationUnavailableError(ValueError):
    def __init__(self) -> None:
        super().__init__("enterprise_registration_unavailable")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise RegistrationUnavailableError()
        result[name] = value
    return result


class NativeRegistrationClient:
    _origin: str
    _token: SecretStr
    _transport: httpx.BaseTransport | None

    def __init__(
        self,
        origin: str,
        token: SecretStr,
        *,
        allow_insecure_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ({"https", "http"} if allow_insecure_http else {"https"})
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
            or origin != origin.strip()
            or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token.get_secret_value()) is None
        ):
            raise ValueError("enterprise_registration_configuration_invalid")
        self._origin, self._token, self._transport = origin, SecretStr(token.get_secret_value()), transport

    def fetch(self, workspace_id: str, app_id: str, workflow_id: str) -> ManagedToolRegistry:
        """Fetch one exact registration; null means absent, every other failure is explicit."""
        try:
            for identity in (workspace_id, app_id, workflow_id):
                parsed = UUID(identity)
                if not parsed.int or str(parsed) != identity:
                    raise RegistrationUnavailableError()
            with httpx.Client(
                transport=self._transport,
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "GET",
                    self._origin + "/enterprise/internal/v1/workflow-registration",
                    params={"workspace_id": workspace_id, "app_id": app_id, "workflow_id": workflow_id},
                    headers={
                        "X-Enterprise-Registration-Token": self._token.get_secret_value(),
                        "Accept": "application/json",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise RegistrationUnavailableError()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > 8192:
                            raise RegistrationUnavailableError()
                        content.extend(chunk)
            value = json.loads(content, object_pairs_hook=_unique)
            if value is None:
                return ManagedToolRegistry()
            if (
                not isinstance(value, dict)
                or set(value) != _FIELDS
                or not all(isinstance(v, str) for v in value.values())
            ):
                raise RegistrationUnavailableError()
            if (value["workspace_id"], value["app_id"], value["workflow_id"]) != (workspace_id, app_id, workflow_id):
                raise RegistrationUnavailableError()
            if (value["provider_id"], value["tool_name"], value["node_id"]) != (
                "enterprise/enterprise_device_assessment/enterprise_device",
                "evaluate_device",
                "assessment",
            ) or re.fullmatch(r"[0-9a-f]{64}", value["graph_hash"]) is None:
                raise RegistrationUnavailableError()
            value.pop("graph_hash")
            return ManagedToolRegistry.from_json(json.dumps([value]))
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise RegistrationUnavailableError() from None


def resolve_factory_registry(
    static: ManagedToolRegistry,
    client: NativeRegistrationClient | None,
    workspace_id: str,
    app_id: str,
    workflow_id: str,
    *,
    invoke_from: str,
) -> ManagedToolRegistry:
    """Merge one published version's registration without overriding explicit policy.

    Non-service executions retain the static policy (whose invocation checks remain
    authoritative) without network I/O. Conflicting node credentials are errors,
    not a precedence choice that could hide revocation or credential drift.
    """
    if client is None or invoke_from != "service-api":
        return static
    dynamic = client.fetch(workspace_id, app_id, workflow_id)
    combined = list(static.registrations)
    for item in dynamic.registrations:
        identity = (item.workspace_id, item.app_id, item.workflow_id, item.node_id)
        for previous in static.registrations:
            if (previous.workspace_id, previous.app_id, previous.workflow_id, previous.node_id) == identity:
                if previous != item:
                    raise RegistrationUnavailableError()
        if item not in combined:
            combined.append(item)
    return ManagedToolRegistry(tuple(combined))
