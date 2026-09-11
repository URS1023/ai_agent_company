"""Deterministic data-only dashboards; this module performs no queries or rendering.

Design snapshots freeze visual JSON and rendering dependencies. Refreshing produces
immutable data batches, never modified visuals. A persistence adapter must atomically
compare the design, execution bindings and expected current batch when storing the
result of ``commit_refresh``. Query adapters must return the binding captured before
execution, not reconstruct provenance from whichever binding is current afterward.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, TypeAdapter, field_validator

type ColumnKind = Literal["string", "integer", "decimal", "boolean"]
type CellValue = str | int | bool | Decimal | None
type InputValue = CellValue | float
type FrozenRow = tuple[tuple[str, CellValue], ...]
type ErrorCode = Literal[
    "invalid_design",
    "revision_conflict",
    "unknown_slot",
    "duplicate_slot",
    "missing_binding",
    "unknown_field",
    "missing_field",
    "column_type_mismatch",
    "unit_mismatch",
    "row_limit_exceeded",
    "null_not_allowed",
    "invalid_value",
    "query_mismatch",
    "refresh_incomplete",
    "unexpected_slot",
    "invalid_batch",
    "truncated_result",
    "binding_conflict",
    "invalid_binding",
]
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=240, strip_whitespace=True)]
_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class DashboardError(ValueError):
    code: ErrorCode
    slot_id: str

    def __init__(self, code: ErrorCode, slot_id: str = "") -> None:
        self.code, self.slot_id = code, slot_id
        super().__init__(f"{code}: {slot_id}" if slot_id else code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class ResourceDigest(Contract):
    name: Name
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ColumnContract(Contract):
    name: Name
    kind: ColumnKind
    unit: Name | None = None
    required: bool = True
    nullable: bool = False


class SlotContract(Contract):
    slot_id: Name
    columns: tuple[ColumnContract, ...] = Field(min_length=1)
    row_limit: int = Field(default=1000, ge=1, le=100000)
    required: bool = True

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value: tuple[ColumnContract, ...]) -> tuple[ColumnContract, ...]:
        if len({column.name for column in value}) != len(value):
            raise ValueError("duplicate column contract")
        return value


class BindingProposal(Contract):
    """Only data references, mappings and parameters cross the AI binding boundary."""

    slot_id: Name = Field(alias="slotId")
    query_ref: Name = Field(alias="queryRef")
    field_map: dict[Name, Name] = Field(alias="fieldMap", min_length=1)
    parameters: dict[Name, JsonValue] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def finite_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _canonical(value)
        return value


def _canonical(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: JsonValue) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionBinding:
    """Server-resolved query revision and detached AI data proposal captured before I/O."""

    proposal_json: str
    query_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.query_revision, str) or not 1 <= len(self.query_revision.strip()) <= 240:
            raise DashboardError("invalid_binding")
        object.__setattr__(self, "query_revision", self.query_revision.strip())
        object.__setattr__(self, "proposal_json", _canonical(self.proposal.model_dump()))

    @classmethod
    def from_proposal(cls, proposal: BindingProposal, *, query_revision: str) -> "ExecutionBinding":
        return cls(_canonical(proposal.model_dump()), query_revision)

    @property
    def proposal(self) -> BindingProposal:
        return BindingProposal.model_validate_json(self.proposal_json)

    @property
    def slot_id(self) -> str:
        return self.proposal.slot_id

    @property
    def identity(self) -> str:
        return _hash({"proposal": self.proposal.model_dump(), "queryRevision": self.query_revision})


def binding_set_identity(bindings: tuple[ExecutionBinding, ...]) -> str:
    return _hash([binding.identity for binding in sorted(bindings, key=lambda item: item.slot_id)])


@dataclass(frozen=True, slots=True)
class DesignSnapshot:
    """Canonical text prevents shallow-frozen nested dictionaries from leaking edits."""

    template_id: str
    template_revision: int
    design_revision: int
    visual_json: str
    renderer_build_id: str
    component_schema_version: str
    asset_digests: tuple[ResourceDigest, ...] = ()
    font_digests: tuple[ResourceDigest, ...] = ()
    slots: tuple[SlotContract, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                self.template_id,
                self.renderer_build_id,
                self.component_schema_version,
            )
        ):
            raise DashboardError("invalid_design")
        if any(type(value) is not int or value < 1 for value in (self.template_revision, self.design_revision)):
            raise DashboardError("invalid_design")
        try:
            visual = _JSON.validate_json(self.visual_json)
            if not isinstance(visual, dict):
                raise DashboardError("invalid_design")
            object.__setattr__(self, "visual_json", _canonical(visual))
            slots = tuple(SlotContract.model_validate(slot.model_dump()) for slot in self.slots)
            object.__setattr__(self, "slots", slots)
            for field in ("asset_digests", "font_digests"):
                resources = tuple(ResourceDigest.model_validate(item.model_dump()) for item in getattr(self, field))
                if len({item.name for item in resources}) != len(resources):
                    raise DashboardError("invalid_design")
                object.__setattr__(self, field, tuple(sorted(resources, key=lambda item: item.name)))
        except (ValueError, TypeError) as error:
            raise DashboardError("invalid_design") from error
        if len({slot.slot_id for slot in self.slots}) != len(self.slots):
            raise DashboardError("duplicate_slot")

    @property
    def visual(self) -> dict[str, JsonValue]:
        value = _JSON.validate_json(self.visual_json)
        if not isinstance(value, dict):
            raise DashboardError("invalid_design")
        return value

    @property
    def visual_hash(self) -> str:
        return _hash(
            {
                "visual": self.visual,
                "rendererBuildId": self.renderer_build_id,
                "componentSchemaVersion": self.component_schema_version,
                "assets": [item.model_dump() for item in self.asset_digests],
                "fonts": [item.model_dump() for item in self.font_digests],
            }
        )

    @property
    def identity(self) -> str:
        return _hash(
            {
                "templateId": self.template_id,
                "templateRevision": self.template_revision,
                "designRevision": self.design_revision,
                "visualHash": self.visual_hash,
                "slots": [slot.model_dump() for slot in sorted(self.slots, key=lambda item: item.slot_id)],
            }
        )


def manual_design_revision(
    snapshot: DesignSnapshot,
    visual: dict[str, JsonValue],
    *,
    expected_revision: int,
    renderer_build_id: str | None = None,
    component_schema_version: str | None = None,
    asset_digests: tuple[ResourceDigest, ...] | None = None,
    font_digests: tuple[ResourceDigest, ...] | None = None,
) -> DesignSnapshot:
    """Explicit authoring operation; permission enforcement belongs to its caller."""
    if expected_revision != snapshot.design_revision:
        raise DashboardError("revision_conflict")
    return replace(
        snapshot,
        design_revision=snapshot.design_revision + 1,
        visual_json=_canonical(visual),
        renderer_build_id=snapshot.renderer_build_id if renderer_build_id is None else renderer_build_id,
        component_schema_version=(
            snapshot.component_schema_version if component_schema_version is None else component_schema_version
        ),
        asset_digests=snapshot.asset_digests if asset_digests is None else asset_digests,
        font_digests=snapshot.font_digests if font_digests is None else font_digests,
    )


class QueryColumn(Contract):
    name: Name
    kind: ColumnKind
    unit: Name | None = None


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Transient adapter output; decimal adapters must use Decimal, not float."""

    execution_binding: ExecutionBinding
    columns: tuple[QueryColumn, ...]
    rows: tuple[dict[str, InputValue], ...]
    truncated: bool = False

    @property
    def query_ref(self) -> str:
        return self.execution_binding.proposal.query_ref


