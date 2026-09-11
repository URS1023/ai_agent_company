"""Immutable server profiles derive per-app keys, never Service API tokens.

Deploy old profile versions alongside new ones. Rotation requires a new master
key ID and configuration revision; no hot reload or journal pinning of secret
material is implemented here. Derivation does not register an execution key.
"""

import hmac
from typing import Annotated, Self

from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .contracts import Contract, Identifier, canonical_json
from .managed_execution import ExecutionKey
from .workflow_plugin_credentials import (
    AsciiId,
    PluginCredentialConfiguration,
    PluginCredentialRejected,
    canonical_uuid,
)

ProfileDisplayName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"\S")]


class PluginProfileChoice(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    config_ref: Identifier
    config_revision: int = Field(strict=True, ge=1)
    display_name: ProfileDisplayName


class PluginCredentialProfile(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    workspace_id: str
    config_ref: Identifier
    config_revision: int = Field(ge=1)
    origin: str = Field(max_length=2048)
    allow_insecure_http: StrictBool = False
    expected_plugin_unique_identifier: str
    master_key_id: AsciiId
    master_secret: SecretStr = Field(repr=False, exclude=True)
    display_name: ProfileDisplayName | None = None

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        return canonical_uuid(value)

    @model_validator(mode="after")
    def valid_policy(self) -> Self:
        PluginCredentialConfiguration(
            origin=self.origin,
            key_id=self.master_key_id,
            signing_secret=self.master_secret,
            allow_insecure_http=self.allow_insecure_http,
            expected_plugin_unique_identifier=self.expected_plugin_unique_identifier,
        )
        return self


def _clone(profile: PluginCredentialProfile) -> PluginCredentialProfile:
    return PluginCredentialProfile.model_validate(
        profile.model_dump() | {"master_secret": SecretStr(profile.master_secret.get_secret_value())}
    )


class WorkflowPluginProfileRegistry:
    _profiles: dict[tuple[str, str, int], PluginCredentialProfile]

    def __init__(self, profiles: tuple[PluginCredentialProfile, ...]) -> None:
        collected: dict[tuple[str, str, int], PluginCredentialProfile] = {}
        masters: dict[str, SecretStr] = {}
        for candidate in profiles:
            profile = _clone(candidate)
            scope = (profile.workspace_id, profile.config_ref, profile.config_revision)
            if scope in collected:
                raise ValueError("Duplicate plugin profile revision")
            previous = masters.get(profile.master_key_id)
            if previous is not None and not hmac.compare_digest(
                previous.get_secret_value(), profile.master_secret.get_secret_value()
            ):
                raise ValueError("Conflicting plugin master key definition")
            collected[scope] = profile
            masters[profile.master_key_id] = profile.master_secret
        self._profiles = collected

    def list_profiles(self, workspace_id: str) -> tuple[PluginProfileChoice, ...]:
        try:
            if not isinstance(workspace_id, str):
                raise ValueError()
            canonical_uuid(workspace_id)
        except ValueError:
            raise PluginCredentialRejected("plugin_configuration_unavailable") from None
        profiles = sorted(
            (profile for profile in self._profiles.values() if profile.workspace_id == workspace_id),
            key=lambda profile: (profile.config_ref, profile.config_revision),
        )
        return tuple(
            PluginProfileChoice(
                config_ref=profile.config_ref,
                config_revision=profile.config_revision,
                display_name=profile.display_name or profile.config_ref,
            )
            for profile in profiles
        )

    def resolve_profile(
        self, workspace_id: str, *, configuration_ref: str, configuration_revision: int
    ) -> PluginCredentialProfile:
        try:
            if not isinstance(workspace_id, str):
                raise ValueError()
            canonical_uuid(workspace_id)
            TypeAdapter(Identifier).validate_python(configuration_ref)
            if type(configuration_revision) is not int or configuration_revision < 1:
                raise ValueError()
        except (ValueError, ValidationError):
            raise PluginCredentialRejected("plugin_configuration_unavailable") from None
        profile = self._profiles.get((workspace_id, configuration_ref, configuration_revision))
        if profile is None:
            raise PluginCredentialRejected("plugin_configuration_unavailable")
        return _clone(profile)


def _derive(profile: PluginCredentialProfile, app_id: str) -> tuple[str, SecretStr]:
    profile = _clone(profile)
    if not isinstance(app_id, str):
        raise ValueError("Invalid native application identity")
    canonical_uuid(app_id)
    context = canonical_json(
        {
            "workspace_id": profile.workspace_id,
            "app_id": app_id,
            "config_ref": profile.config_ref,
            "config_revision": profile.config_revision,
            "master_key_id": profile.master_key_id,
        }
    ).encode("utf-8")
    master = profile.master_secret.get_secret_value().encode("ascii")
    key_id = "ep-" + hmac.digest(master, b"enterprise-plugin/execution-key-id/v1\n" + context, "sha256").hex()
    secret = SecretStr(
        hmac.digest(master, b"enterprise-plugin/execution-signing-secret/v1\n" + context, "sha256").hex()
    )
    return key_id, secret


def derive_plugin_configuration(profile: PluginCredentialProfile, *, app_id: str) -> PluginCredentialConfiguration:
    profile = _clone(profile)
    key_id, secret = _derive(profile, app_id)
    return PluginCredentialConfiguration(
        origin=profile.origin,
        key_id=key_id,
        signing_secret=secret,
        allow_insecure_http=profile.allow_insecure_http,
        expected_plugin_unique_identifier=profile.expected_plugin_unique_identifier,
    )


def derive_execution_key(profile: PluginCredentialProfile, *, app_id: str, node_ids: frozenset[str]) -> ExecutionKey:
    profile = _clone(profile)
    key_id, secret = _derive(profile, app_id)
    return ExecutionKey(
        key_id=key_id, workspace_id=profile.workspace_id, app_id=app_id, secret=secret, node_ids=node_ids
    )
