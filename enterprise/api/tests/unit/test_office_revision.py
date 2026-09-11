from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_platform.domain.office_content import ChartData, ChartSeries, TableData
from enterprise_platform.domain.office_revision import (
    OfficeRevision,
    OfficeRevisionError,
    OfficeText,
    OfficeUnit,
    UnitReplacement,
    replace_units,
)

FILE = UUID(int=1)
FIRST = UUID(int=2)
SECOND = UUID(int=3)


def revision() -> OfficeRevision:
    return OfficeRevision(
        file_id=FILE,
        revision=7,
        kind="presentation",
        units=(
            OfficeUnit(unit_id=SECOND, kind="slide", content=(OfficeText(text="Second, moved first"),)),
            OfficeUnit(unit_id=FIRST, kind="slide", content=(OfficeText(text="First, moved second"),)),
        ),
    )


def test_edit_uses_stable_id_not_current_position_and_preserves_original() -> None:
    original = revision()
    change = UnitReplacement(unit_id=FIRST, content=(OfficeText(text="Revised"),))
    result = replace_units(original, file_id=FILE, expected_revision=7, replacements=(change,))
    assert result.revision == 8 and result.file_id == FILE
    assert result.units[0] == original.units[0]
    assert result.units[1].unit_id == FIRST
    assert result.units[1].content == change.content
    assert original.revision == 7
    assert original.units[1].content == (OfficeText(text="First, moved second"),)
    assert OfficeRevision.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("file_id", "expected", "code"),
    [(UUID(int=9), 7, "file_mismatch"), (FILE, 6, "revision_conflict"), (FILE, 8, "revision_conflict")],
)
def test_rejects_wrong_file_or_stale_revision(file_id: UUID, expected: int, code: str) -> None:
    with pytest.raises(OfficeRevisionError) as error:
        replace_units(
            revision(),
            file_id=file_id,
            expected_revision=expected,
            replacements=(UnitReplacement(unit_id=FIRST, content=(OfficeText(text="Changed"),)),),
        )
    assert error.value.code == code


def test_unknown_or_duplicate_targets_reject_entire_edit() -> None:
    original = revision()
    first = UnitReplacement(unit_id=FIRST, content=(OfficeText(text="Changed"),))
    unknown = UnitReplacement(unit_id=UUID(int=10), content=(OfficeText(text="Unknown"),))
    for edits, code in [((first, unknown), "unknown_unit"), ((first, first), "duplicate_unit")]:
        with pytest.raises(OfficeRevisionError) as error:
            replace_units(original, file_id=FILE, expected_revision=7, replacements=edits)
        assert error.value.code == code
        assert original == revision()


def test_duplicate_ids_and_wrong_document_unit_kinds_are_rejected() -> None:
    original = revision()
    with pytest.raises(ValidationError):
        OfficeRevision(file_id=FILE, revision=1, kind="presentation", units=(original.units[0],) * 2)
    with pytest.raises(ValidationError):
        OfficeRevision(file_id=FILE, revision=1, kind="document", units=original.units)


def test_document_paragraph_is_replaced_without_changing_neighbors() -> None:
    original = OfficeRevision(
        file_id=FILE,
        revision=1,
        kind="document",
        units=(OfficeUnit(unit_id=FIRST, kind="paragraph", content=(OfficeText(text="Original"),)),),
    )
    result = replace_units(
        original,
        file_id=FILE,
        expected_revision=1,
        replacements=(UnitReplacement(unit_id=FIRST, content=(OfficeText(text="New paragraph"),)),),
    )
    assert result.units[0].kind == "paragraph"
    assert result.units[0].unit_id == FIRST
    assert result.revision == 2


def test_empty_edit_and_boolean_revision_are_rejected() -> None:
    for expected, edits in [(7, ()), (True, (UnitReplacement(unit_id=FIRST, content=(OfficeText(text="x"),)),))]:
        with pytest.raises(OfficeRevisionError):
            replace_units(revision(), file_id=FILE, expected_revision=expected, replacements=edits)


def test_revision_json_retains_chart_and_table_value_types() -> None:
    chart = ChartData(
        categories=("Day",), series=(ChartSeries(name="Value", values=(Decimal("0.1234567890123456789"),)),)
    )
    table = TableData(table_id="quality", headers=("ID", "Value"), rows=(("0001", Decimal("0.1234567890123456789")),))
    original = OfficeRevision(
        file_id=FILE,
        revision=1,
        kind="presentation",
        units=(OfficeUnit(unit_id=FIRST, kind="slide", content=(OfficeText(text="Quality"), chart, table)),),
    )
    restored = OfficeRevision.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.units[0].content[1], ChartData)
    assert isinstance(restored.units[0].content[2], TableData)


def test_invalid_replacement_does_not_partially_apply_document_edit() -> None:
    original = OfficeRevision(
        file_id=FILE,
        revision=1,
        kind="document",
        units=tuple(
            OfficeUnit(unit_id=key, kind="paragraph", content=(OfficeText(text="Original"),)) for key in (FIRST, SECOND)
        ),
    )
    table = TableData(table_id="quality", headers=("Value",), rows=((1,),))
    with pytest.raises(ValidationError):
        replace_units(
            original,
            file_id=FILE,
            expected_revision=1,
            replacements=(
                UnitReplacement(unit_id=FIRST, content=(OfficeText(text="Changed"),)),
                UnitReplacement(unit_id=SECOND, content=(table,)),
            ),
        )
    assert all(unit.content == (OfficeText(text="Original"),) for unit in original.units)


def test_replacement_cannot_change_unit_kind_or_file_metadata() -> None:
    with pytest.raises(ValidationError):
        UnitReplacement.model_validate({"unit_id": FIRST, "content": (OfficeText(text="x"),), "kind": "table"})
    original = revision()
    with pytest.raises(ValidationError):
        original.revision = 8
    with pytest.raises(ValidationError):
        original.units[0].unit_id = FIRST


def test_document_table_unit_preserves_identity_when_replaced() -> None:
    table = TableData(table_id="quality", headers=("Value",), rows=((1,),))
    updated = TableData(table_id="quality", headers=("Value",), rows=((2,),))
    original = OfficeRevision(
        file_id=FILE,
        revision=1,
        kind="document",
        units=(OfficeUnit(unit_id=FIRST, kind="table", content=(table,)),),
    )
    result = replace_units(
        original, file_id=FILE, expected_revision=1, replacements=(UnitReplacement(unit_id=FIRST, content=(updated,)),)
    )
    assert result.units[0].unit_id == FIRST
    assert result.units[0].content == (updated,)


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_revision_values_are_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        OfficeRevision(file_id=FILE, revision=value, kind="presentation", units=revision().units)
