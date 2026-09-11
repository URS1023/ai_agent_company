from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest


def test_measurement_preserves_ids_raw_precision_and_aware_time() -> None:
    from enterprise_platform.domain.measurements import Measurement

    sampled = datetime(2026, 9, 8, 14, tzinfo=timezone(timedelta(hours=8)))
    record = Measurement("r01", "001", "0001", "01", "temperature", "85.0000000000000001", "C", sampled)

    assert record.device_id == "001"
    assert record.sample_id == "0001"
    assert record.raw_value == "85.0000000000000001"
    assert record.value == Decimal("85.0000000000000001")
    assert record.measured_at.astimezone(UTC).hour == 6
    with pytest.raises(FrozenInstanceError):
        record.unit = "F"


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "1e1001", "9" * 129, "", "not numeric"])
def test_invalid_numeric_source_is_rejected(value: object) -> None:
    from enterprise_platform.domain.measurements import MeasurementError, parse_decimal

    with pytest.raises(MeasurementError):
        parse_decimal(value)


def test_zero_null_and_decimal_remain_distinct() -> None:
    from enterprise_platform.domain.measurements import parse_decimal

    assert parse_decimal(None) is None
    assert parse_decimal("0") == Decimal(0)
    assert parse_decimal(12345678901234567890) == Decimal("12345678901234567890")
    assert parse_decimal(Decimal("0.100")) == Decimal("0.100")


def test_naive_measurement_timestamp_is_rejected() -> None:
    from enterprise_platform.domain.measurements import Measurement, MeasurementError

    with pytest.raises(MeasurementError, match="timezone"):
        Measurement("r", "d", "s", "v1", "x", "1", "MPa", datetime(2026, 9, 8))


def test_identifier_is_not_coerced_from_numeric_source() -> None:
    from enterprise_platform.domain.measurements import Measurement, MeasurementError

    with pytest.raises(MeasurementError, match="sample_id"):
        Measurement("r", "d", 1, "v1", "x", "1", "MPa", datetime.now(UTC))


def test_snapshot_copies_collection_and_has_content_fingerprint() -> None:
    from enterprise_platform.domain.measurements import Measurement, MeasurementSnapshot

    time = datetime(2026, 9, 8, tzinfo=UTC)
    record = Measurement("r", "d", "0001", "v1", "x", "1.00", "MPa", time)
    records = [record]
    snapshot = MeasurementSnapshot("snap1", "source1", "source-v1", time, records)
    records.clear()

    assert snapshot.measurements == (record,)
    assert len(snapshot.fingerprint) == 64
    assert snapshot.fingerprint == MeasurementSnapshot("snap1", "source1", "source-v1", time, (record,)).fingerprint
    assert snapshot.fingerprint != MeasurementSnapshot("snap1", "source1", "source-v2", time, (record,)).fingerprint


def test_snapshot_rejects_duplicate_sample_metric_revision() -> None:
    from enterprise_platform.domain.measurements import Measurement, MeasurementError, MeasurementSnapshot

    time = datetime(2026, 9, 8, tzinfo=UTC)
    first = Measurement("r1", "d", "s", "v1", "x", "1", "MPa", time)
    second = Measurement("r2", "d", "s", "v1", "x", "2", "MPa", time)

    with pytest.raises(MeasurementError, match="duplicate"):
        MeasurementSnapshot("snap", "source", "v1", time, (first, second))


def test_snapshot_accepts_empty_data_without_fabrication() -> None:
    from enterprise_platform.domain.measurements import MeasurementSnapshot

    snapshot = MeasurementSnapshot("snap", "source", "v1", datetime.now(UTC), ())
    assert snapshot.measurements == ()
