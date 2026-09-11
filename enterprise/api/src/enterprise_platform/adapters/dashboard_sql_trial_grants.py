"""Operator-owned trial grants, read afresh with no cached authority fallback.

This file is distinct from metadata visibility grants. Operators protect its path
and replace it atomically; the adapter never creates grants or discovers files.
"""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from enterprise_platform.application.dashboard_sql_trial_authorization import SqlTrialGrant
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.domain.data_sources import Policy


class _TrialGrantDocument(Policy):
    schema_version: Literal[1]
    grants: tuple[SqlTrialGrant, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        keys = [(grant.source.workspace_id, grant.source.source_id) for grant in self.grants]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate trial source authority")
        return self


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate trial authority key")
        result[key] = value
    return result


class FileSqlTrialGrants:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, workspace_id: str, source_id: str) -> SqlTrialGrant:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Trial grants exceed size limit")
            json.loads(content, object_pairs_hook=_unique_keys)
            document = _TrialGrantDocument.model_validate_json(content, strict=True)
        except (OSError, ValueError, RecursionError):
            raise DependencyUnavailable("sql_trial_grants_unavailable") from None
        for grant in document.grants:
            if (grant.source.workspace_id, grant.source.source_id) == (workspace_id, source_id):
                return grant
        raise AccessDenied("sql_trial_not_granted")
