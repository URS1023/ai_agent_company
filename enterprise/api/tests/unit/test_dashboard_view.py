import json
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError
from test_dashboard_storage import stored_record

from enterprise_platform.application.dashboard_contracts import (
    DashboardCell,
    DashboardRefreshCommand,
    as_dashboard_view,
)
from enterprise_platform.domain import dashboard as d


def test_public_view_preserves_numeric_precision_and_omits_query_configuration() -> None:
    record = stored_record()
    view = as_dashboard_view(record)
    document = json.loads(view.model_dump_json())
    values = document["current"]["slots"][0]["rows"][0]
    assert values["decimal"] == {"kind": "decimal", "value": "98.2500"}
    assert values["integer"] == {"kind": "integer", "value": "9007199254740993"}
    assert values["string"] == {"kind": "string", "value": "98.2500"}
    assert values["boolean"] == {"kind": "boolean", "value": True}
    assert values["null"] == {"kind": "null", "value": None}
    assert document["revision"] == record.revision
    assert document["design_identity"] == record.design.identity
    assert document["template_id"] == record.design.template_id
    for internal in ("query_ref", "bindings", "parameters", "visual_json", "workspace_id", "actor_ids"):
        assert internal not in view.model_dump_json()


def test_failed_refresh_marks_last_good_data_as_failed_not_fresh_success() -> None:
    record = stored_record()
    record = replace(
        record,
        state=replace(
            record.state,
            status="failed",
            last_attempt_id="batch-2",
            failures=(d.SlotFailure("metric", "source_query_failed"),),
        ),
    )
    document = as_dashboard_view(record).model_dump(mode="json")
    assert document["status"] == "failed"
    assert document["last_attempt_id"] == "batch-2"
    assert document["current"]["batch_id"] == "batch-1"
    assert document["failures"] == [{"slot_id": "metric", "code": "source_query_failed"}]


def test_no_data_is_distinct_from_successful_zero_rows() -> None:
    record = stored_record()
    empty = replace(record, state=d.RefreshState(record.design.identity))
    assert as_dashboard_view(empty).current is None
    slot = replace(record.state.current.slots[0], rows=())
    ready = replace(record, state=replace(record.state, current=replace(record.state.current, slots=(slot,))))
    assert as_dashboard_view(ready).current.slots[0].rows == ()


def test_small_decimal_exponent_and_large_decimal_are_preserved_as_text() -> None:
    record = stored_record()
    slot = record.state.current.slots[0]
    for value in (Decimal("1E-1000"), Decimal("12345678901234567890.1234567890"), Decimal("-0.00")):
        changed = replace(
            slot, rows=(tuple((name, value if name == "decimal" else cell) for name, cell in slot.rows[0]),)
        )
        current = replace(record.state.current, slots=(changed,))
        view = as_dashboard_view(replace(record, state=replace(record.state, current=current)))
        assert view.current.slots[0].rows[0]["decimal"].value == str(value)


@pytest.mark.parametrize(
    "document",
    [
        '{"kind":"integer","value":9007199254740993}',
        '{"kind":"decimal","value":"NaN"}',
        '{"kind":"boolean","value":"true"}',
        '{"kind":"null","value":0}',
        '{"kind":"string","value":"text","style":"red"}',
    ],
)
def test_public_scalar_contract_rejects_ambiguous_or_visual_payloads(document: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DashboardCell).validate_json(document)


@pytest.mark.parametrize("revision", [True, "1", 0, 1.5])
def test_refresh_command_accepts_only_positive_integer_revision(revision: object) -> None:
    with pytest.raises(ValidationError):
        DashboardRefreshCommand.model_validate({"expected_revision": revision})
