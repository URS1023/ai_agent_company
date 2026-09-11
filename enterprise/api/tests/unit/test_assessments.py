import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec, patch
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from enterprise_platform.application.assessments import (
    AlertSpecification,
    AlertVariable,
    AssessmentService,
    ExpectedSample,
    ImmutableSpecificationCatalog,
    QualityIdentityColumns,
    QualityLongMapping,
    QualitySpecification,
    QualityWideMapping,
    QualityWideMetric,
    Specification,
    SpecificationRegistry,
)
from enterprise_platform.application.contracts import Run, RunSpec, Scenario, canonical_hash
from enterprise_platform.application.errors import AccessDenied, InvalidInput, InvalidState
from enterprise_platform.application.input_capture import encode_rows
from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef, SourceValue
from enterprise_platform.domain.quality import QualityMetric, QualityStandard, UnitConversion
from enterprise_platform.domain.rules import AlertRule

NOW = datetime(2026, 9, 8, tzinfo=UTC)
LONG_COLUMNS = ("device", "record", "sample", "revision", "metric", "value", "unit", "time")


def test_specification_discovery_returns_only_exact_workspace_and_scenario_revisions():
    catalog = ImmutableSpecificationCatalog(
        (
            alert_spec(),
            alert_spec().model_copy(update={"specification_revision": "spec-v2"}),
            alert_spec().model_copy(update={"workspace_id": "other", "specification_revision": "private"}),
        )
    )
    assert catalog.list_revisions("workspace-1", "alert") == ("spec-v1", "spec-v2")
    assert catalog.list_revisions("workspace-1", "quality") == ()


def captured_run(
    rows: tuple[tuple[SourceValue, ...], ...], columns: tuple[str, ...], scenario: Scenario = "alert"
) -> Run:
    parameters = {"batch": "0001"}
    frozen = FrozenRows(
        SourceRef(workspace_id="workspace-1", source_id="source-1", revision="source-v1"),
        "read-1",
        "read-v1",
        "a" * 64,
        NOW,
        columns,
        rows,
        (PageEvidence(1, len(rows), "b" * 64),),
    )
    return Run(
        id="run-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        request_key="request-1",
        payload_hash="hash",
        status="claimed",
        dispatch_nonce="private-nonce",
        created_at=NOW,
        updated_at=NOW,
        spec=RunSpec(
            binding_id="binding-1",
            binding_revision=1,
            device_id="device-1",
            scenario=scenario,
            app_id="app-1",
            workflow_id=UUID(int=1),
            specification_revision="spec-v1",
            secret_ref="private-ref",
            source_id="source-1",
            source_revision="source-v1",
            read_id="read-1",
            read_revision="read-v1",
            parameters=parameters,
            manifest={"expression": "forged-model-expression"},
        ),
        input_snapshot={
            "schema_version": 1,
            "device_id": "device-1",
            "parameters_digest": canonical_hash(parameters),
            "scope_column": "device",
            "scope_value": "0001",
            "data": encode_rows(frozen),
        },
    )


def alert_spec() -> AlertSpecification:
    return AlertSpecification(
        workspace_id="workspace-1",
        specification_revision="spec-v1",
        variables=(AlertVariable(name="temperature", column="temp", kind="decimal"),),
        rules=(AlertRule("hot", "rule-v3", "temperature > 85", "high", "Too hot"),),
    )


def identity() -> QualityIdentityColumns:
    return QualityIdentityColumns(
        record_id="record", sample_id="sample", measurement_revision="revision", measured_at="time"
    )


def quality_spec(*, wide: bool = False) -> QualitySpecification:
    return QualitySpecification(
        workspace_id="workspace-1",
        specification_revision="spec-v1",
        standard=QualityStandard(
            "standard-1",
            "standard-v2",
            (QualityMetric("pressure-limit", "pressure", "MPa", upper_bound=Decimal("85")),),
        ),
        mapping=QualityWideMapping(
            identity=identity(), metrics=(QualityWideMetric(metric="pressure", value="pressure", unit="MPa"),)
        )
        if wide
        else QualityLongMapping(identity=identity(), metric="metric", value="value", unit="unit"),
    )