@dataclass(frozen=True, slots=True)
class SlotData:
    slot_id: str
    query_ref: str
    binding_hash: str
    rows: tuple[FrozenRow, ...]


def _cell(value: InputValue, column: ColumnContract, slot_id: str) -> CellValue:
    if value is None:
        if not column.nullable:
            raise DashboardError("null_not_allowed", slot_id)
        return None
    if column.kind == "decimal" and isinstance(value, Decimal) and value.is_finite():
        return value
    if column.kind == "integer" and type(value) is int:
        return value
    if column.kind == "boolean" and type(value) is bool:
        return value
    if column.kind == "string" and type(value) is str:
        return value
    raise DashboardError("invalid_value", slot_id)


def validate_binding_columns(
    snapshot: DesignSnapshot, binding: ExecutionBinding, source: tuple[QueryColumn, ...]
) -> SlotContract:
    """Validate approved metadata without executing or fabricating a result batch."""
    proposal = binding.proposal
    slot = next((item for item in snapshot.slots if item.slot_id == proposal.slot_id), None)
    if slot is None:
        raise DashboardError("unknown_slot", proposal.slot_id)
    columns = {item.name: item for item in slot.columns}
    if set(proposal.field_map) - columns.keys():
        raise DashboardError("unknown_field", slot.slot_id)
    if any(item.required and item.name not in proposal.field_map for item in slot.columns):
        raise DashboardError("missing_binding", slot.slot_id)
    source_columns = {item.name: item for item in source}
    if len(source_columns) != len(source):
        raise DashboardError("unknown_field", slot.slot_id)
    for name, source_name in proposal.field_map.items():
        expected, actual = columns[name], source_columns.get(source_name)
        if actual is None:
            raise DashboardError("missing_field", slot.slot_id)
        if actual.kind != expected.kind:
            raise DashboardError("column_type_mismatch", slot.slot_id)
        if actual.unit != expected.unit:
            raise DashboardError("unit_mismatch", slot.slot_id)
    return slot


