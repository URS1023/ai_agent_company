import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from enterprise_platform.domain.office_content import ChartData, ChartSeries, TableData, paginate_table


def test_chart_preserves_missing_zero_and_decimal_precision() -> None:
    values = (Decimal("0"), None, Decimal("12.00000000000000001"))
    chart = ChartData(categories=("Mon", "Tue", "Wed"), series=(ChartSeries(name="Count", values=values),))
    assert chart.series[0].values == values
    assert chart.series[0].values[1] is None
    assert ChartData.model_validate_json(chart.model_dump_json()) == chart


@pytest.mark.parametrize("values", [(Decimal("1"),), (Decimal("1"), Decimal("2"), Decimal("3"))])
def test_chart_rejects_length_mismatch_instead_of_padding_or_truncating(values: tuple[Decimal, ...]) -> None:
    with pytest.raises(ValidationError, match="category count"):
        ChartData(categories=("Mon", "Tue"), series=(ChartSeries(name="Count", values=values),))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), True, "12", 0.1])
def test_chart_rejects_nonfinite_or_coerced_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ChartSeries.model_validate({"name": "Count", "values": (value,)})


@pytest.mark.parametrize("token", ["0.1", "0.123456789012345678901", "1", "true"])
def test_chart_json_rejects_numeric_tokens_before_precision_can_be_lost(token: str) -> None:
    with pytest.raises(ValidationError):
        ChartSeries.model_validate_json('{"name":"Count","values":[' + token + "]}")


def test_chart_json_accepts_exact_decimal_strings_and_missing_values() -> None:
    value = ChartSeries.model_validate_json('{"name":"Count","values":["0.123456789012345678901",null]}')
    assert value.values == (Decimal("0.123456789012345678901"), None)


def test_chart_validation_schema_only_advertises_exact_string_or_null_values() -> None:
    values = ChartSeries.model_json_schema(mode="validation")["properties"]["values"]
    assert values["type"] == "array"
    assert {item["type"] for item in values["items"]["anyOf"]} == {"string", "null"}
    assert values["minItems"] == 1
    assert values["maxItems"] == 10000


@pytest.mark.parametrize("token", ["0.1", "0.123456789012345678901", "true"])
def test_table_json_rejects_raw_floats_and_boolean_cells(token: str) -> None:
    payload = '{"table_id":"quality","headers":["Value"],"rows":[[' + token + "]]}"
    with pytest.raises(ValidationError):
        TableData.model_validate_json(payload)


def test_table_pagination_preserves_every_row_and_repeats_headers() -> None:
    rows = tuple((f"{i:04d}", Decimal(i), None) for i in range(23))
    table = TableData(table_id="quality", headers=("Device", "Count", "Missing"), rows=rows)
    pages = paginate_table(table, data_rows_per_page=8)
    assert [len(page.rows) for page in pages] == [8, 8, 7]
    assert [page.row_start for page in pages] == [0, 8, 16]
    assert tuple(row for page in pages for row in page.rows) == rows
    assert all(page.headers == table.headers and page.table_id == "quality" for page in pages)
    assert pages[0].rows[1][0] == "0001"
    assert table.rows == rows


def test_empty_table_retains_headers_without_inventing_data() -> None:
    table = TableData(table_id="quality", headers=("Device",), rows=())
    pages = paginate_table(table, data_rows_per_page=8)
    assert len(pages) == 1 and pages[0].rows == () and pages[0].headers == ("Device",)


def test_table_json_roundtrip_distinguishes_decimal_string_integer_and_missing() -> None:
    rows = (("0001", Decimal("12.00000000000000001"), "12.00000000000000001", 0, None),)
    table = TableData(table_id="quality", headers=("ID", "Decimal", "Text", "Count", "Missing"), rows=rows)
    restored = TableData.model_validate_json(table.model_dump_json())
    assert restored == table
    assert isinstance(restored.rows[0][1], Decimal)
    assert isinstance(restored.rows[0][2], str)
    for page in paginate_table(table, data_rows_per_page=8):
        assert type(page).model_validate_json(page.model_dump_json()) == page


@pytest.mark.parametrize(
    "cell",
    [
        {"decimal": "invalid"},
        {"decimal": ""},
        {"decimal": "NaN"},
        {"decimal": "Infinity"},
        {"decimal": 12},
        {"decimal": "1", "extra": "x"},
    ],
)
def test_table_rejects_invalid_decimal_wire_values(cell: object) -> None:
    payload = json.dumps({"table_id": "quality", "headers": ["Value"], "rows": [[cell]]})
    with pytest.raises(ValidationError):
        TableData.model_validate_json(payload)


@pytest.mark.parametrize("count", [1, 8, 9, 16, 17, 64])
def test_table_pagination_boundaries_never_add_or_drop_rows(count: int) -> None:
    rows = tuple((f"{i:04d}",) for i in range(count))
    table = TableData(table_id="quality", headers=("Device",), rows=rows)
    pages = paginate_table(table, data_rows_per_page=8)
    assert len(pages) == (count + 7) // 8
    assert tuple(row for page in pages for row in page.rows) == rows
    assert TableData.model_validate_json(table.model_dump_json()) == table


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), True, 0.1])
def test_table_rejects_nonfinite_and_implicitly_coerced_cells(value: object) -> None:
    with pytest.raises(ValidationError):
        TableData.model_validate({"table_id": "quality", "headers": ("Count",), "rows": ((value,),)})


@pytest.mark.parametrize("limit", [0, -1, 1001, True])
def test_invalid_page_capacity_is_rejected(limit: int) -> None:
    table = TableData(table_id="quality", headers=("Device",), rows=())
    with pytest.raises(ValueError):
        paginate_table(table, data_rows_per_page=limit)


@pytest.mark.parametrize("row", [("0001",), ("0001", 0, "extra")])
def test_table_rejects_ragged_rows(row: tuple[str | int, ...]) -> None:
    with pytest.raises(ValidationError, match="header count"):
        TableData(table_id="quality", headers=("Device", "Count"), rows=(row,))


def test_office_contract_rejects_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError):
        ChartSeries.model_validate({"name": "Count", "values": (None,), "style": {}})
    series = ChartSeries(name="Count", values=(None,))
    with pytest.raises(ValidationError):
        series.name = "Changed"
