"""Immutable records read from an existing source, with no acquisition or storage side effects.

IDs remain strings, raw values retain their source representation, and numeric values
are finite Decimals. Snapshots describe actual reads rather than future query parameters.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

type MeasurementValue = Decimal | str | int | None
MAX_DIGITS = 128
MAX_EXPONENT = 1000


class MeasurementError(ValueError):
    """Source records violate the deterministic measurement contract."""


def require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
        raise MeasurementError(f"{field_name} must be a nonempty, unpadded string of at most 256 characters")
    return value


def require_aware_time(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MeasurementError(f"{field_name} requires an explicit timezone")


def parse_decimal(value: object) -> Decimal | None:
    """Preserve exact finite source decimals; reject floats, coercive booleans and oversized values."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise MeasurementError("measurement value must be Decimal, decimal text, integer or null")
    if isinstance(value, int) and value.bit_length() > 426:
        raise MeasurementError("measurement value exceeds numeric limits")
    if isinstance(value, str) and (len(value) > MAX_DIGITS + 16 or not value.strip()):
        raise MeasurementError("measurement value exceeds numeric limits or is empty")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise MeasurementError("measurement value is not a decimal") from error
    if not result.is_finite():
        raise MeasurementError("measurement value must be finite")
    exponent = result.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or abs(exponent) > MAX_EXPONENT
        or abs(result.adjusted()) > MAX_EXPONENT
        or len(result.as_tuple().digits) > MAX_DIGITS
    ):
        raise MeasurementError("measurement value exceeds numeric limits")
    return result


@dataclass(frozen=True, slots=True)
class Measurement:
    record_id: str
    device_id: str
    sample_id: str
    measurement_revision: str
    metric: str
    raw_value: MeasurementValue
    unit: str
    measured_at: datetime
    value: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        for name in ("record_id", "device_id", "sample_id", "measurement_revision", "metric", "unit"):
            require_identifier(getattr(self, name), name)
        require_aware_time(self.measured_at, "measured_at")
        object.__setattr__(self, "value", parse_decimal(self.raw_value))


@dataclass(frozen=True, slots=True)
class MeasurementSnapshot:
    snapshot_id: str
    source_id: str
    source_revision: str
    captured_at: datetime
    measurements: tuple[Measurement, ...]

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "source_id", "source_revision"):
            require_identifier(getattr(self, name), name)
        require_aware_time(self.captured_at, "captured_at")
        records = tuple(self.measurements)
        identities: set[tuple[str, str, str, str]] = set()
        record_ids: set[str] = set()
        for record in records:
            if not isinstance(record, Measurement):
                raise MeasurementError("snapshot contains a non-measurement record")
            identity = (record.device_id, record.sample_id, record.measurement_revision, record.metric)
            if identity in identities or record.record_id in record_ids:
                raise MeasurementError("duplicate source record or sample metric revision")
            identities.add(identity)
            record_ids.add(record.record_id)
        object.__setattr__(self, "measurements", records)

    @property
    def fingerprint(self) -> str:
        """Hash the captured source content, including raw precision and provenance; row order is irrelevant."""
        rows = [
            (
                record.record_id,
                record.device_id,
                record.sample_id,
                record.measurement_revision,
                record.metric,
                type(record.raw_value).__name__,
                None if record.raw_value is None else str(record.raw_value),
                record.unit,
                record.measured_at.astimezone(UTC).isoformat(),
            )
            for record in sorted(self.measurements, key=lambda item: item.record_id)
        ]
        content = (self.source_id, self.source_revision, self.captured_at.astimezone(UTC).isoformat(), rows)
        encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
