"""Assess saved samples against fixed slot types, never approve SQL or infer units."""

from typing import Literal

from pydantic import Field

from enterprise_platform.domain.dashboard import DesignSnapshot

from .contracts import Contract, Identifier
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_proposals import Name
from .dashboard_sql_trial_evidence import SqlTrialEvidence, validate_trial_evidence
from .errors import Conflict


class SqlTrialIssue(Contract):
    code: Literal["unknown_field", "missing_binding", "column_type_mismatch", "null_not_allowed", "row_limit_exceeded"]
    field: Name | None = None
    row_index: int | None = Field(default=None, ge=0)


class SqlTrialAssessment(Contract):
    trial_id: Identifier
    slot_id: Name
    status: Literal["sample_compatible", "incompatible", "insufficient_samples"]
    semantic_status: Literal["not_reviewed"] = "not_reviewed"
    unit_status: Literal["not_reviewed"] = "not_reviewed"
    issues: tuple[SqlTrialIssue, ...] = Field(max_length=100)
    issues_truncated: bool = False


def assess_sql_trial(evidence: SqlTrialEvidence, draft: SqlDraftRecord, design: DesignSnapshot) -> SqlTrialAssessment:
    """Internal pure assessment; callers must authorize access to evidence first."""
    validate_trial_evidence(evidence, draft)
    if design.identity != draft.design_identity:
        raise Conflict("sql_trial_design_mismatch")
    result = evidence.result
    slot = next((slot for slot in design.slots if slot.slot_id == result.slot_id), None)
    if slot is None:
        raise Conflict("sql_trial_slot_missing")
    proposal = next(proposal for proposal in draft.proposals.slots if proposal.slot_id == result.slot_id)
    issues: list[SqlTrialIssue] = []
    truncated = False

    def report(issue: SqlTrialIssue) -> None:
        nonlocal truncated
        if len(issues) < 100:
            issues.append(issue)
        else:
            truncated = True

    columns = {column.name: column for column in slot.columns}
    for name in sorted(proposal.field_map.keys() - columns.keys()):
        report(SqlTrialIssue(code="unknown_field", field=name))
    for column in slot.columns:
        if column.required and column.name not in proposal.field_map:
            report(SqlTrialIssue(code="missing_binding", field=column.name))
    if result.row_count > slot.row_limit:
        report(SqlTrialIssue(code="row_limit_exceeded"))
    indexes = {name: index for index, name in enumerate(result.columns)}
    observed: set[str] = set()
    mapped = {name: columns[name] for name in proposal.field_map if name in columns}
    for row_index, row in enumerate(result.rows):
        for name, column in mapped.items():
            cell = row[indexes[proposal.field_map[name]]]
            if cell.kind == "null":
                if not column.nullable:
                    report(SqlTrialIssue(code="null_not_allowed", field=name, row_index=row_index))
            elif cell.kind != column.kind:
                report(SqlTrialIssue(code="column_type_mismatch", field=name, row_index=row_index))
            else:
                observed.add(name)
        if truncated:
            break
    return SqlTrialAssessment(
        trial_id=evidence.trial_id,
        slot_id=result.slot_id,
        status="incompatible"
        if issues
        else ("sample_compatible" if mapped and observed == set(mapped) else "insufficient_samples"),
        issues=tuple(issues),
        issues_truncated=truncated,
    )
