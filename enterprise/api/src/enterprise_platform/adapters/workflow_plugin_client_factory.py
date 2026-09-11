"""Resolve immutable per-app plugin clients without HTTP or key registration."""

import math
from urllib.parse import urlsplit

import httpx

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_plugin_credentials import (
    NativePluginCredentialOutcome,
    PluginCredentialRejected,
    WorkflowPluginCredentialClient,
    canonical_uuid,
)
from enterprise_platform.application.workflow_plugin_profiles import (
    PluginCredentialProfile,
    WorkflowPluginProfileRegistry,
    derive_plugin_configuration,
)
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .workflow_plugin_credential_client import DifyWorkflowPluginCredentialClient


class _ScopedPluginClient:
    _workspace_id: str
    _app_id: str
    _client: DifyWorkflowPluginCredentialClient

    def __init__(self, workspace_id: str, app_id: str, client: DifyWorkflowPluginCredentialClient) -> None:
        self._workspace_id, self._app_id, self._client = workspace_id, app_id, client

    async def prepare(
        self, principal: Principal, session: NativeSetupSession, *, app_id: str, operation_id: str
    ) -> NativePluginCredentialOutcome:
        if (principal.workspace_id, app_id) != (self._workspace_id, self._app_id):
            raise PluginCredentialRejected("plugin_configuration_scope_mismatch")
        return await self._client.prepare(principal, session, app_id=app_id, operation_id=operation_id)


class ProfiledWorkflowPluginClientFactory:
    _registry: WorkflowPluginProfileRegistry
    _base_url: str
    _transport: httpx.AsyncBaseTransport | None
    _timeout_seconds: float
    _max_response_bytes: int

    def __init__(
        self,
        *,
        base_url: str,
        profiles: tuple[PluginCredentialProfile, ...],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        try:
            parsed = urlsplit(base_url)
            httpx.URL(base_url)
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
        self._registry = WorkflowPluginProfileRegistry(profiles)
        self._base_url = base_url
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def resolve_profile(
        self, workspace_id: str, *, configuration_ref: str, configuration_revision: int
    ) -> PluginCredentialProfile:
        return self._registry.resolve_profile(
            workspace_id, configuration_ref=configuration_ref, configuration_revision=configuration_revision
        )

    async def resolve(
        self, principal: Principal, *, app_id: str, configuration_ref: str, configuration_revision: int
    ) -> WorkflowPluginCredentialClient:
        try:
            if not isinstance(app_id, str):
                raise ValueError()
            canonical_uuid(app_id)
            profile = self.resolve_profile(
                principal.workspace_id,
                configuration_ref=configuration_ref,
                configuration_revision=configuration_revision,
            )
            client = DifyWorkflowPluginCredentialClient(
                base_url=self._base_url,
                configuration=derive_plugin_configuration(profile, app_id=app_id),
                transport=self._transport,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
            )
            return _ScopedPluginClient(principal.workspace_id, app_id, client)
        except ValueError:
            raise PluginCredentialRejected("plugin_configuration_unavailable") from None
