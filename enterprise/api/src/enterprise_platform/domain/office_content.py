"""Lossless chart/table input for Office rendering, independent of layout and storage.

Render adapters must retain missing values and consume every table page. These
contracts do not make legacy renderer coercion or truncation acceptable.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from typing_extensions import TypedDict

Label = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class DecimalCellJSON(TypedDict):
    decimal: str


def _read_decimal_cell(value: object, info: ValidationInfo) -> object:
    if isinstance(value, dict) and set(value) == {"decimal"} and isinstance(value["decimal"], str):
        try:
            return Decimal(value["decimal"])
        except InvalidOperation:
            raise ValueError("Invalid decimal table cell") from None
    if info.mode == "json":
        raise ValueError("Decimal table cells require a tagged exact string")
    return value


def _write_decimal_cell(value: Decimal) -> DecimalCellJSON:
    return {"decimal": str(value)}


# A JSON string cannot distinguish a numeric Decimal from an intentional text cell.
ExactTableDecimal = Annotated[
    Decimal,
    BeforeValidator(_read_decimal_cell, json_schema_input_type=DecimalCellJSON),
    PlainSerializer(_write_decimal_cell, return_type=DecimalCellJSON, when_used="json"),
]
type TableCell = str | int | ExactTableDecimal | None
type TableRow = tuple[TableCell, ...]


class OfficeContent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ChartSeries(OfficeContent):
    name: Label
    values: tuple[Decimal | None, ...] = Field(min_length=1, max_length=10000)

    @field_validator(
        "values",
        mode="before",
        json_schema_input_type=Annotated[list[str | None], Field(min_length=1, max_length=10000)],
    )
    @classmethod
    def exact_json_values(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and isinstance(value, list):
            if any(item is not None and not isinstance(item, str) for item in value):
                raise ValueError("Chart JSON values must be exact decimal strings or null")
            try:
                return tuple(Decimal(item) if item is not None else None for item in value)
            except InvalidOperation:
                raise ValueError("Invalid decimal chart value") from None
        return value

    @field_validator("values")
    @classmethod
    def finite_values(cls, values: tuple[Decimal | None, ...]) -> tuple[Decimal | None, ...]:
        if any(value is not None and not value.is_finite() for value in values):
            raise ValueError("Chart values must be finite or missing")
        return values


class ChartData(OfficeContent):
    categories: tuple[Label, ...] = Field(min_length=1, max_length=10000)
    series: tuple[ChartSeries, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def aligned_series(self) -> Self:
        if any(len(series.values) != len(self.categories) for series in self.series):
            raise ValueError("Every series must match the category count")
        return self


class TableData(OfficeContent):
    table_id: Label
    headers: tuple[Label, ...] = Field(min_length=1, max_length=64)
    rows: tuple[TableRow, ...] = Field(max_length=100000)

    @model_validator(mode="after")
    def rectangular_finite_rows(self) -> Self:
        for row in self.rows:
            if len(row) != len(self.headers):
                raise ValueError("Every table row must match the header count")
            if any(isinstance(cell, Decimal) and not cell.is_finite() for cell in row):
                raise ValueError("Table numbers must be finite or missing")
        return self


class TablePage(TableData):
    row_start: int = Field(ge=0)


def paginate_table(table: TableData, *, data_rows_per_page: int) -> tuple[TablePage, ...]:
    """Repeat headers; row offsets identify ranges within the unchanged source table.

    Capacity excludes the header. Pagination never limits the total exported rows
    and does not claim a text-height/visual fit without a renderer measurement.
    """
    if type(data_rows_per_page) is not int or not 1 <= data_rows_per_page <= 1000:
        raise ValueError("Table page capacity must be an integer from 1 through 1000")
    return tuple(
        TablePage(
            table_id=table.table_id,
            headers=table.headers,
            rows=table.rows[start : start + data_rows_per_page],
            row_start=start,
        )
        for start in range(0, max(1, len(table.rows)), data_rows_per_page)
    )
