"""Lossless transport of trial captures, not semantic approval or published data.

Only use this projection after DashboardSqlTrial completes current authorization and
capture checks. Naive database datetimes remain naive; no timezone or unit is inferred.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from enterprise_platform.domain.data_sources import FrozenRows, SourceRef, SourceValue

from .contracts import Contract, Identifier, Principal
from .dashboard_contracts import DashboardBoolean, DashboardDecimal, DashboardInteger, DashboardNull, DashboardString
from .dashboard_sql_proposals import Name
from .dashboard_sql_trial import SqlTrialCommand
from .errors import AccessDenied, DependencyUnavailable


class SqlTrialDate(Contract):
    kind: Literal["date"] = "date"
    value: str

    @field_validator("value")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Expected canonical date")
        return value


class SqlTrialDateTime(Contract):
    kind: Literal["datetime"] = "datetime"
    value: str

    @field_validator("value")
    @classmethod
    def iso_datetime(cls, value: str) -> str:
        if "T" not in value or datetime.fromisoformat(value).isoformat() != value:
            raise ValueError("Expected canonical datetime without inferred timezone")
        return value


type SqlTrialCell = Annotated[
    DashboardString
    | DashboardInteger
    | DashboardDecimal
    | DashboardBoolean
    | DashboardNull
    | SqlTrialDate
    | SqlTrialDateTime,
    Field(discriminator="kind"),
]


class SqlTrialResult(Contract):
    workspace_id: Identifier
    dashboard_id: Identifier
    draft_id: Identifier
    slot_id: Name
    dashboard_revision: int = Field(strict=True, ge=1)
    design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceRef
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: AwareDatetime
    columns: tuple[Name, ...] = Field(min_length=1, max_length=500)
    rows: tuple[tuple[SqlTrialCell, ...], ...] = Field(max_length=100000)
    row_count: int = Field(strict=True, ge=0, le=100000)
    status: Literal["trial_only"] = "trial_only"
    semantic_status: Literal["not_reviewed"] = "not_reviewed"

    @model_validator(mode="after")
    def consistent_capture(self) -> Self:
        if self.source.workspace_id != self.workspace_id or self.row_count != len(self.rows):
            raise ValueError("Trial scope or row count mismatch")
        if len(set(self.columns)) != len(self.columns) or any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Trial column contract mismatch")
        if len(self.model_dump_json().encode("utf-8")) > 512 * 1024:
            raise ValueError("Trial response exceeds wire limit")
        return self


def _cell(value: SourceValue) -> SqlTrialCell:
    if value is None:
        return DashboardNull()
    if type(value) is bool:
        return DashboardBoolean(value=value)
    if type(value) is int:
        return DashboardInteger(value=str(value))
    if isinstance(value, Decimal) and value.is_finite():
        return DashboardDecimal(value=str(value))
    if isinstance(value, datetime):
        return SqlTrialDateTime(value=value.isoformat())
    if isinstance(value, date):
        return SqlTrialDate(value=value.isoformat())
    if type(value) is str:
        return DashboardString(value=value)
    raise DependencyUnavailable("sql_trial_result_invalid")


def as_sql_trial_result(
    principal: Principal, dashboard_id: str, draft_id: str, command: SqlTrialCommand, capture: FrozenRows
) -> SqlTrialResult:
    if capture.source.workspace_id != principal.workspace_id:
        raise AccessDenied()
    if capture.read_id != draft_id:
        raise DependencyUnavailable("sql_trial_result_mismatch")
    try:
        return SqlTrialResult(
            workspace_id=principal.workspace_id,
            dashboard_id=dashboard_id,
            draft_id=draft_id,
            slot_id=command.slot_id,
            dashboard_revision=command.expected_revision,
            design_identity=command.expected_design_identity,
            source=capture.source,
            draft_hash=capture.read_revision,
            read_fingerprint=capture.read_fingerprint,
            captured_at=capture.captured_at,
            columns=capture.columns,
            rows=tuple(tuple(_cell(value) for value in row) for row in capture.rows),
            row_count=len(capture.rows),
        )
    except ValidationError:
        raise DependencyUnavailable("sql_trial_result_invalid") from None
