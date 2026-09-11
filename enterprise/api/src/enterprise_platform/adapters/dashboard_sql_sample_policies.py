"""Explicit operator-owned sample configuration, never model-generated authority.

Operators protect the configured file and replace it atomically. Every lookup reads
current bytes; this adapter grants neither evidence access nor query execution.
"""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRecord
from enterprise_platform.application.dashboard_sql_sample_policy import SqlSamplePolicy
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.domain.data_sources import Policy


class _SamplePolicyDocument(Policy):
    schema_version: Literal[1]
    policies: tuple[SqlSamplePolicy, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_revisions(self) -> Self:
        keys = [(item.source.workspace_id, item.policy_id, item.revision) for item in self.policies]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate sample policy revision")
        return self


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate sample policy key")
        result[key] = value
    return result


class FileSqlSamplePolicies:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> _SamplePolicyDocument:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Sample policy document exceeds limit")
            json.loads(content, object_pairs_hook=_unique_keys)
            return _SamplePolicyDocument.model_validate_json(content, strict=True)
        except (OSError, ValueError, RecursionError):
            raise DependencyUnavailable("sql_sample_policies_unavailable") from None

    def get(self, workspace_id: str, policy_id: str, revision: str) -> SqlSamplePolicy:
        for policy in self._read().policies:
            if (policy.source.workspace_id, policy.policy_id, policy.revision) == (workspace_id, policy_id, revision):
                return policy
        raise AccessDenied("sql_sample_policy_not_configured")

    def match(self, draft: SqlDraftRecord, slot_id: str) -> tuple[SqlSamplePolicy, ...]:
        document = self._read()
        slot = next((slot for slot in draft.proposals.slots if slot.slot_id == slot_id), None)
        if slot is None or draft.workspace_id != draft.source.workspace_id:
            return ()
        proposal_hash = canonical_hash(slot.model_dump(mode="json"))
        return tuple(
            sorted(
                (
                    policy
                    for policy in document.policies
                    if policy.enabled
                    and policy.source == draft.source
                    and policy.schema_revision == draft.schema_revision
                    and policy.proposal_hash == proposal_hash
                    and policy.rules.design_identity == draft.design_identity
                    and policy.rules.slot_id == slot_id
                ),
                key=lambda policy: (policy.policy_id, policy.revision),
            )
        )
