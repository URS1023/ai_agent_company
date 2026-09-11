from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_dashboard_refresh_service import principal, record

from enterprise_platform.application.dashboard_sql_trial import SqlTrialCommand
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult, as_sql_trial_result
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef


def capture(values=()):
    columns = tuple(f"field_{index}" for index in range(len(values))) if values else ("count",)
    rows = (values,) if values else ()
    return FrozenRows(
        SourceRef(workspace_id="workspace-1", source_id="source-1", revision="v1"),
        "draft-1",
        "a" * 64,
        "b" * 64,
        datetime(2026, 9, 11, tzinfo=UTC),
        columns,
        rows,
        (PageEvidence(1, len(rows), "c" * 64),),
    )


def result(data):
    return as_sql_trial_result(
        principal(),
        "dashboard-1",
        "draft-1",
        SqlTrialCommand(expected_revision=1, expected_design_identity=record().design.identity, slot_id="a"),
        data,
    )


def test_result_preserves_exact_numbers_and_distinct_scalar_types():
    view = result(capture((2**64, Decimal("0.0000000000000000001"), False, None, "", 0)))
    row = view.model_dump(mode="json")["rows"][0]
    assert row == [
        {"kind": "integer", "value": str(2**64)},
        {"kind": "decimal", "value": "1E-19"},
        {"kind": "boolean", "value": False},
        {"kind": "null", "value": None},
        {"kind": "string", "value": ""},
        {"kind": "integer", "value": "0"},
    ]
    assert SqlTrialResult.model_validate_json(view.model_dump_json()) == view


def test_dates_preserve_naive_datetime_without_inventing_a_timezone():
    view = result(capture((date(2026, 9, 11), datetime(2026, 9, 11, 8, 30), datetime(2026, 9, 11, 8, 30, tzinfo=UTC))))
    row = view.model_dump(mode="json")["rows"][0]
    assert row[0] == {"kind": "date", "value": "2026-09-11"}
    assert row[1] == {"kind": "datetime", "value": "2026-09-11T08:30:00"}
    assert row[2] == {"kind": "datetime", "value": "2026-09-11T08:30:00+00:00"}


def test_empty_result_is_not_a_zero_row_or_published_dashboard():
    view = result(capture())
    assert view.rows == ()
    assert view.row_count == 0
    assert view.columns == ("count",)
    assert view.status == "trial_only"
    assert view.semantic_status == "not_reviewed"
    assert view.draft_hash == "a" * 64
    assert view.read_fingerprint == "b" * 64
    assert view.workspace_id == "workspace-1"


@pytest.mark.parametrize(
    "field,value",
    [("row_count", 2), ("columns", ["count", "count"]), ("status", "approved"), ("semantic_status", "verified")],
)
def test_inconsistent_or_approved_receipts_are_rejected(field, value):
    payload = result(capture()).model_dump(mode="json")
    with pytest.raises(ValidationError):
        SqlTrialResult.model_validate({**payload, field: value})


def test_cross_workspace_capture_is_not_serialized():
    from dataclasses import replace

    data = capture()
    with pytest.raises(AccessDenied):
        result(replace(data, source=data.source.model_copy(update={"workspace_id": "other"})))


def test_oversized_wire_document_is_rejected_not_silently_truncated():
    with pytest.raises(DependencyUnavailable):
        result(capture(("x" * (512 * 1024),)))