def assess(run: Run, spec: AlertSpecification | QualitySpecification):
    return AssessmentService(ImmutableSpecificationCatalog((spec,))).assess(run)


def test_alerts_evaluate_every_row_with_precise_evidence_and_outer_capture_digest() -> None:
    run = captured_run((("0001", Decimal("85.0000000000000001")), ("0001", "85")), ("device", "temp"))

    envelope = assess(run, alert_spec())

    assert envelope.result.conclusion == "issues"
    assert envelope.result.complete is True
    assert envelope.input_snapshot_digest == canonical_hash(run.input_snapshot)
    assert run.input_snapshot is not None
    assert envelope.input_snapshot_digest != canonical_hash(run.input_snapshot["data"])
    evidence = envelope.result.model_dump(mode="json")["evidence"]
    assert [row["conclusion"] for row in evidence["rows"]] == ["issues", "normal"]
    assert evidence["rows"][0]["inputs"]["temperature"] == "85.0000000000000001"
    assert evidence["rows"][0]["findings"][0]["rule_revision"] == "rule-v3"
    assert evidence["specification_revision"] == "spec-v1"
    assert "private-nonce" not in envelope.model_dump_json()
    assert "forged-model-expression" not in envelope.model_dump_json()


@pytest.mark.parametrize(
    ("values", "conclusion", "complete"),
    [
        ((Decimal(86), None), "issues", False),
        ((Decimal(1), None), "incomplete", False),
        ((None, None), "no_data", False),
        ((0, "0.000"), "normal", True),
        ((), "no_data", False),
    ],
)
def test_alert_overall_known_issues_survive_missing_evidence(values, conclusion, complete) -> None:
    result = assess(captured_run(tuple(("0001", value) for value in values), ("device", "temp")), alert_spec()).result
    assert (result.conclusion, result.complete) == (conclusion, complete)


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "not-a-number", "1e1001"])
def test_invalid_alert_numeric_input_is_rejected_instead_of_ignored(value: SourceValue) -> None:
    with pytest.raises(InvalidInput, match="assessment_input_invalid"):
        assess(captured_run((("0001", value),), ("device", "temp")), alert_spec())


def test_declared_alert_string_and_boolean_columns_are_not_coerced() -> None:
    spec = AlertSpecification(
        workspace_id="workspace-1",
        specification_revision="spec-v1",
        variables=(
            AlertVariable(name="mode", column="state", kind="string"),
            AlertVariable(name="active", column="on", kind="boolean"),
        ),
        rules=(AlertRule("enabled", "v1", "mode == '0001' and active", "high", "Active"),),
    )
    assert (
        assess(captured_run((("0001", "0001", True),), ("device", "state", "on")), spec).result.conclusion == "issues"
    )
    with pytest.raises(InvalidInput):
        assess(captured_run((("0001", "0001", "true"),), ("device", "state", "on")), spec)


def test_short_circuit_alert_match_retains_incomplete_flag() -> None:
    spec = AlertSpecification(
        workspace_id="workspace-1",
        specification_revision="spec-v1",
        variables=(
            AlertVariable(name="a", column="a", kind="decimal"),
            AlertVariable(name="b", column="b", kind="decimal"),
        ),
        rules=(AlertRule("or", "v1", "a > 5 or b > 5", "high", "Over limit"),),
    )
    result = assess(captured_run((("0001", 6, None),), ("device", "a", "b")), spec).result
    assert (result.conclusion, result.complete) == ("issues", False)


@pytest.mark.parametrize("scenario", ["alert", "quality"])
def test_empty_captures_are_never_successful_assessments(scenario: Scenario) -> None:
    run = captured_run((), (), scenario)
    result = assess(run, alert_spec() if scenario == "alert" else quality_spec()).result
    assert result.conclusion == ("no_data" if scenario == "alert" else "incomplete")
    assert result.complete is False


