"""Append-only trial captures for later review; persistence does not approve SQL."""

from typing import Literal, Protocol, Self

from pydantic import AwareDatetime, Field, model_validator

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.domain.data_sources import SqlRead

from .contracts import Contract, Identifier, canonical_hash
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_trial_result import SqlTrialResult
from .errors import Conflict


class SqlTrialEvidence(Contract):
    schema_version: Literal[1] = 1
    trial_id: Identifier
    actor_id: Identifier
    recorded_at: AwareDatetime
    result: SqlTrialResult

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if self.recorded_at < self.result.captured_at:
            raise ValueError("Evidence recording precedes capture")
        return self


def validate_trial_evidence(evidence: SqlTrialEvidence, draft: SqlDraftRecord) -> None:
    result = evidence.result
    slot = next((slot for slot in draft.proposals.slots if slot.slot_id == result.slot_id), None)
    if (
        result.workspace_id != draft.workspace_id
        or result.dashboard_id != draft.dashboard_id
        or result.draft_id != draft.draft_id
        or result.dashboard_revision != draft.dashboard_revision
        or result.design_identity != draft.design_identity
        or result.source != draft.source
        or result.draft_hash != canonical_hash(draft.model_dump(mode="json"))
        or result.captured_at < draft.created_at
        or slot is None
        or not set(slot.field_map.values()).issubset(result.columns)
        or result.read_fingerprint
        != read_fingerprint(
            SqlRead(source=draft.source, read_id=draft.draft_id, revision=result.draft_hash, sql=slot.sql), ()
        )
    ):
        raise Conflict("sql_trial_evidence_draft_mismatch")


class SqlTrialSummary(Contract):
    workspace_id: Identifier
    dashboard_id: Identifier
    draft_id: Identifier
    trial_id: Identifier
    actor_id: Identifier
    recorded_at: AwareDatetime


class SqlTrialPage(Contract):
    items: tuple[SqlTrialSummary, ...] = Field(max_length=100)
    next_cursor: Identifier | None = None


class SqlTrialEvidenceRepository(Protocol):
    def create(self, evidence: SqlTrialEvidence) -> SqlTrialEvidence: ...

    def get(self, workspace_id: str, trial_id: str) -> SqlTrialEvidence: ...

    def list(
        self, workspace_id: str, dashboard_id: str, draft_id: str, *, after: str | None = None, limit: int = 20
    ) -> tuple[SqlTrialSummary, ...]: ...
