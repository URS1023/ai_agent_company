"""Lossless physical-page planning for stable Office slide editing units.

The original renderer discarded sibling content when a table overflowed. Flush
siblings in order and retain the source index of every item and table segment.
Capacities are explicit template inputs, not a promise of measured visual fit.
This module does not choose layouts, truncate text, render or mutate a revision.
"""

from dataclasses import dataclass
from uuid import UUID

from .office_content import ChartData, TableData, TablePage, paginate_table
from .office_revision import OfficeText, OfficeUnit

type PageContent = OfficeText | ChartData | TablePage


@dataclass(frozen=True, slots=True)
class SlidePage:
    unit_id: UUID
    page_index: int
    source_indices: tuple[int, ...]
    content: tuple[PageContent, ...]


def paginate_slide(unit: OfficeUnit, *, data_rows_per_page: int, items_per_page: int) -> tuple[SlidePage, ...]:
    if unit.kind != "slide":
        raise ValueError("A slide unit is required")
    for capacity in (data_rows_per_page, items_per_page):
        if type(capacity) is not int or not 1 <= capacity <= 1000:
            raise ValueError("Page capacity must be an integer from 1 through 1000")
    pages: list[SlidePage] = []
    pending: list[PageContent] = []
    indices: list[int] = []

    def flush() -> None:
        if pending:
            pages.append(SlidePage(unit.unit_id, len(pages), tuple(indices), tuple(pending)))
            pending.clear()
            indices.clear()

    for index, item in enumerate(unit.content):
        if isinstance(item, TableData):
            flush()
            for table_page in paginate_table(item, data_rows_per_page=data_rows_per_page):
                pages.append(SlidePage(unit.unit_id, len(pages), (index,), (table_page,)))
        else:
            pending.append(item)
            indices.append(index)
            if len(pending) == items_per_page:
                flush()
    flush()
    return tuple(pages)
