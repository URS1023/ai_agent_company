from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from enterprise_platform.domain.measurements import Measurement, MeasurementSnapshot, MeasurementValue


def _record(
    sample: str,
    value: MeasurementValue,
    metric: str = "pressure",
    revision: str = "v1",
    unit: str = "MPa",
    device: str = "d",
) -> Measurement:
    from enterprise_platform.domain.measurements import Measurement

    return Measurement(
        f"{device}:{sample}:{revision}:{metric}",
        device,
        sample,
        revision,
        metric,
        value,
        unit,
        datetime(2026, 9, 8, tzinfo=UTC),
    )


def _snapshot(*records: Measurement) -> MeasurementSnapshot:
    from enterprise_platform.domain.measurements import MeasurementSnapshot

    return MeasurementSnapshot("snap", "source", "v1", datetime(2026, 9, 8, tzinfo=UTC), records)


def test_boundary_and_high_precision_values_are_deterministic() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard(
        "standard", "v1", (QualityMetric("pressure-limit", "pressure", "MPa", upper_bound=Decimal("85")),)
    )
    result = evaluate_quality(_snapshot(_record("0001", "85"), _record("0002", "85.0000000000000001")), standard)

    assert [sample.conclusion for sample in result.samples] == ["passed", "failed"]
    assert result.samples[1].items[0].raw_value == "85.0000000000000001"
    assert result.samples[1].items[0].calculated_value == Decimal("85.0000000000000001")
    assert result.snapshot_id == "snap"
    assert result.standard_revision == "v1"


def test_sample_revision_and_device_isolation_ignore_row_order() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("m", "pressure", "MPa", upper_bound=Decimal(5)),))
    records = (_record("0001", "6", revision="v2"), _record("0001", "1"), _record("0001", "2", device="d2"))
    result = evaluate_quality(_snapshot(*records), standard)
    reversed_result = evaluate_quality(_snapshot(*reversed(records)), standard)

    assert result == reversed_result
    assert [(s.device_id, s.sample_id, s.measurement_revision, s.conclusion) for s in result.samples] == [
        ("d", "0001", "v1", "passed"),
        ("d", "0001", "v2", "failed"),
        ("d2", "0001", "v1", "passed"),
    ]


def test_known_failure_and_missing_required_item_coexist() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard(
        "s",
        "1",
        (
            QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(5)),
            QualityMetric("t", "temperature", "C", upper_bound=Decimal(85)),
        ),
    )
    result = evaluate_quality(_snapshot(_record("0001", "6")), standard)
    sample = result.samples[0]

    assert sample.conclusion == "failed"
    assert sample.complete is False
    assert [item.conclusion for item in sample.items] == ["failed", "incomplete"]
    assert result.complete is False
    assert result.incomplete_samples == 1
    assert result.pass_rate_denominator == 0


def test_missing_optional_item_does_not_fail_a_complete_sample() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard(
        "s",
        "1",
        (
            QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(5)),
            QualityMetric("t", "temperature", "C", required=False),
        ),
    )
    result = evaluate_quality(_snapshot(_record("0001", "1")), standard)
    assert result.samples[0].conclusion == "passed"
    assert result.samples[0].complete is True


def test_null_and_wrong_units_remain_incomplete() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(5)),))
    result = evaluate_quality(_snapshot(_record("0001", None), _record("0002", "1", unit="bar")), standard)

    assert [item.items[0].error_code for item in result.samples] == ["missing_value", "unit_mismatch"]
    assert all(item.conclusion == "incomplete" for item in result.samples)
    assert result.pass_rate_denominator == 0


def test_explicit_unit_conversion_preserves_raw_and_normalized_values() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, UnitConversion, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(1)),))
    conversion = UnitConversion("bar", "MPa", Decimal("0.1"))
    result = evaluate_quality(_snapshot(_record("0001", "10", unit="bar")), standard, (conversion,))
    item = result.samples[0].items[0]

    assert item.raw_value == "10"
    assert item.source_unit == "bar"
    assert item.normalized_value == Decimal("1.0")
    assert item.calculated_value == Decimal("1.0")
    assert item.conclusion == "passed"


def test_calculated_and_rounded_values_are_separate() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard(
        "s",
        "1",
        (
            QualityMetric(
                "p",
                "pressure",
                "MPa",
                upper_bound=Decimal("1.24"),
                calculation="value / 10",
                rounding_places=2,
            ),
        ),
    )
    result = evaluate_quality(_snapshot(_record("0001", "12.45")), standard)
    item = result.samples[0].items[0]

    assert item.raw_value == "12.45"
    assert item.calculated_value == Decimal("1.245")
    assert item.comparison_value == Decimal("1.24")
    assert item.conclusion == "passed"


