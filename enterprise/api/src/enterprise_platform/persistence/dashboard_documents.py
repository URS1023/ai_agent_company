"""Versioned storage documents with explicitly tagged scalars, never browser JSON.

Decimal scale, large integers and numeric-looking strings must survive database
text round trips without a union decoder guessing the wrong scalar type.
"""

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from enterprise_platform.application.dashboard_refresh_service import DashboardRecord
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.domain import dashboard as d


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Cell(_Document):
    kind: Literal["string", "integer", "decimal", "boolean", "null"]
    value: str | bool | None

    @classmethod
    def encode(cls, value: d.CellValue) -> "_Cell":
        if value is None:
            return cls(kind="null", value=None)
        if type(value) is bool:
            return cls(kind="boolean", value=value)
        if type(value) is int:
            return cls(kind="integer", value=str(value))
        if isinstance(value, Decimal) and value.is_finite():
            return cls(kind="decimal", value=str(value))
        if type(value) is str:
            return cls(kind="string", value=value)
        raise ValueError("invalid scalar")

    def decode(self) -> d.CellValue:
        if self.kind == "null" and self.value is None:
            return None
        if self.kind == "boolean" and type(self.value) is bool:
            return self.value
        if isinstance(self.value, str):
            if self.kind == "string":
                return self.value
            if self.kind == "integer" and re.fullmatch(r"-?(0|[1-9][0-9]*)", self.value):
                return int(self.value)
            if self.kind == "decimal":
                value = Decimal(self.value)
                if value.is_finite():
                    return value
        raise ValueError("invalid scalar")


class _Slot(_Document):
    slot_id: str
    query_ref: str
    binding_hash: str
    rows: tuple[tuple[tuple[str, _Cell], ...], ...]


class _Batch(_Document):
    batch_id: str
    design_identity: str
    bindings_hash: str
    slots: tuple[_Slot, ...]


class _State(_Document):
    design_identity: str
    status: Literal["empty", "ready", "failed"]
    current: _Batch | None
    last_attempt_id: str | None
    failures: tuple[d.SlotFailure, ...]


class _Record(_Document):
    schema_version: Literal[1] = 1
    workspace_id: str = Field(min_length=1)
    dashboard_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    design: d.DesignSnapshot
    bindings: tuple[d.ExecutionBinding, ...]
    state: _State
    name: str = Field(default="", max_length=200)
    creation_request_hash: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


def _validate(record: DashboardRecord) -> DashboardRecord:
    state = record.state
    if (
        state.design_identity != record.design.identity
        or state.current
        and state.current.design_identity != state.design_identity
        or state.status == "ready"
        and state.current is None
        or state.status == "empty"
        and state.current is not None
        or state.status == "ready"
        and state.failures
    ):
        raise ValueError("invalid state")
    if state.current:
        d.validate_committed_batch(record.design, state.current)
        if state.status == "ready":
            bindings = {binding.slot_id: binding for binding in record.bindings}
            if (
                state.current.batch_id != state.last_attempt_id
                or state.current.bindings_hash != d.binding_set_identity(record.bindings)
                or len(bindings) != len(record.bindings)
                or set(bindings) != {slot.slot_id for slot in state.current.slots}
            ):
                raise ValueError("invalid ready batch")
            for slot in state.current.slots:
                binding = bindings[slot.slot_id]
                if slot.binding_hash != binding.identity or slot.query_ref != binding.proposal.query_ref:
                    raise ValueError("invalid slot provenance")
    return record


def encode_dashboard(record: DashboardRecord) -> str:
    try:
        _validate(record)
        current = record.state.current
        batch = (
            None
            if current is None
            else _Batch(
                batch_id=current.batch_id,
                design_identity=current.design_identity,
                bindings_hash=current.bindings_hash,
                slots=tuple(
                    _Slot(
                        slot_id=slot.slot_id,
                        query_ref=slot.query_ref,
                        binding_hash=slot.binding_hash,
                        rows=tuple(tuple((name, _Cell.encode(value)) for name, value in row) for row in slot.rows),
                    )
                    for slot in current.slots
                ),
            )
        )
        return _Record(
            workspace_id=record.workspace_id,
            dashboard_id=record.dashboard_id,
            revision=record.revision,
            name=record.name,
            creation_request_hash=record.creation_request_hash,
            design=record.design,
            bindings=record.bindings,
            state=_State(
                design_identity=record.state.design_identity,
                status=record.state.status,
                current=batch,
                last_attempt_id=record.state.last_attempt_id,
                failures=record.state.failures,
            ),
        ).model_dump_json()
    except (ValueError, TypeError, ArithmeticError):
        raise PersistenceError("dashboard_document_invalid") from None


def decode_dashboard(document: str) -> DashboardRecord:
    try:
        stored = _Record.model_validate_json(document)
        current = stored.state.current
        batch = (
            None
            if current is None
            else d.CommittedBatch(
                current.batch_id,
                current.design_identity,
                current.bindings_hash,
                tuple(
                    d.SlotData(
                        slot.slot_id,
                        slot.query_ref,
                        slot.binding_hash,
                        tuple(tuple((name, cell.decode()) for name, cell in row) for row in slot.rows),
                    )
                    for slot in current.slots
                ),
            )
        )
        return _validate(
            DashboardRecord(
                stored.workspace_id,
                stored.dashboard_id,
                stored.revision,
                stored.design,
                stored.bindings,
                d.RefreshState(
                    stored.state.design_identity,
                    stored.state.status,
                    batch,
                    stored.state.last_attempt_id,
                    stored.state.failures,
                ),
                name=stored.name,
                creation_request_hash=stored.creation_request_hash,
            )
        )
    except (ValueError, TypeError, ArithmeticError):
        raise PersistenceError("dashboard_document_invalid") from None