def test_long_quality_rows_keep_sample_revisions_and_raw_precision() -> None:
    rows = (
        ("0001", "r2", "0001", "v2", "pressure", "85.0000000000000001", "MPa", NOW),
        ("0001", "r1", "0001", "v1", "pressure", Decimal("85.000"), "MPa", NOW),
    )
    result = assess(captured_run(rows, LONG_COLUMNS, "quality"), quality_spec()).result
    evidence = result.model_dump(mode="json")["evidence"]
    assert (result.conclusion, result.complete) == ("failed", True)
    assert [(sample["sample_id"], sample["measurement_revision"]) for sample in evidence["samples"]] == [
        ("0001", "v1"),
        ("0001", "v2"),
    ]
    assert evidence["standard_revision"] == "standard-v2"
    assert evidence["samples"][1]["items"][0]["raw_value"] == "85.0000000000000001"
    assert evidence["samples"][1]["items"][0]["comparison_value"] == "85.0000000000000001"
    assert evidence["pass_rate_numerator"] == 1
    assert evidence["pass_rate_denominator"] == 2


def test_explicit_unit_conversion_does_not_replace_source_evidence() -> None:
    spec = quality_spec().model_copy(update={"conversions": (UnitConversion("bar", "MPa", Decimal("0.1")),)})
    run = captured_run((("0001", "r1", "0001", "v1", "pressure", "850.000", "bar", NOW),), LONG_COLUMNS, "quality")
    item = assess(run, spec).result.model_dump(mode="json")["evidence"]["samples"][0]["items"][0]
    assert (item["raw_value"], item["normalized_value"]) == ("850.000", "85.0000")
    assert item["conversion"]["factor"] == "0.1"


@pytest.mark.parametrize(("value", "unit", "conclusion"), [(None, "MPa", "incomplete"), ("1", "bar", "incomplete")])
def test_null_values_or_missing_conversion_do_not_pass_quality(value, unit, conclusion) -> None:
    result = assess(
        captured_run((("0001", "r1", "0001", "v1", "pressure", value, unit, NOW),), LONG_COLUMNS, "quality"),
        quality_spec(),
    ).result
    assert (result.conclusion, result.complete) == (conclusion, False)


def test_expected_sample_revisions_detect_fully_absent_samples() -> None:
    spec = quality_spec().model_copy(
        update={
            "expected_samples": (
                ExpectedSample(sample_id="0001", measurement_revision="v1"),
                ExpectedSample(sample_id="0002", measurement_revision="v1"),
            )
        }
    )
    result = assess(
        captured_run((("0001", "r1", "0001", "v1", "pressure", "1", "MPa", NOW),), LONG_COLUMNS, "quality"), spec
    ).result
    assert (result.conclusion, result.complete) == ("incomplete", False)
    assert len(result.model_dump(mode="json")["evidence"]["samples"]) == 2


def test_wide_mapping_expands_multiple_metrics_with_collision_free_source_lineage() -> None:
    spec = QualitySpecification(
        workspace_id="workspace-1",
        specification_revision="spec-v1",
        standard=QualityStandard("s", "v1", (QualityMetric("p", "b:c", "MPa"), QualityMetric("t", "c", "C"))),
        mapping=QualityWideMapping(
            identity=identity(),
            metrics=(
                QualityWideMetric(metric="b:c", value="pressure", unit="MPa"),
                QualityWideMetric(metric="c", value="temperature", unit_column="temp_unit"),
            ),
        ),
    )
    columns = ("device", "record", "sample", "revision", "time", "pressure", "temperature", "temp_unit")
    rows = (("0001", "a", "0001", "v1", NOW, "1.00", "20.001", "C"), ("0001", "a:b", "0002", "v1", NOW, "2", "30", "C"))
    result = assess(captured_run(rows, columns, "quality"), spec).result
    evidence = result.model_dump(mode="json")["evidence"]
    assert result.conclusion == "passed"
    ids = [item["record_id"] for sample in evidence["samples"] for item in sample["items"]]
    assert len(set(ids)) == 4
    assert {row["source_record_id"] for row in evidence["record_lineage"]} == {"a", "a:b"}
    assert evidence["samples"][0]["items"][0]["raw_value"] == "1.00"
    reverse = assess(captured_run(tuple(reversed(rows)), columns, "quality"), spec).result.model_dump(mode="json")[
        "evidence"
    ]
    assert evidence["samples"] == reverse["samples"]
    assert evidence["record_lineage"] == reverse["record_lineage"]


