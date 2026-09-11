"""Immutable Office editing units identified independently of their current position.

These checks prepare a candidate revision, not a persisted transaction. The caller
must authorize the file and atomically compare/store its expected revision. Source
snapshots and template bindings belong to that same transaction, never a fresh
query performed halfway through rendering. This module performs no I/O.
"""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from enterprise_platform.domain.office_content import ChartData, OfficeContent, TableData

type RevisionErrorCode = Literal["file_mismatch", "revision_conflict", "empty_edit", "duplicate_unit", "unknown_unit"]


class OfficeRevisionError(ValueError):
    def __init__(self, code: RevisionErrorCode) -> None:
        self.code = code
        super().__init__(code)


class OfficeText(OfficeContent):
    text: str = Field(max_length=100000)


type UnitContent = tuple[OfficeText | ChartData | TableData, ...]


class OfficeUnit(OfficeContent):
    unit_id: UUID
    kind: Literal["slide", "paragraph", "table"]
    content: UnitContent = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def compatible_content(self) -> Self:
        if self.kind == "paragraph" and any(not isinstance(item, OfficeText) for item in self.content):
            raise ValueError("Paragraphs require text content")
        if self.kind == "table" and (len(self.content) != 1 or not isinstance(self.content[0], TableData)):
            raise ValueError("Table units require exactly one table")
        return self


class OfficeRevision(OfficeContent):
    file_id: UUID
    revision: int = Field(ge=1)
    kind: Literal["presentation", "document"]
    units: tuple[OfficeUnit, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_compatible_units(self) -> Self:
        if len({unit.unit_id for unit in self.units}) != len(self.units):
            raise ValueError("Unit IDs must be unique within the file")
        if any((unit.kind == "slide") != (self.kind == "presentation") for unit in self.units):
            raise ValueError("Unit kind does not match the file kind")
        return self


class UnitReplacement(OfficeContent):
    unit_id: UUID
    content: UnitContent = Field(min_length=1, max_length=1000)


def replace_units(
    current: OfficeRevision,
    *,
    file_id: UUID,
    expected_revision: int,
    replacements: tuple[UnitReplacement, ...],
) -> OfficeRevision:
    """Replace selected content; preserve ordering, IDs, kinds and all other units."""
    if file_id != current.file_id:
        raise OfficeRevisionError("file_mismatch")
    if type(expected_revision) is not int or expected_revision != current.revision:
        raise OfficeRevisionError("revision_conflict")
    if not replacements:
        raise OfficeRevisionError("empty_edit")
    changes = {change.unit_id: change.content for change in replacements}
    if len(changes) != len(replacements):
        raise OfficeRevisionError("duplicate_unit")
    if changes.keys() - {unit.unit_id for unit in current.units}:
        raise OfficeRevisionError("unknown_unit")
    units = tuple(
        OfficeUnit(unit_id=unit.unit_id, kind=unit.kind, content=changes[unit.unit_id])
        if unit.unit_id in changes
        else unit
        for unit in current.units
    )
    return OfficeRevision(file_id=current.file_id, revision=current.revision + 1, kind=current.kind, units=units)
