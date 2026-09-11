"""Data-only preview projection of saved evidence, never a committed dashboard batch.

Callers must authorize the evidence against current source grants before projection.
The frozen renderer/template identity is preserved; business meaning is not inferred.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from enterprise_platform.domain.dashboard import DesignSnapshot

from .contracts import Contract, Identifier
from .dashboard_contracts import DashboardCell, DashboardSlotView
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_trial_assessment import assess_sql_trial
from .dashboard_sql_trial_evidence import SqlTrialEvidence
from .errors import InvalidInput


class SqlTrialPreview(Contract):
    workspace_id: Identifier
    dashboard_id: Identifier
    draft_id: Identifier
    trial_id: Identifier
    dashboard_revision: int = Field(strict=True, ge=1)
    template_id: str
    design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_build_id: str
    status: Literal["preview_only"] = "preview_only"
    semantic_status: Literal["not_reviewed"] = "not_reviewed"
    unit_status: Literal["not_reviewed"] = "not_reviewed"
    sample_status: Literal["sample_compatible", "insufficient_samples"]
    slot: DashboardSlotView


class SqlDraftPreviewSelection(Contract):
    trial_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_trials(self) -> Self:
        if len(set(self.trial_ids)) != len(self.trial_ids):
            raise ValueError("Duplicate selected trial")
        return self


class SqlDraftPreview(Contract):
    status: Literal["preview_only"] = "preview_only"
    snapshot_status: Literal["independent_trials"] = "independent_trials"
    items: tuple[SqlTrialPreview, ...] = Field(min_length=1, max_length=100)
    missing_required_slots: tuple[str, ...]


def preview_sql_draft(
    evidence: tuple[SqlTrialEvidence, ...], draft: SqlDraftRecord, design: DesignSnapshot
) -> SqlDraftPreview:
    if not 1 <= len(evidence) <= 100:
        raise InvalidInput("sql_draft_preview_selection_limit")
    trial_ids = {item.trial_id for item in evidence}
    slot_ids = {item.result.slot_id for item in evidence}
    if len(trial_ids) != len(evidence) or len(slot_ids) != len(evidence):
        raise InvalidInput("sql_draft_preview_duplicate_selection")
    total_bytes = 0
    for item in evidence:
        total_bytes += len(item.model_dump_json().encode("utf-8"))
        if total_bytes > 2 * 1024 * 1024:
            raise InvalidInput("sql_draft_preview_size_limit")
    previews = {item.result.slot_id: preview_sql_trial(item, draft, design) for item in evidence}
    return SqlDraftPreview(
        items=tuple(previews[slot.slot_id] for slot in design.slots if slot.slot_id in previews),
        missing_required_slots=tuple(
            slot.slot_id for slot in design.slots if slot.required and slot.slot_id not in previews
        ),
    )


def preview_sql_trial(evidence: SqlTrialEvidence, draft: SqlDraftRecord, design: DesignSnapshot) -> SqlTrialPreview:
    assessment = assess_sql_trial(evidence, draft, design)
    if assessment.status == "incompatible":
        raise InvalidInput("sql_trial_preview_incompatible")
    result = evidence.result
    proposal = next(slot for slot in draft.proposals.slots if slot.slot_id == result.slot_id)
    indexes = {name: index for index, name in enumerate(result.columns)}
    rows: list[dict[str, DashboardCell]] = []
    for row in result.rows:
        mapped: dict[str, DashboardCell] = {}
        for field, column in proposal.field_map.items():
            cell = row[indexes[column]]
            if cell.kind == "date" or cell.kind == "datetime":
                raise InvalidInput("sql_trial_preview_incompatible")
            mapped[field] = cell
        rows.append(mapped)
    return SqlTrialPreview(
        workspace_id=result.workspace_id,
        dashboard_id=result.dashboard_id,
        draft_id=result.draft_id,
        trial_id=evidence.trial_id,
        dashboard_revision=result.dashboard_revision,
        template_id=design.template_id,
        design_identity=design.identity,
        renderer_build_id=design.renderer_build_id,
        sample_status=assessment.status,
        slot=DashboardSlotView(slot_id=result.slot_id, rows=tuple(rows)),
    )