@pytest.mark.parametrize("bad_column", ["record", "sample", "revision", "metric", "unit", "time"])
def test_quality_identity_and_timestamp_values_are_not_coerced(bad_column: str) -> None:
    row: list[SourceValue] = ["0001", "r1", "0001", "v1", "pressure", "1", "MPa", NOW]
    row[LONG_COLUMNS.index(bad_column)] = 1
    with pytest.raises(InvalidInput):
        assess(captured_run((tuple(row),), LONG_COLUMNS, "quality"), quality_spec())


@pytest.mark.parametrize("scenario", ["alert", "quality"])
def test_missing_declared_columns_are_rejected(scenario: Scenario) -> None:
    with pytest.raises(InvalidInput, match="assessment_columns_missing"):
        assess(
            captured_run((("0001",),), ("device",), scenario), alert_spec() if scenario == "alert" else quality_spec()
        )


def test_missing_or_tampered_capture_is_not_replaced_by_model_or_live_data() -> None:
    run = captured_run((("0001", 1),), ("device", "temp"))
    with patch(
        "enterprise_platform.application.input_capture.RegisteredSourceReader.read",
        side_effect=AssertionError("Live read"),
    ):
        with pytest.raises(InvalidState):
            assess(run.model_copy(update={"input_snapshot": None}), alert_spec())
        assert run.input_snapshot is not None
        wrong = {**run.input_snapshot, "parameters_digest": "0" * 64}
        with pytest.raises(InvalidInput):
            assess(run.model_copy(update={"input_snapshot": wrong}), alert_spec())


def test_registry_uses_exact_workspace_scenario_revision_and_copies_config() -> None:
    spec = alert_spec()
    catalog = ImmutableSpecificationCatalog((spec, quality_spec()))
    assert catalog.resolve("workspace-1", "alert", "spec-v1") == spec
    assert catalog.resolve("workspace-1", "alert", "spec-v1") is not spec
    for key in [("other", "alert", "spec-v1"), ("workspace-1", "alert", "latest")]:
        with pytest.raises(InvalidInput, match="assessment_specification_unavailable"):
            catalog.resolve(*key)
    with pytest.raises(ValueError):
        ImmutableSpecificationCatalog((spec, spec))
    with pytest.raises(ValidationError):
        spec.workspace_id = "other"


def test_service_rechecks_an_injected_registry_scope() -> None:
    registry = create_autospec(SpecificationRegistry, instance=True, spec_set=True)
    registry.resolve.return_value = alert_spec().model_copy(update={"workspace_id": "other"})
    with pytest.raises(AccessDenied):
        AssessmentService(registry).assess(captured_run((("0001", 1),), ("device", "temp")))


def test_invalid_or_ambiguous_specification_declarations_fail_before_evaluation() -> None:
    with pytest.raises(ValueError):
        ImmutableSpecificationCatalog(
            (alert_spec().model_copy(update={"rules": (AlertRule("x", "v1", "unknown > 1", "high", "Bad"),)}),)
        )
    conversion = UnitConversion("bar", "MPa", Decimal("0.1"))
    with pytest.raises(ValueError):
        ImmutableSpecificationCatalog((quality_spec().model_copy(update={"conversions": (conversion, conversion)}),))
    with pytest.raises(ValueError):
        QualityWideMetric(metric="x", value="value")
    with pytest.raises(ValueError):
        QualityWideMetric(metric="x", value="value", unit="C", unit_column="unit")


