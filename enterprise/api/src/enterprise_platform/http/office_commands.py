"""Convert lossless browser values to existing domain edit commands, never bindings."""

from decimal import Decimal
from uuid import UUID

from pydantic import Field, StrictStr

from enterprise_platform.application.contracts import Contract
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.application.office_edits import OfficeEditCommand
from enterprise_platform.domain.office_content import ChartData, ChartSeries, TableCell, TableData
from enterprise_platform.domain.office_revision import OfficeText, UnitReplacement
from enterprise_platform.http.office_views import OfficeDecimalView, OfficeIntegerView


class OfficeTextInput(Contract):
    text: StrictStr = Field(max_length=100000)


class OfficeSeriesInput(Contract):
    name: StrictStr = Field(min_length=1, max_length=256)
    values: tuple[StrictStr | None, ...] = Field(min_length=1, max_length=10000)


class OfficeChartInput(Contract):
    categories: tuple[StrictStr, ...] = Field(min_length=1, max_length=10000)
    series: tuple[OfficeSeriesInput, ...] = Field(min_length=1, max_length=64)


class OfficeTableInput(Contract):
    table_id: StrictStr = Field(min_length=1, max_length=256)
    headers: tuple[StrictStr, ...] = Field(min_length=1, max_length=64)
    rows: tuple[tuple[StrictStr | OfficeIntegerView | OfficeDecimalView | None, ...], ...] = Field(max_length=100000)


def _cell(value: str | OfficeIntegerView | OfficeDecimalView | None) -> TableCell:
    if isinstance(value, OfficeIntegerView):
        return int(value.integer)
    if isinstance(value, OfficeDecimalView):
        return Decimal(value.decimal)
    return value


def _content(value: OfficeTextInput | OfficeChartInput | OfficeTableInput) -> OfficeText | ChartData | TableData:
    if isinstance(value, OfficeTextInput):
        return OfficeText(text=value.text)
    if isinstance(value, OfficeTableInput):
        return TableData(
            table_id=value.table_id,
            headers=value.headers,
            rows=tuple(tuple(_cell(cell) for cell in row) for row in value.rows),
        )
    return ChartData(
        categories=value.categories,
        series=tuple(
            ChartSeries(name=series.name, values=tuple(Decimal(v) if v is not None else None for v in series.values))
            for series in value.series
        ),
    )


class OfficeReplacementInput(Contract):
    unit_id: UUID
    content: tuple[OfficeTextInput | OfficeChartInput | OfficeTableInput, ...] = Field(min_length=1, max_length=1000)


class OfficeEditRequest(Contract):
    request_id: UUID
    expected_revision: StrictStr = Field(pattern=r"^[1-9][0-9]{0,18}$")
    replacements: tuple[OfficeReplacementInput, ...] = Field(min_length=1, max_length=10000)

    def command(self) -> OfficeEditCommand:
        try:
            return OfficeEditCommand(
                request_id=self.request_id,
                expected_revision=int(self.expected_revision),
                replacements=tuple(
                    UnitReplacement(unit_id=item.unit_id, content=tuple(_content(value) for value in item.content))
                    for item in self.replacements
                ),
            )
        except (ValueError, ArithmeticError):
            raise InvalidInput("office_unit_content_invalid") from None
