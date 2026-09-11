"""Opt-in execution identity for server-registered managed plugin tools.

This pure policy module has no environment, graphon, database or plugin dependencies.
The workflow factory supplies configuration; the runtime supplies resolved server
scope and a live system-variable getter. Tool/model parameters are not an identity
source. Only exact registrations receive metadata; no wildcard or credential fallback
is supported. The selected credential ID must be a canonical UUID so native credential
resolution does not silently choose the workspace default. Metadata is private to a
single invocation and carries neither credentials nor enterprise dispatch nonces.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID

_MAX_CONFIG_BYTES = 65536
_MAX_REGISTRATIONS = 256
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PROVIDER = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_FIELDS = frozenset({"workspace_id", "app_id", "workflow_id", "provider_id", "tool_name", "credential_id", "node_id"})


class ManagedExecutionConfigurationError(ValueError):
    def __init__(self) -> None:
        super().__init__("enterprise_managed_tools_configuration_invalid")


class ManagedExecutionIdentityError(ValueError):
    def __init__(self) -> None:
        super().__init__("enterprise_execution_identity_invalid")


class NativeExecutionMetadata(TypedDict):
    workspace_id: str
    app_id: str
    workflow_id: str
    native_run_id: str
    node_id: str
    node_execution_id: str
    invoke_from: Literal["service-api"]


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return bool(parsed.int) and str(parsed) == value


@dataclass(frozen=True, slots=True)
class ManagedToolRegistration:
    workspace_id: str
    app_id: str
    workflow_id: str
    provider_id: str
    tool_name: str
    credential_id: str
    node_id: str

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)
                for value in (self.workspace_id, self.app_id, self.tool_name, self.node_id)
            )
            or not isinstance(self.provider_id, str)
            or len(self.provider_id) > 256
            or not _PROVIDER.fullmatch(self.provider_id)
            or not _canonical_uuid(self.workflow_id)
            or not _canonical_uuid(self.credential_id)
        ):
            raise ManagedExecutionConfigurationError()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ManagedExecutionConfigurationError()
        result[name] = value
    return result


def _text_field(document: dict[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str):
        raise ManagedExecutionConfigurationError()
    return value


@dataclass(frozen=True, slots=True)
class ManagedToolRegistry:
    """Immutable, bounded registrations captured once per graph factory."""

    registrations: tuple[ManagedToolRegistration, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.registrations, tuple)
            or len(self.registrations) > _MAX_REGISTRATIONS
            or any(not isinstance(item, ManagedToolRegistration) for item in self.registrations)
            or len(set(self.registrations)) != len(self.registrations)
        ):
            raise ManagedExecutionConfigurationError()

    @classmethod
    def from_json(cls, raw: str) -> "ManagedToolRegistry":
        try:
            if len(raw.encode("utf-8")) > _MAX_CONFIG_BYTES:
                raise ManagedExecutionConfigurationError()
            if not raw.strip():
                return cls()
            document: object = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(document, list) or len(document) > _MAX_REGISTRATIONS:
                raise ManagedExecutionConfigurationError()
            entries: list[ManagedToolRegistration] = []
            for item in document:
                if not isinstance(item, dict) or set(item) != _FIELDS:
                    raise ManagedExecutionConfigurationError()
                entries.append(
                    ManagedToolRegistration(
                        workspace_id=_text_field(item, "workspace_id"),
                        app_id=_text_field(item, "app_id"),
                        workflow_id=_text_field(item, "workflow_id"),
                        provider_id=_text_field(item, "provider_id"),
                        tool_name=_text_field(item, "tool_name"),
                        credential_id=_text_field(item, "credential_id"),
                        node_id=_text_field(item, "node_id"),
                    )
                )
            return cls(tuple(entries))
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ManagedExecutionConfigurationError() from None

    def metadata_for(
        self,
        *,
        workspace_id: str,
        app_id: str,
        workflow_id: str | None,
        provider_id: str,
        tool_name: str,
        credential_id: str | None,
        node_id: str,
        node_execution_id: str | None,
        invoke_from: str,
        native_run_id_getter: Callable[[], str | None] | None,
    ) -> NativeExecutionMetadata | None:
        """Return a fresh JSON document, or no change for an unregistered tool.

        Matched tools fail closed if trusted execution state is incomplete, malformed,
        or from any entry other than service-api. Identity getter failures are redacted.
        A different published workflow UUID is a different, unregistered version.
        """
        for entry in self.registrations:
            if (
                entry.workspace_id,
                entry.app_id,
                entry.provider_id,
                entry.tool_name,
                entry.credential_id,
                entry.node_id,
            ) != (workspace_id, app_id, provider_id, tool_name, credential_id, node_id):
                continue
            if workflow_id is None:
                raise ManagedExecutionIdentityError()
            if entry.workflow_id != workflow_id:
                continue
            if invoke_from != "service-api" or not _canonical_uuid(node_execution_id) or native_run_id_getter is None:
                raise ManagedExecutionIdentityError()
            try:
                native_run_id = native_run_id_getter()
            except Exception:
                raise ManagedExecutionIdentityError() from None
            if not isinstance(native_run_id, str) or not _canonical_uuid(native_run_id):
                raise ManagedExecutionIdentityError()
            assert isinstance(node_execution_id, str)
            return NativeExecutionMetadata(
                workspace_id=workspace_id,
                app_id=app_id,
                workflow_id=workflow_id,
                native_run_id=native_run_id,
                node_id=node_id,
                node_execution_id=node_execution_id,
                invoke_from="service-api",
            )
        return None
