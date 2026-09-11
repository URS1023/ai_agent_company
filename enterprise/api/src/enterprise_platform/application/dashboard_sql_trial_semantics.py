"""Explicit fixed-slot sample constraints, not inferred formulas or SQL approval."""

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from enterprise_platform.domain.dashboard import DesignSnapshot

from .contracts import Contract, Identifier
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_proposals import Name
from .dashboard_sql_trial_evidence import SqlTrialEvidence, validate_trial_evidence
from .errors import Conflict


class NumericRule(Contract):
    field: Name
    minimum: Decimal | None = Field(default=None, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Reversed numeric bounds")
        return self


class SqlSlotSampleRules(Contract):
    design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    slot_id: Name
    min_rows: int = Field(default=0, strict=True, ge=0, le=100000)
    max_rows: int = Field(default=100000, strict=True, ge=0, le=100000)
    numeric: tuple[NumericRule, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.min_rows > self.max_rows or len({rule.field for rule in self.numeric}) != len(self.numeric):
            raise ValueError("Inconsistent sample rules")
        return self


class SqlSampleIssue(Contract):
    code: Literal["row_count_mismatch", "numeric_value_required", "numeric_out_of_range"]
    field: Name | None = None
    row_index: int | None = Field(default=None, ge=0)


class SqlSampleCheck(Contract):
    trial_id: Identifier
    slot_id: Name
    status: Literal["sample_constraints_passed", "sample_constraints_failed"]
    semantic_status: Literal["not_reviewed"] = "not_reviewed"
    issues: tuple[SqlSampleIssue, ...] = Field(max_length=100)
    issues_truncated: bool = False


def check_sample_rules(
    evidence: SqlTrialEvidence, draft: SqlDraftRecord, design: DesignSnapshot, rules: SqlSlotSampleRules
) -> SqlSampleCheck:
    validate_trial_evidence(evidence, draft)
    result = evidence.result
    if (
        design.identity != result.design_identity
        or rules.design_identity != design.identity
        or rules.slot_id != result.slot_id
    ):
        raise Conflict("sql_sample_rule_context_mismatch")
    slot = next((slot for slot in design.slots if slot.slot_id == result.slot_id), None)
    proposal = next(proposal for proposal in draft.proposals.slots if proposal.slot_id == result.slot_id)
    if slot is None or any(
        rule.field not in proposal.field_map or rule.field not in {column.name for column in slot.columns}
        for rule in rules.numeric
    ):
        raise Conflict("sql_sample_rule_field_mismatch")
    issues: list[SqlSampleIssue] = []
    truncated = False

    def report(issue: SqlSampleIssue) -> None:
        nonlocal truncated
        if len(issues) < 100:
            issues.append(issue)
        else:
            truncated = True

    if not rules.min_rows <= result.row_count <= min(rules.max_rows, slot.row_limit):
        report(SqlSampleIssue(code="row_count_mismatch"))
    indexes = {name: index for index, name in enumerate(result.columns)}
    for row_index, row in enumerate(result.rows):
        for rule in rules.numeric:
            cell = row[indexes[proposal.field_map[rule.field]]]
            if cell.kind != "integer" and cell.kind != "decimal":
                report(SqlSampleIssue(code="numeric_value_required", field=rule.field, row_index=row_index))
                continue
            value = Decimal(cell.value)
            if (rule.minimum is not None and value < rule.minimum) or (
                rule.maximum is not None and value > rule.maximum
            ):
                report(SqlSampleIssue(code="numeric_out_of_range", field=rule.field, row_index=row_index))
        if truncated:
            break
    return SqlSampleCheck(
        trial_id=evidence.trial_id,
        slot_id=result.slot_id,
        status="sample_constraints_failed" if issues else "sample_constraints_passed",
        issues=tuple(issues),
        issues_truncated=truncated,
    )
