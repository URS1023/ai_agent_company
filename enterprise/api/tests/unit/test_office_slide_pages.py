"""Content conservation before rendering original template layouts."""

from decimal import Decimal
from uuid import UUID

import pytest

from enterprise_platform.domain.office_content import ChartData, ChartSeries, TableData, TablePage
from enterprise_platform.domain.office_revision import OfficeText, OfficeUnit
from enterprise_platform.domain.office_slide_pages import paginate_slide


def test_table_overflow_retains_following_note_and_stable_source_identity() -> None:
    table = TableData(table_id="quality", headers=("Reading",), rows=tuple((Decimal(f"{i}.00"),) for i in range(17)))
    note = OfficeText(text="IMPORTANT FINAL QUALITY NOTE")
    unit = OfficeUnit(unit_id=UUID(int=1), kind="slide", content=(table, note))
    before = unit.model_dump_json()

    pages = paginate_slide(unit, data_rows_per_page=8, items_per_page=8)

    assert len(pages) == 4
    assert all(page.unit_id == unit.unit_id for page in pages)
    assert [page.page_index for page in pages] == [0, 1, 2, 3]
    tables = [item for page in pages for item in page.content if isinstance(item, TablePage)]
    assert tuple(row for item in tables for row in item.rows) == table.rows
    assert [item.row_start for item in tables] == [0, 8, 16]
    assert all(item.headers == table.headers for item in tables)
    assert pages[-1].content == (note,)
    assert [page.source_indices for page in pages] == [(0,), (0,), (0,), (1,)]
    assert unit.model_dump_json() == before


def test_mixed_content_retains_order_and_empty_tables() -> None:
    text = OfficeText(text="Leading text")
    chart = ChartData(categories=("A",), series=(ChartSeries(name="Exact", values=(Decimal("1.2300"),)),))
    table = TableData(table_id="empty", headers=("Header",), rows=())
    unit = OfficeUnit(unit_id=UUID(int=2), kind="slide", content=(text, table, chart, table, text))

    pages = paginate_slide(unit, data_rows_per_page=8, items_per_page=2)

    assert tuple(index for page in pages for index in page.source_indices) == (0, 1, 2, 3, 4)
    assert tuple(item for page in pages for item in page.content if not isinstance(item, TablePage)) == (
        text,
        chart,
        text,
    )
    assert sum(isinstance(item, TablePage) for page in pages for item in page.content) == 2


def test_non_table_items_are_packed_without_clipping() -> None:
    content = tuple(OfficeText(text=str(index)) for index in range(17))
    unit = OfficeUnit(unit_id=UUID(int=3), kind="slide", content=content)
    pages = paginate_slide(unit, data_rows_per_page=8, items_per_page=8)
    assert [len(page.content) for page in pages] == [8, 8, 1]
    assert tuple(item for page in pages for item in page.content) == content


@pytest.mark.parametrize("limit", [0, -1, True, 1001])
@pytest.mark.parametrize("field", ["rows", "items"])
def test_invalid_capacity_fails_even_for_text_only_slides(limit: int, field: str) -> None:
    unit = OfficeUnit(unit_id=UUID(int=4), kind="slide", content=(OfficeText(text="Text"),))
    with pytest.raises(ValueError):
        paginate_slide(
            unit, data_rows_per_page=limit if field == "rows" else 8, items_per_page=limit if field == "items" else 8
        )


def test_document_unit_is_not_silently_converted_to_a_slide() -> None:
    unit = OfficeUnit(unit_id=UUID(int=5), kind="paragraph", content=(OfficeText(text="Text"),))
    with pytest.raises(ValueError, match="slide"):
        paginate_slide(unit, data_rows_per_page=8, items_per_page=8)
