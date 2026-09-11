"""One-shot, workspace-guarded native plugin credential preparation, never publication."""

import asyncio
import math
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from enterprise_platform.application.contracts import JsonObject, Principal
from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.workflow_plugin_credentials import (
    NativePluginCredentialOutcome,
    PluginCredentialConfiguration,
    PluginCredentialRejected,
    canonical_uuid,
)
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers

_PROVIDER = "enterprise/enterprise_device_assessment/enterprise_device"
_PLUGIN = "enterprise/enterprise_device_assessment"
_PATH = "workspaces/current/tool-provider/builtin/" + _PROVIDER


class _Projection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Capabilities(_Projection):
    enabled: bool
    credential_setup_enabled: bool
    workspace_id: str


class _Provider(_Projection):
    id: str
    name: str
    plugin_id: str | None = None
    plugin_unique_identifier: str | None = None


class _Tool(_Projection):
    name: str


class _Credential(_Projection):
    id: str
    name: str
    provider: str
    credential_type: str
    credentials: JsonObject = Field(default_factory=dict, repr=False)
    visibility: str
    created_by: str
    from_other_member: bool = False


class _Info(_Projection):
    supported_credential_types: list[str]
    credentials: list[_Credential]


class _Created(_Projection):
    result: str


class DifyWorkflowPluginCredentialClient:
    _base_url: str
    _configuration: PluginCredentialConfiguration
    _transport: httpx.AsyncBaseTransport | None
    _timeout_seconds: float
    _max_response_bytes: int

    def __init__(
        self,
        *,
        base_url: str,
        configuration: PluginCredentialConfiguration,
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
        self._configuration = PluginCredentialConfiguration.model_validate(
            configuration.model_dump() | {"signing_secret": configuration.signing_secret}
        )
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def _request(
        self,
        client: httpx.AsyncClient,
        workspace_id: str,
        method: str,
        path: str,
        body: JsonObject | None = None,
        *,
        require_ack: bool = True,
    ) -> bytes:
        async with client.stream(method, path, json=body) as response:
            if response.status_code != 200 or (
                require_ack and response.headers.get("X-Enterprise-Workspace") != workspace_id
            ):
                raise ValueError("native_plugin_protocol_mismatch")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise ValueError("native_plugin_response_too_large")
                chunks.append(chunk)
            return b"".join(chunks)

    def _uncertain(self, reason: str) -> NativePluginCredentialOutcome:
        return NativePluginCredentialOutcome(
            state="uncertain",
            plugin_unique_identifier=self._configuration.expected_plugin_unique_identifier,
            reason_code=reason,
        )

    def _matching(self, item: _Credential, principal: Principal, app_id: str) -> bool:
        config = self._configuration
        return (
            item.provider == _PROVIDER
            and item.credential_type == "api-key"
            and item.visibility == "all_team_members"
            and item.created_by == principal.actor_id
            and not item.from_other_member
            and item.credentials.get("origin") == config.origin
            and item.credentials.get("key_id") == config.key_id
            and item.credentials.get("expected_app_id") == app_id
            and item.credentials.get("allow_insecure_http") is config.allow_insecure_http
        )

    async def prepare(
        self, principal: Principal, session: NativeSetupSession, *, app_id: str, operation_id: str
    ) -> NativePluginCredentialOutcome:
        post_started = False
        try:
            canonical_uuid(principal.workspace_id)
            canonical_uuid(app_id)
            canonical_uuid(operation_id)
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers["X-Enterprise-Expected-Workspace"] = principal.workspace_id
            name = "ent-" + operation_id.replace("-", "")[:26]
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=self._timeout_seconds,
                ) as client:
                    capabilities = _Capabilities.model_validate_json(
                        await self._request(
                            client,
                            principal.workspace_id,
                            "GET",
                            "enterprise/workflow-setup/capabilities",
                            require_ack=False,
                        )
                    )
                    if (
                        not capabilities.enabled
                        or not capabilities.credential_setup_enabled
                        or capabilities.workspace_id != principal.workspace_id
                    ):
                        raise PluginCredentialRejected()
                    providers = TypeAdapter(list[_Provider]).validate_json(
                        await self._request(
                            client, principal.workspace_id, "GET", "workspaces/current/tool-providers?type=builtin"
                        )
                    )
                    matches = [item for item in providers if item.id == _PROVIDER]
                    if (
                        len(matches) != 1
                        or matches[0].name != _PROVIDER
                        or matches[0].plugin_id != _PLUGIN
                        or matches[0].plugin_unique_identifier != self._configuration.expected_plugin_unique_identifier
                    ):
                        raise PluginCredentialRejected()
                    tools = TypeAdapter(list[_Tool]).validate_json(
                        await self._request(client, principal.workspace_id, "GET", _PATH + "/tools")
                    )
                    if sum(item.name == "evaluate_device" for item in tools) != 1:
                        raise PluginCredentialRejected()
                    info = _Info.model_validate_json(
                        await self._request(client, principal.workspace_id, "GET", _PATH + "/credential/info")
                    )
                    if "api-key" not in info.supported_credential_types:
                        raise PluginCredentialRejected()
                    if any(item.name == name for item in info.credentials):
                        return self._uncertain("native_plugin_credential_name_exists")
                    if len(info.credentials) >= 100:
                        raise PluginCredentialRejected("native_plugin_credential_capacity")
                    config = self._configuration
                    body: JsonObject = {
                        "name": name,
                        "type": "api-key",
                        "visibility": "all_team_members",
                        "credentials": {
                            "origin": config.origin,
                            "key_id": config.key_id,
                            "secret": config.signing_secret.get_secret_value(),
                            "expected_app_id": app_id,
                            "allow_insecure_http": config.allow_insecure_http,
                        },
                    }
                    post_started = True
                    created = _Created.model_validate_json(
                        await self._request(client, principal.workspace_id, "POST", _PATH + "/add", body)
                    )
                    if created.result != "success":
                        return self._uncertain("native_plugin_credential_create_unconfirmed")
                    info = _Info.model_validate_json(
                        await self._request(client, principal.workspace_id, "GET", _PATH + "/credential/info")
                    )
                    selected = [item for item in info.credentials if item.name == name]
                    if len(selected) != 1 or not self._matching(selected[0], principal, app_id):
                        return self._uncertain("native_plugin_credential_readback_mismatch")
                    credential_id = canonical_uuid(selected[0].id)
                    return NativePluginCredentialOutcome(
                        state="credential_created",
                        credential_id=credential_id,
                        plugin_unique_identifier=config.expected_plugin_unique_identifier,
                    )
        except PluginCredentialRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError):
            if post_started:
                return self._uncertain("native_plugin_credential_transport_uncertain")
            raise PluginCredentialRejected() from None
