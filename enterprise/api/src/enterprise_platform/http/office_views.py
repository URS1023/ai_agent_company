"""Read projections keep exact numeric types across JavaScript JSON parsing."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from enterprise_platform.application.office_edits import OfficeFileRecord
from enterprise_platform.domain.office_content import ChartData, OfficeContent, TableCell, TableData
from enterprise_platform.domain.office_revision import OfficeText


class OfficeIntegerView(OfficeContent):
    integer: str = Field(pattern=r"^-?(0|[1-9][0-9]*)$")


class OfficeDecimalView(OfficeContent):
    decimal: str


type OfficeCellView = str | OfficeIntegerView | OfficeDecimalView | None


def _cell(value: TableCell) -> OfficeCellView:
    if isinstance(value, Decimal):
        return OfficeDecimalView(decimal=str(value))
    if isinstance(value, int):
        return OfficeIntegerView(integer=str(value))
    return value


class OfficeTableView(OfficeContent):
    table_id: str
    headers: tuple[str, ...]
    rows: tuple[tuple[OfficeCellView, ...], ...]


class OfficeUnitView(OfficeContent):
    unit_id: UUID
    kind: Literal["slide", "paragraph", "table"]
    content: tuple[OfficeText | ChartData | OfficeTableView, ...]


class OfficeFileView(OfficeContent):
    file_id: UUID
    revision: str = Field(pattern=r"^[1-9][0-9]*$")
    kind: Literal["presentation", "document"]
    template_id: str
    template_revision: str = Field(pattern=r"^[1-9][0-9]*$")
    source_snapshot_ids: tuple[str, ...]
    units: tuple[OfficeUnitView, ...]

    @classmethod
    def from_record(cls, record: OfficeFileRecord) -> "OfficeFileView":
        return cls(
            file_id=record.content.file_id,
            revision=str(record.content.revision),
            kind=record.content.kind,
            template_id=record.template_id,
            template_revision=str(record.template_revision),
            source_snapshot_ids=record.source_snapshot_ids,
            units=tuple(
                OfficeUnitView(
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    content=tuple(
                        OfficeTableView(
                            table_id=item.table_id,
                            headers=item.headers,
                            rows=tuple(tuple(_cell(cell) for cell in row) for row in item.rows),
                        )
                        if isinstance(item, TableData)
                        else item
                        for item in unit.content
                    ),
                )
                for unit in record.content.units
            ),
        )