def test_exclusive_bounds_are_respected() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard(
        "s",
        "1",
        (
            QualityMetric(
                "p",
                "pressure",
                "MPa",
                lower_bound=Decimal(1),
                upper_bound=Decimal(2),
                lower_inclusive=False,
                upper_inclusive=False,
            ),
        ),
    )
    result = evaluate_quality(_snapshot(_record("0001", "1"), _record("0002", "2")), standard)
    assert all(sample.conclusion == "failed" for sample in result.samples)


def test_review_is_not_passed_or_incomplete() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa", requires_review=True),))
    result = evaluate_quality(_snapshot(_record("0001", "0")), standard)
    assert result.samples[0].conclusion == "review"
    assert result.samples[0].complete is True
    assert result.pass_rate_numerator == 0
    assert result.pass_rate_denominator == 0


def test_calculation_error_has_evidence_and_never_passes() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa", calculation="value / 0"),))
    result = evaluate_quality(_snapshot(_record("0001", "1")), standard)
    assert result.samples[0].conclusion == "incomplete"
    assert result.samples[0].items[0].error_code == "calculation_error"
    assert result.samples[0].items[0].raw_value == "1"


def test_empty_snapshot_returns_incomplete_without_invented_samples() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa"),))
    result = evaluate_quality(_snapshot(), standard)
    assert result.samples == ()
    assert result.conclusion == "incomplete"
    assert result.complete is False
    assert result.pass_rate_denominator == 0


def test_standard_revision_is_explicit_and_recalculation_does_not_mutate_input() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    snapshot = _snapshot(_record("0001", "5"))
    old = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(4)),))
    new = QualityStandard("s", "2", (QualityMetric("p", "pressure", "MPa", upper_bound=Decimal(6)),))
    first = evaluate_quality(snapshot, old)
    second = evaluate_quality(snapshot, new)
    assert first.conclusion == "failed"
    assert second.conclusion == "passed"
    assert first.standard_revision == "1"
    assert snapshot.measurements[0].value == Decimal(5)


def test_invalid_standard_bounds_or_expression_are_rejected() -> None:
    from enterprise_platform.domain.quality import QualityError, QualityMetric, QualityStandard

    with pytest.raises(QualityError):
        QualityMetric("p", "pressure", "MPa", lower_bound=Decimal(2), upper_bound=Decimal(1))
    with pytest.raises(QualityError):
        QualityMetric("p", "pressure", "MPa", calculation="other_metric + value")
    with pytest.raises(QualityError):
        QualityMetric("p", "pressure", "MPa", calculation="abs(value)")
    with pytest.raises(QualityError):
        QualityStandard("s", "1", ())


def test_ambiguous_conversion_is_rejected() -> None:
    from enterprise_platform.domain.quality import (
        QualityError,
        QualityMetric,
        QualityStandard,
        UnitConversion,
        evaluate_quality,
    )

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa"),))
    conversions = (UnitConversion("bar", "MPa", Decimal("0.1")), UnitConversion("bar", "MPa", Decimal("0.2")))
    with pytest.raises(QualityError, match="duplicate"):
        evaluate_quality(_snapshot(_record("0001", "1", unit="bar")), standard, conversions)


def test_required_flag_is_a_boolean_not_a_truthy_string() -> None:
    from enterprise_platform.domain.quality import QualityError, QualityMetric

    with pytest.raises(QualityError, match="boolean"):
        QualityMetric("p", "pressure", "MPa", required="false")


def test_conversion_definition_remains_in_evidence() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, UnitConversion, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("t", "pressure", "C"),))
    conversion = UnitConversion("K", "C", Decimal(1), Decimal("-273.15"))
    result = evaluate_quality(_snapshot(_record("0001", "300", unit="K")), standard, (conversion,))
    assert result.samples[0].items[0].conversion == conversion
    assert result.samples[0].items[0].normalized_value == Decimal("26.85")


def test_expected_sample_missing_entirely_is_reported() -> None:
    from enterprise_platform.domain.quality import QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa"),))
    result = evaluate_quality(
        _snapshot(_record("0001", "1")),
        standard,
        expected_samples=(("d", "0001", "v1"), ("d", "0002", "v1")),
    )
    assert result.samples[1].sample_id == "0002"
    assert result.samples[1].conclusion == "incomplete"
    assert result.complete is False
    assert result.pass_rate_numerator == result.pass_rate_denominator == 1


def test_records_outside_expected_scope_are_rejected() -> None:
    from enterprise_platform.domain.quality import QualityError, QualityMetric, QualityStandard, evaluate_quality

    standard = QualityStandard("s", "1", (QualityMetric("p", "pressure", "MPa"),))
    with pytest.raises(QualityError, match="scope"):
        evaluate_quality(_snapshot(_record("0002", "1")), standard, expected_samples=(("d", "0001", "v1"),))