def test_alert_raw_large_integers_are_text_at_the_json_boundary() -> None:
    value = 123456789012345678901234567890
    result = assess(captured_run((("0001", value),), ("device", "temp")), alert_spec()).result
    row = result.model_dump(mode="json")["evidence"]["rows"][0]
    assert row["inputs"]["temperature"] == str(value)
    assert row["raw_inputs"]["temperature"] == {"column": "temp", "type": "int", "value": str(value)}


def test_quality_raw_large_integers_are_text_at_the_json_boundary() -> None:
    value = 123456789012345678901234567890
    run = captured_run((("0001", "r1", "0001", "v1", "pressure", value, "MPa", NOW),), LONG_COLUMNS, "quality")
    item = assess(run, quality_spec()).result.model_dump(mode="json")["evidence"]["samples"][0]["items"][0]
    assert item["raw_value"] == str(value)
    assert item["calculated_value"] == str(value)


@pytest.mark.parametrize("scenario", ["alert", "quality"])
def test_oversized_output_fails_explicitly_without_truncating_or_mutating_capture(scenario: Scenario) -> None:
    run = (
        captured_run((("0001", 1),), ("device", "temp"))
        if scenario == "alert"
        else captured_run(
            (("0001", "r1", "0001", "v1", "pressure", "1", "MPa", NOW),),
            LONG_COLUMNS,
            "quality",
        )
    )
    original = run.model_dump_json()
    service = AssessmentService(
        ImmutableSpecificationCatalog((alert_spec() if scenario == "alert" else quality_spec(),)), max_output_bytes=128
    )
    with pytest.raises(InvalidInput, match="assessment_output_limit"):
        service.assess(run)
    assert run.model_dump_json() == original


def test_output_limit_counts_utf8_bytes_at_the_exact_envelope_boundary() -> None:
    spec = alert_spec().model_copy(update={"rules": (AlertRule("hot", "v1", "temperature > 85", "high", "温度过高"),)})
    registry = ImmutableSpecificationCatalog((spec,))
    run = captured_run((("0001", 86),), ("device", "temp"))
    envelope = AssessmentService(registry).assess(run)
    size = len(envelope.model_dump_json().encode("utf-8"))
    assert AssessmentService(registry, max_output_bytes=size).assess(run) == envelope
    with pytest.raises(InvalidInput, match="assessment_output_limit"):
        AssessmentService(registry, max_output_bytes=size - 1).assess(run)


@pytest.mark.parametrize("limit", [0, -1, True, 512 * 1024 + 1])
def test_output_budget_is_a_bounded_server_policy(limit: int) -> None:
    with pytest.raises(ValueError):
        AssessmentService(ImmutableSpecificationCatalog((alert_spec(),)), max_output_bytes=limit)


def test_specifications_round_trip_through_typed_server_json_configuration() -> None:
    adapter = TypeAdapter(tuple[Specification, ...])
    original = (alert_spec(), quality_spec(), quality_spec(wide=True))
    restored = adapter.validate_json(adapter.dump_json(original))
    assert restored == original
    assert isinstance(restored[1], QualitySpecification)
    assert restored[1].standard.metrics[0].upper_bound == Decimal("85")


@pytest.mark.parametrize("field", ["upper_bound", "lower_bound", "factor", "offset"])
def test_specification_json_rejects_floating_point_decimal_fields(field: str) -> None:
    spec = quality_spec().model_copy(update={"conversions": (UnitConversion("kPa", "MPa", Decimal("0.001")),)})
    payload = spec.model_dump_json()
    document = json.loads(payload)
    target = document["standard"]["metrics"][0] if field.endswith("bound") else document["conversions"][0]
    target[field] = 1.0000000000000001
    with pytest.raises(ValidationError):
        QualitySpecification.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("value", ['"9007199254740993.0000000000000001"', "9007199254740993"])
def test_specification_json_keeps_exact_decimal_text_and_integer_bounds(value: str) -> None:
    payload = quality_spec().model_dump_json().replace('"upper_bound":"85"', f'"upper_bound":{value}')
    restored = QualitySpecification.model_validate_json(payload)
    assert restored.standard.metrics[0].upper_bound == Decimal(value.strip('"'))