def validate_slot_result(snapshot: DesignSnapshot, binding: ExecutionBinding, result: QueryResult) -> SlotData:
    """Validate source metadata and every mapped cell, then detach immutable rows."""
    proposal = binding.proposal
    slot = validate_binding_columns(snapshot, binding, result.columns)
    if result.query_ref != proposal.query_ref:
        raise DashboardError("query_mismatch", slot.slot_id)
    if result.execution_binding.identity != binding.identity:
        raise DashboardError("binding_conflict", slot.slot_id)
    if result.truncated:
        raise DashboardError("truncated_result", slot.slot_id)
    if len(result.rows) > slot.row_limit:
        raise DashboardError("row_limit_exceeded", slot.slot_id)
    rows: list[FrozenRow] = []
    for row in result.rows:
        cells: list[tuple[str, CellValue]] = []
        for column in slot.columns:
            mapped_source = proposal.field_map.get(column.name)
            if mapped_source is None or mapped_source not in row:
                if column.required:
                    raise DashboardError("missing_field", slot.slot_id)
                continue
            cells.append((column.name, _cell(row[mapped_source], column, slot.slot_id)))
        rows.append(tuple(cells))
    return SlotData(slot.slot_id, proposal.query_ref, result.execution_binding.identity, tuple(rows))


@dataclass(frozen=True, slots=True)
class SlotSuccess:
    slot_id: str
    result: QueryResult


@dataclass(frozen=True, slots=True)
class SlotFailure:
    slot_id: str
    code: str


