"""Explicit operator schema grants, freshly read for each authorization check.

Operators protect this file and replace it atomically. This adapter does not create
grants, discover paths or substitute cached grants after a read/validation failure.
"""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from enterprise_platform.application.dashboard_schema_resolver import DashboardSchemaGrant
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.domain.data_sources import Policy


class _GrantDocument(Policy):
    schema_version: Literal[1]
    grants: tuple[DashboardSchemaGrant, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        keys = [(item.source.workspace_id, item.source.source_id) for item in self.grants]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate source grant")
        return self


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate authority key")
        result[key] = value
    return result


class FileDashboardSchemaGrants:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, workspace_id: str, source_id: str) -> DashboardSchemaGrant:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Schema grants exceed limit")
            json.loads(content, object_pairs_hook=_unique_keys)
            document = _GrantDocument.model_validate_json(content, strict=True)
        except (OSError, ValueError, RecursionError):
            raise DependencyUnavailable("dashboard_schema_grants_unavailable") from None
        for grant in document.grants:
            if (grant.source.workspace_id, grant.source.source_id) == (workspace_id, source_id):
                return grant
        raise AccessDenied("dashboard_schema_not_granted")