@pytest.mark.parametrize("value", [True, "NaN", "not-numeric", "1e1001"])
def test_invalid_quality_values_are_rejected_without_a_partial_pass(value: SourceValue) -> None:
    run = captured_run((("0001", "r1", "0001", "v1", "pressure", value, "MPa", NOW),), LONG_COLUMNS, "quality")
    with pytest.raises(InvalidInput, match="assessment_input_invalid"):
        assess(run, quality_spec())


@pytest.mark.parametrize("timestamp", [None, "not-a-date", "2026-09-08T00:00:00"])
def test_quality_timestamps_require_explicit_valid_timezones(timestamp: SourceValue) -> None:
    run = captured_run((("0001", "r1", "0001", "v1", "pressure", "1", "MPa", timestamp),), LONG_COLUMNS, "quality")
    with pytest.raises(InvalidInput):
        assess(run, quality_spec())


@pytest.mark.parametrize("duplicate_record", [True, False])
def test_quality_rejects_duplicate_source_or_sample_metric_revision(duplicate_record: bool) -> None:
    first = ("0001", "r1", "0001", "v1", "pressure", "1", "MPa", NOW)
    second = (
        "0001",
        "r1" if duplicate_record else "r2",
        "0002" if duplicate_record else "0001",
        "v1",
        "pressure",
        "2",
        "MPa",
        NOW,
    )
    with pytest.raises(InvalidInput):
        assess(captured_run((first, second), LONG_COLUMNS, "quality"), quality_spec())


def test_quality_failure_is_preserved_alongside_missing_required_metrics() -> None:
    spec = quality_spec().model_copy(
        update={
            "standard": QualityStandard(
                "s",
                "v2",
                (
                    QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(5)),
                    QualityMetric("t", "temperature", "C"),
                ),
            )
        }
    )
    result = assess(
        captured_run((("0001", "r1", "0001", "v1", "pressure", "6", "MPa", NOW),), LONG_COLUMNS, "quality"), spec
    ).result
    assert (result.conclusion, result.complete) == ("failed", False)
    assert result.evidence["pass_rate_denominator"] == 0


def test_quality_review_stays_distinct_from_passed() -> None:
    spec = quality_spec().model_copy(
        update={"standard": QualityStandard("s", "v2", (QualityMetric("p", "pressure", "MPa", requires_review=True),))}
    )
    result = assess(
        captured_run((("0001", "r1", "0001", "v1", "pressure", "6", "MPa", NOW),), LONG_COLUMNS, "quality"), spec
    ).result
    assert (result.conclusion, result.complete) == ("review", True)
    assert result.evidence["pass_rate_denominator"] == 0


def test_quality_rejects_unregistered_metrics_and_out_of_scope_samples() -> None:
    run = captured_run((("0001", "r1", "0001", "v1", "unknown", "1", "MPa", NOW),), LONG_COLUMNS, "quality")
    with pytest.raises(InvalidInput, match="assessment_unregistered_metric"):
        assess(run, quality_spec())
    spec = quality_spec().model_copy(
        update={"expected_samples": (ExpectedSample(sample_id="0002", measurement_revision="v1"),)}
    )
    run = captured_run((("0001", "r1", "0001", "v1", "pressure", "1", "MPa", NOW),), LONG_COLUMNS, "quality")
    with pytest.raises(InvalidInput):
        assess(run, spec)


def test_expected_sample_metric_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("enterprise_platform.application.assessments._MAX_ASSESSMENT_ITEMS", 1)
    spec = quality_spec().model_copy(
        update={
            "expected_samples": (
                ExpectedSample(sample_id="0001", measurement_revision="v1"),
                ExpectedSample(sample_id="0002", measurement_revision="v1"),
            )
        }
    )
    with pytest.raises(InvalidInput, match="assessment_size_limit"):
        assess(captured_run((), (), "quality"), spec)