@dataclass(frozen=True, slots=True)
class RefreshBatch:
    batch_id: str
    design_identity: str
    expected_current_batch_id: str | None
    bindings: tuple[ExecutionBinding, ...]
    outcomes: tuple[SlotSuccess | SlotFailure, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bindings", tuple(self.bindings))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))

    @property
    def bindings_hash(self) -> str:
        return binding_set_identity(self.bindings)


@dataclass(frozen=True, slots=True)
class CommittedBatch:
    batch_id: str
    design_identity: str
    bindings_hash: str
    slots: tuple[SlotData, ...]


def validate_committed_batch(snapshot: DesignSnapshot, batch: CommittedBatch) -> None:
    """Check restored data against its frozen design without inventing historical query provenance."""
    if batch.design_identity != snapshot.identity:
        raise DashboardError("revision_conflict")
    expected = {slot.slot_id: slot for slot in snapshot.slots}
    received = {slot.slot_id: slot for slot in batch.slots}
    if len(received) != len(batch.slots):
        raise DashboardError("duplicate_slot")
    if received.keys() - expected.keys():
        raise DashboardError("unexpected_slot")
    if any(slot.required and slot.slot_id not in received for slot in snapshot.slots):
        raise DashboardError("refresh_incomplete")
    for slot in batch.slots:
        contract = expected[slot.slot_id]
        if len(slot.rows) > contract.row_limit:
            raise DashboardError("row_limit_exceeded", slot.slot_id)
        columns = {column.name: column for column in contract.columns}
        for row in slot.rows:
            values = dict(row)
            if len(values) != len(row) or values.keys() - columns.keys():
                raise DashboardError("unknown_field", slot.slot_id)
            for column in contract.columns:
                if column.name not in values:
                    if column.required:
                        raise DashboardError("missing_field", slot.slot_id)
                else:
                    _cell(values[column.name], column, slot.slot_id)


@dataclass(frozen=True, slots=True)
class RefreshState:
    design_identity: str
    status: Literal["empty", "ready", "failed"] = "empty"
    current: CommittedBatch | None = None
    last_attempt_id: str | None = None
    failures: tuple[SlotFailure, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.current is None


def commit_refresh(
    snapshot: DesignSnapshot,
    bindings: tuple[ExecutionBinding, ...],
    previous: RefreshState,
    batch: RefreshBatch,
) -> RefreshState:
    """Commit all configured slots or retain the entire previous batch with failures.

    Version conflicts raise DashboardError instead of being applied to another design.
    Empty query results are successful data, but an absent first batch stays absent.
    """
    identity = snapshot.identity
    current_id = previous.current.batch_id if previous.current else None
    if batch.design_identity != identity or previous.design_identity != identity:
        raise DashboardError("revision_conflict")
    if previous.current and previous.current.design_identity != identity:
        raise DashboardError("revision_conflict")
    if batch.expected_current_batch_id != current_id:
        raise DashboardError("revision_conflict")
    if batch.bindings_hash != binding_set_identity(bindings):
        raise DashboardError("binding_conflict")
    if not batch.batch_id.strip() or batch.batch_id == current_id:
        raise DashboardError("invalid_batch")
    configured = {proposal.slot_id: proposal for proposal in bindings}
    expected = {slot.slot_id: slot for slot in snapshot.slots}
    received = {outcome.slot_id: outcome for outcome in batch.outcomes}
    failures: list[SlotFailure] = []
    if len(configured) != len(bindings) or len(received) != len(batch.outcomes):
        failures.append(SlotFailure("", "duplicate_slot"))
    if not configured:
        failures.append(SlotFailure("", "missing_binding"))
    for slot in snapshot.slots:
        if slot.required and slot.slot_id not in configured:
            failures.append(SlotFailure(slot.slot_id, "missing_binding"))
    for slot_id in sorted(configured.keys() - expected.keys()):
        failures.append(SlotFailure(slot_id, "unknown_slot"))
    for slot_id in sorted(received.keys() - configured.keys()):
        failures.append(SlotFailure(slot_id, "unexpected_slot"))
    completed: list[SlotData] = []
    for slot_id, proposal in configured.items():
        outcome = received.get(slot_id)
        if outcome is None:
            failures.append(SlotFailure(slot_id, "refresh_incomplete"))
        elif isinstance(outcome, SlotFailure):
            failures.append(outcome)
        else:
            try:
                completed.append(validate_slot_result(snapshot, proposal, outcome.result))
            except DashboardError as error:
                failures.append(SlotFailure(slot_id, error.code))
    if failures:
        return RefreshState(identity, "failed", previous.current, batch.batch_id, tuple(failures))
    committed = CommittedBatch(batch.batch_id, identity, batch.bindings_hash, tuple(completed))
    return RefreshState(identity, "ready", committed, batch.batch_id)
