"""Bridge complete source captures into the existing data-only dashboard domain.

The caller resolves this contract before executing the read, including the reader's
parameter fingerprint and server-owned column metadata. This bridge performs no
I/O or authorization and never infers decimal types/units from sampled rows.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from enterprise_platform.domain.dashboard import CellValue, DashboardError, ExecutionBinding, QueryColumn, QueryResult
from enterprise_platform.domain.data_sources import FrozenRows, SourceRef, SourceValue


@dataclass(frozen=True, slots=True)
class CapturedQueryContract:
    binding: ExecutionBinding
    source: SourceRef
    read_id: str
    read_revision: str
    read_fingerprint: str
    columns: tuple[QueryColumn, ...]

    def __post_init__(self) -> None:
        if (
            not self.read_id.strip()
            or not self.read_revision.strip()
            or not re.fullmatch(r"[a-f0-9]{64}", self.read_fingerprint)
            or not self.columns
            or len({column.name for column in self.columns}) != len(self.columns)
        ):
            raise DashboardError("invalid_binding", self.binding.slot_id)
        object.__setattr__(self, "columns", tuple(self.columns))


def _typed_cell(value: SourceValue, column: QueryColumn, slot_id: str) -> CellValue:
    if value is None:
        return None
    if column.kind == "string" and type(value) is str:
        return value
    if column.kind == "boolean" and type(value) is bool:
        return value
    if column.kind == "integer" and type(value) is int:
        return value
    if column.kind == "decimal" and isinstance(value, Decimal) and value.is_finite():
        return value
    raise DashboardError("invalid_value", slot_id)


def map_query_capture(contract: CapturedQueryContract, capture: FrozenRows) -> QueryResult:
    slot_id = contract.binding.slot_id
    if (
        capture.source != contract.source
        or capture.read_id != contract.read_id
        or capture.read_revision != contract.read_revision
        or capture.read_fingerprint != contract.read_fingerprint
    ):
        raise DashboardError("query_mismatch", slot_id)
    if set(capture.columns) != {column.name for column in contract.columns}:
        raise DashboardError("missing_field", slot_id)
    positions = {name: index for index, name in enumerate(capture.columns)}
    return QueryResult(
        execution_binding=contract.binding,
        columns=contract.columns,
        rows=tuple(
            {column.name: _typed_cell(row[positions[column.name]], column, slot_id) for column in contract.columns}
            for row in capture.rows
        ),
    )
