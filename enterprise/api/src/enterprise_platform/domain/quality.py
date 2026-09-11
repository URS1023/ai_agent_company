"""Pure quality assessment of frozen, already-collected measurements.

Each sample revision is isolated. Missing evidence and known failures coexist;
review is a separate verdict, not successful execution. Unit conversion is explicit,
and a metric's bounded calculation receives only its unit-normalized ``value``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from typing import Literal

from .measurements import (
    MAX_DIGITS,
    MAX_EXPONENT,
    Measurement,
    MeasurementError,
    MeasurementSnapshot,
    MeasurementValue,
    parse_decimal,
    require_identifier,
)
from .rules import RuleError, evaluate_expression, validate_expression

type QualityConclusion = Literal["passed", "failed", "review", "incomplete"]
type SampleKey = tuple[str, str, str]


class QualityError(ValueError):
    """An immutable standard or explicit conversion is invalid or ambiguous."""


@dataclass(frozen=True, slots=True)
class QualityMetric:
    metric_id: str
    source_metric: str
    unit: str
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    required: bool = True
    calculation: str = "value"
    rounding_places: int | None = None
    requires_review: bool = False

    def __post_init__(self) -> None:
        for name in ("required", "requires_review", "lower_inclusive", "upper_inclusive"):
            if not isinstance(getattr(self, name), bool):
                raise QualityError(f"{name} must be a boolean")
        try:
            for name in ("metric_id", "source_metric", "unit"):
                require_identifier(getattr(self, name), name)
            object.__setattr__(self, "lower_bound", parse_decimal(self.lower_bound))
            object.__setattr__(self, "upper_bound", parse_decimal(self.upper_bound))
            validate_expression(self.calculation, frozenset({"value"}))
        except (MeasurementError, RuleError) as error:
            raise QualityError(str(error)) from error
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise QualityError("Lower bound exceeds upper bound")
        if self.rounding_places is not None and (
            isinstance(self.rounding_places, bool)
            or not isinstance(self.rounding_places, int)
            or not 0 <= self.rounding_places <= 28
        ):
            raise QualityError("Rounding places must be an integer between 0 and 28")


@dataclass(frozen=True, slots=True)
class QualityStandard:
    standard_id: str
    revision: str
    metrics: tuple[QualityMetric, ...]

    def __post_init__(self) -> None:
        require_identifier(self.standard_id, "standard_id")
        require_identifier(self.revision, "revision")
        metrics = tuple(self.metrics)
        if not metrics or any(not isinstance(metric, QualityMetric) for metric in metrics):
            raise QualityError("Standard requires declared quality metrics")
        if len({metric.metric_id for metric in metrics}) != len(metrics):
            raise QualityError("Standard contains duplicate metric IDs")
        object.__setattr__(self, "metrics", metrics)


@dataclass(frozen=True, slots=True)
class UnitConversion:
    source_unit: str
    target_unit: str
    factor: Decimal
    offset: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        require_identifier(self.source_unit, "source_unit")
        require_identifier(self.target_unit, "target_unit")
        try:
            factor, offset = parse_decimal(self.factor), parse_decimal(self.offset)
        except MeasurementError as error:
            raise QualityError(str(error)) from error
        if factor is None or factor <= 0 or offset is None:
            raise QualityError("Unit conversion requires a positive factor and finite offset")
        object.__setattr__(self, "factor", factor)
        object.__setattr__(self, "offset", offset)


@dataclass(frozen=True, slots=True)
class QualityItem:
    metric_id: str
    source_metric: str
    record_id: str | None
    raw_value: MeasurementValue
    source_unit: str | None
    unit: str
    normalized_value: Decimal | None
    calculated_value: Decimal | None
    comparison_value: Decimal | None
    calculation: str
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    lower_inclusive: bool
    upper_inclusive: bool
    required: bool
    conclusion: QualityConclusion
    complete: bool
    error_code: str | None = None
    error: str | None = None
    conversion: UnitConversion | None = None


@dataclass(frozen=True, slots=True)
class SampleAssessment:
    device_id: str
    sample_id: str
    measurement_revision: str
    conclusion: QualityConclusion
    complete: bool
    items: tuple[QualityItem, ...]


@dataclass(frozen=True, slots=True)
class BatchAssessment:
    snapshot_id: str
    snapshot_fingerprint: str
    standard_id: str
    standard_revision: str
    conclusion: QualityConclusion
    complete: bool
    samples: tuple[SampleAssessment, ...]

    @property
    def incomplete_samples(self) -> int:
        """Count evidence incompleteness independently of the primary verdict."""
        return sum(not sample.complete for sample in self.samples)

    @property
    def pass_rate_numerator(self) -> int:
        return sum(sample.complete and sample.conclusion == "passed" for sample in self.samples)

    @property
    def pass_rate_denominator(self) -> int:
        """Exact rate denominator: complete, decided samples; excludes review and missing evidence."""
        return sum(sample.complete and sample.conclusion in ("passed", "failed") for sample in self.samples)


def _conclusion(conclusions: Sequence[QualityConclusion], complete: bool) -> QualityConclusion:
    if "failed" in conclusions:
        return "failed"
    if not complete:
        return "incomplete"
    if "review" in conclusions:
        return "review"
    return "passed"


def _normalize(record: Measurement, metric: QualityMetric, conversion: UnitConversion | None) -> Decimal | None:
    if record.unit == metric.unit:
        return record.value
    if conversion is not None:
        converted = evaluate_expression(
            "value * factor + offset",
            {"value": record.value, "factor": conversion.factor, "offset": conversion.offset},
        )
        if isinstance(converted, Decimal):
            return converted
    return None


def _round_for_comparison(value: Decimal, places: int | None) -> Decimal:
    if places is None:
        return value
    context = Context(prec=MAX_DIGITS, Emin=-MAX_EXPONENT, Emax=MAX_EXPONENT, rounding=ROUND_HALF_EVEN)
    with localcontext(context):
        return value.quantize(Decimal((0, (1,), -places)))


def _within_bounds(value: Decimal, metric: QualityMetric) -> bool:
    if metric.lower_bound is not None:
        if value < metric.lower_bound or (value == metric.lower_bound and not metric.lower_inclusive):
            return False
    if metric.upper_bound is not None:
        if value > metric.upper_bound or (value == metric.upper_bound and not metric.upper_inclusive):
            return False
    return True


def _assess_item(
    metric: QualityMetric, record: Measurement | None, conversions: Sequence[UnitConversion]
) -> QualityItem:
    normalized: Decimal | None = None
    calculated: Decimal | None = None
    compared: Decimal | None = None
    conclusion: QualityConclusion = "incomplete"
    code: str | None = None
    error: str | None = None
    conversion = next(
        (
            item
            for item in conversions
            if record is not None
            and record.unit != metric.unit
            and (item.source_unit, item.target_unit) == (record.unit, metric.unit)
        ),
        None,
    )
    if record is None:
        code, error = "missing_measurement", "Source metric is absent from this sample revision"
    elif record.value is None:
        code, error = "missing_value", "Source measurement is null"
    else:
        try:
            normalized = _normalize(record, metric, conversion)
            if normalized is None:
                code, error = "unit_mismatch", "No explicit source-to-standard unit conversion"
            else:
                result = evaluate_expression(metric.calculation, {"value": normalized})
                if not isinstance(result, Decimal):
                    raise RuleError("Quality calculation must return a number")
                calculated = result
                compared = _round_for_comparison(result, metric.rounding_places)
                conclusion = (
                    "failed"
                    if not _within_bounds(compared, metric)
                    else "review"
                    if metric.requires_review
                    else "passed"
                )
        except (RuleError, DecimalException) as exception:
            code, error = "calculation_error", str(exception)
    return QualityItem(
        metric.metric_id,
        metric.source_metric,
        record.record_id if record else None,
        record.raw_value if record else None,
        record.unit if record else None,
        metric.unit,
        normalized,
        calculated,
        compared,
        metric.calculation,
        metric.lower_bound,
        metric.upper_bound,
        metric.lower_inclusive,
        metric.upper_inclusive,
        metric.required,
        conclusion,
        code is None,
        code,
        error,
        conversion,
    )


def evaluate_quality(
    snapshot: MeasurementSnapshot,
    standard: QualityStandard,
    conversions: Sequence[UnitConversion] = (),
    *,
    expected_samples: Sequence[SampleKey] | None = None,
) -> BatchAssessment:
    """Assess existing records only; return immutable evidence, with no instruments, I/O or mutation.

    Missing optional items remain visible but do not invalidate a sample. Present optional
    items still contribute failures or review. Rates are returned as an exact count fraction.
    Supply expected_samples to detect entirely absent samples and reject out-of-scope rows;
    when omitted, completeness describes only the observed sample revisions.
    """
    conversion_keys = [(item.source_unit, item.target_unit) for item in conversions]
    if len(set(conversion_keys)) != len(conversion_keys):
        raise QualityError("Ambiguous duplicate unit conversion")
    groups: dict[SampleKey, dict[str, Measurement]] = {}
    if expected_samples is not None:
        for key in expected_samples:
            if len(key) != 3 or any(not isinstance(value, str) or not value.strip() for value in key):
                raise QualityError("Expected sample scope needs device, sample and revision strings")
            if key in groups:
                raise QualityError("Expected sample scope contains duplicate identities")
            groups[key] = {}
    for record in snapshot.measurements:
        key = (record.device_id, record.sample_id, record.measurement_revision)
        if expected_samples is not None and key not in groups:
            raise QualityError("Measurement is outside the expected sample scope")
        groups.setdefault(key, {})[record.metric] = record
    samples: list[SampleAssessment] = []
    for (device_id, sample_id, revision), records in sorted(groups.items()):
        items = tuple(
            _assess_item(metric, records.get(metric.source_metric), conversions) for metric in standard.metrics
        )
        complete = all(item.complete or (not item.required and item.record_id is None) for item in items)
        relevant = tuple(item.conclusion for item in items if item.required or item.record_id is not None)
        complete = complete and bool(relevant)
        samples.append(
            SampleAssessment(
                device_id,
                sample_id,
                revision,
                _conclusion(relevant, complete),
                complete,
                items,
            )
        )
    complete = bool(samples) and all(sample.complete for sample in samples)
    return BatchAssessment(
        snapshot.snapshot_id,
        snapshot.fingerprint,
        standard.standard_id,
        standard.revision,
        _conclusion(tuple(sample.conclusion for sample in samples), complete),
        complete,
        tuple(samples),
    )
