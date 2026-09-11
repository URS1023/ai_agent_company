import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.dashboard_query_capture import CapturedQueryContract, map_query_capture
from enterprise_platform.application.input_capture import RegisteredRead, RegisteredSourceReader
from enterprise_platform.domain.dashboard import (
    BindingProposal,
    ColumnContract,
    DashboardError,
    DesignSnapshot,
    ExecutionBinding,
    QueryColumn,
    SlotContract,
    validate_slot_result,
)
from enterprise_platform.domain.data_sources import FrozenRows, HttpRead, HttpSourceConfig, PageEvidence, SourceRef


def contract() -> CapturedQueryContract:
    return CapturedQueryContract(
        binding=ExecutionBinding.from_proposal(
            BindingProposal(slot_id="metric", query_ref="query-1", field_map={"name": "device", "value": "rate"}),
            query_revision="query-rev-1",
        ),
        source=SourceRef(workspace_id="workspace-1", source_id="source-1", revision="source-rev-1"),
        read_id="read-1",
        read_revision="read-rev-1",
        read_fingerprint="a" * 64,
        columns=(QueryColumn(name="device", kind="string"), QueryColumn(name="rate", kind="decimal", unit="%")),
    )


def captured() -> FrozenRows:
    expected = contract()
    return FrozenRows(
        source=expected.source,
        read_id=expected.read_id,
        read_revision=expected.read_revision,
        read_fingerprint=expected.read_fingerprint,
        captured_at=datetime.now(UTC),
        columns=("device", "rate"),
        rows=(("device-1", Decimal("98.2500")),),
        pages=(PageEvidence(number=1, row_count=1, response_digest="b" * 64),),
    )


def test_capture_maps_to_existing_dashboard_validation_without_float_conversion() -> None:
    expected = contract()
    result = map_query_capture(expected, captured())
    snapshot = DesignSnapshot(
        template_id="equipment",
        template_revision=1,
        design_revision=1,
        visual_json='{"widgets":[]}',
        renderer_build_id="lynx",
        component_schema_version="1",
        slots=(
            SlotContract(
                slot_id="metric",
                columns=(
                    ColumnContract(name="name", kind="string"),
                    ColumnContract(name="value", kind="decimal", unit="%"),
                ),
            ),
        ),
    )
    slot = validate_slot_result(snapshot, expected.binding, result)
    assert dict(slot.rows[0]) == {"name": "device-1", "value": Decimal("98.2500")}
    assert result.execution_binding.identity == expected.binding.identity
    assert result.truncated is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", SourceRef(workspace_id="other", source_id="source-1", revision="source-rev-1")),
        ("source", SourceRef(workspace_id="workspace-1", source_id="other", revision="source-rev-1")),
        ("source", SourceRef(workspace_id="workspace-1", source_id="source-1", revision="other")),
        ("read_id", "other"),
        ("read_revision", "other"),
        ("read_fingerprint", "c" * 64),
    ],
)
def test_capture_requires_exact_source_read_and_parameter_fingerprint(field: str, value: object) -> None:
    with pytest.raises(DashboardError, match="query_mismatch"):
        map_query_capture(contract(), replace(captured(), **{field: value}))


def test_empty_results_keep_declared_types_and_units() -> None:
    empty = replace(captured(), rows=(), pages=(PageEvidence(number=1, row_count=0, response_digest="b" * 64),))
    result = map_query_capture(contract(), empty)
    assert result.rows == ()
    assert result.columns == contract().columns


@pytest.mark.parametrize("value", [True, 98, "98.25"])
def test_source_type_drift_is_not_silently_coerced(value: object) -> None:
    rows = replace(captured(), rows=(("device-1", value),))
    with pytest.raises(DashboardError, match="invalid_value"):
        map_query_capture(contract(), rows)


def test_missing_or_unexpected_columns_are_rejected() -> None:
    with pytest.raises(DashboardError, match="missing_field"):
        map_query_capture(contract(), replace(captured(), columns=("device", "wrong")))


def test_output_rows_are_detached_from_capture() -> None:
    source = captured()
    result = map_query_capture(contract(), source)
    result.rows[0]["rate"] = Decimal("0")
    assert source.rows[0][1] == Decimal("98.2500")


def test_null_is_preserved_for_the_slot_nullability_contract() -> None:
    result = map_query_capture(contract(), replace(captured(), rows=(("device-1", None),)))
    assert result.rows[0]["rate"] is None


def test_column_order_is_resolved_by_name_not_position() -> None:
    reordered = replace(captured(), columns=("rate", "device"), rows=((Decimal("98.2500"), "device-1"),))
    assert map_query_capture(contract(), reordered).rows == map_query_capture(contract(), captured()).rows


@pytest.mark.parametrize(
    "field,value",
    [
        ("read_id", " "),
        ("read_revision", ""),
        ("read_fingerprint", "invalid"),
        ("columns", ()),
        ("columns", (QueryColumn(name="rate", kind="decimal"),) * 2),
    ],
)
def test_incomplete_or_ambiguous_query_contract_is_rejected(field: str, value: object) -> None:
    with pytest.raises(DashboardError, match="invalid_binding"):
        replace(contract(), **{field: value})


def test_registered_http_reader_output_maps_without_replacing_decimal_values() -> None:
    expected = contract()
    read = HttpRead(
        source=expected.source,
        read_id=expected.read_id,
        revision=expected.read_revision,
        method="GET",
        read_only=True,
        parameter_names=frozenset({"device"}),
        rows_path=("data",),
    )
    registration = RegisteredRead(
        connection=HttpSourceConfig(
            source=expected.source,
            url="https://source.example.test/quality",
            allowed_hosts=frozenset({"source.example.test"}),
        ),
        read=read,
        device_ids=frozenset({"device-1"}),
        device_parameter="device",
        device_column="device",
    )
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["device"])
        return httpx.Response(200, content=b'{"data":[{"device":"device-1","rate":98.2500}]}')

    expected = replace(expected, read_fingerprint=read_fingerprint(read, (("device", "device-1"),)))
    reader = RegisteredSourceReader(http_transport=httpx.MockTransport(respond))
    capture = asyncio.run(reader.read(registration, {"device": "device-1"}))
    result = map_query_capture(expected, capture)
    assert calls == ["device-1"]
    assert result.rows == ({"device": "device-1", "rate": Decimal("98.2500")},)
    assert result.rows[0]["rate"].as_tuple().exponent == -4
