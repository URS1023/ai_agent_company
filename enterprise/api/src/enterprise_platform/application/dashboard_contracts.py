"""Public data views and management-only query references; never SQL or connections."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictBool, StringConstraints, model_validator

from enterprise_platform.domain import dashboard as d

from .contracts import Contract, Identifier, canonical_json
from .dashboard_query_registry import DashboardQueryChoice
from .dashboard_refresh_service import DashboardRecord


class DashboardQueryCatalog(Contract):
    items: tuple[DashboardQueryChoice, ...]


class DashboardString(Contract):
    kind: Literal["string"] = "string"
    value: str


class DashboardInteger(Contract):
    kind: Literal["integer"] = "integer"
    value: str = Field(pattern=r"^-?(0|[1-9][0-9]*)$")


class DashboardDecimal(Contract):
    kind: Literal["decimal"] = "decimal"
    value: str = Field(pattern=r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")


class DashboardBoolean(Contract):
    kind: Literal["boolean"] = "boolean"
    value: StrictBool


class DashboardNull(Contract):
    kind: Literal["null"] = "null"
    value: None = None


type DashboardCell = Annotated[
    DashboardString | DashboardInteger | DashboardDecimal | DashboardBoolean | DashboardNull,
    Field(discriminator="kind"),
]


class DashboardSlotView(Contract):
    slot_id: str
    rows: tuple[dict[str, DashboardCell], ...]


class DashboardBatchView(Contract):
    batch_id: str
    slots: tuple[DashboardSlotView, ...]


class DashboardFailure(Contract):
    slot_id: str
    code: str


class DashboardView(Contract):
    id: Identifier
    name: str = ""
    revision: int = Field(ge=1)
    template_id: str
    design_identity: str
    renderer_build_id: str
    status: Literal["empty", "ready", "failed"]
    current: DashboardBatchView | None
    last_attempt_id: str | None
    failures: tuple[DashboardFailure, ...]


class DashboardRefreshCommand(Contract):
    expected_revision: int = Field(ge=1, strict=True)


class DashboardCreateCommand(Contract):
    template_id: Identifier
    expected_design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)] | None = (
        None
    )


class DashboardSummary(Contract):
    id: Identifier
    name: str = ""
    revision: int = Field(ge=1)
    template_id: str
    status: Literal["empty", "ready", "failed"]


class DashboardPage(Contract):
    items: tuple[DashboardSummary, ...]
    next_cursor: Identifier | None


class DashboardTemplateView(Contract):
    template_id: str
    template_revision: int
    design_identity: str
    renderer_build_id: str
    slots: tuple[d.SlotContract, ...]


class DashboardTemplateCatalog(Contract):
    items: tuple[DashboardTemplateView, ...]


class DashboardBindingItem(Contract):
    slot_id: d.Name
    query_ref: d.Name
    query_revision: d.Name
    field_map: dict[d.Name, d.Name] = Field(min_length=1, max_length=100)
    parameters: dict[d.Name, JsonValue] = Field(default_factory=dict, max_length=100)

    def execution(self) -> d.ExecutionBinding:
        return d.ExecutionBinding.from_proposal(
            d.BindingProposal(
                slotId=self.slot_id, queryRef=self.query_ref, fieldMap=self.field_map, parameters=self.parameters
            ),
            query_revision=self.query_revision,
        )


class DashboardBindingWrite(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    expected_design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindings: tuple[DashboardBindingItem, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def bounded_document(self) -> Self:
        if len(canonical_json(self.model_dump()).encode("utf-8")) > 262144:
            raise ValueError("Binding configuration exceeds limit")
        if len({item.slot_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate binding slot")
        return self


class DashboardBindingView(Contract):
    dashboard_id: Identifier
    revision: int
    design_identity: str
    slots: tuple[d.SlotContract, ...]
    bindings: tuple[DashboardBindingItem, ...]


def as_dashboard_bindings(record: DashboardRecord) -> DashboardBindingView:
    return DashboardBindingView(
        dashboard_id=record.dashboard_id,
        revision=record.revision,
        design_identity=record.design.identity,
        slots=record.design.slots,
        bindings=tuple(
            DashboardBindingItem(
                slot_id=binding.slot_id,
                query_ref=binding.proposal.query_ref,
                query_revision=binding.query_revision,
                field_map=binding.proposal.field_map,
                parameters=binding.proposal.parameters,
            )
            for binding in record.bindings
        ),
    )


def _public_cell(value: d.CellValue) -> DashboardCell:
    if value is None:
        return DashboardNull()
    if type(value) is bool:
        return DashboardBoolean(value=value)
    if type(value) is int:
        return DashboardInteger(value=str(value))
    if isinstance(value, Decimal) and value.is_finite():
        return DashboardDecimal(value=str(value))
    if type(value) is str:
        return DashboardString(value=value)
    raise d.DashboardError("invalid_value")


def as_dashboard_view(record: DashboardRecord) -> DashboardView:
    current = record.state.current
    if current is not None:
        d.validate_committed_batch(record.design, current)
    return DashboardView(
        id=record.dashboard_id,
        name=record.name or record.dashboard_id,
        revision=record.revision,
        template_id=record.design.template_id,
        design_identity=record.design.identity,
        renderer_build_id=record.design.renderer_build_id,
        status=record.state.status,
        last_attempt_id=record.state.last_attempt_id,
        failures=tuple(
            DashboardFailure(slot_id=failure.slot_id, code=failure.code) for failure in record.state.failures
        ),
        current=None
        if current is None
        else DashboardBatchView(
            batch_id=current.batch_id,
            slots=tuple(
                DashboardSlotView(
                    slot_id=slot.slot_id,
                    rows=tuple({name: _public_cell(value) for name, value in row} for row in slot.rows),
                )
                for slot in current.slots
            ),
        ),
    )
