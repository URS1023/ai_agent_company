"""Assess persisted captures with server-owned, immutable specification versions.

No source reads, model outputs, temporary run manifests or new rule engine enter this
boundary. Long and wide table mappings both normalize to the existing Measurement
contract. Wide record identities hash a structured source-record/metric pair; lineage
retains the original source ID. Decimal evidence is serialized as exact text.

Work is bounded by record/rule and sample/metric expansion counts. JSON evidence has
an explicit UTF-8 byte budget (at most 512 KiB, including the complete envelope).
Oversized assessments fail rather than truncate data and claim a normal result.
Without declared expected samples, quality completeness covers observed revisions only.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from enterprise_platform.domain.data_sources import FrozenRows, Policy, SourceValue
from enterprise_platform.domain.measurements import (
    Measurement,
    MeasurementSnapshot,
    MeasurementValue,
    parse_decimal,
    require_identifier,
)
from enterprise_platform.domain.quality import (
    BatchAssessment,
    QualityStandard,
    SampleAssessment,
    UnitConversion,
    evaluate_quality,
)
from enterprise_platform.domain.rules import AlertAssessment, AlertRule, RuleValue, evaluate_alerts, validate_expression

from .contracts import BusinessResult, Identifier, JsonObject, Run, Scenario, canonical_hash, canonical_json
from .dispatcher import BusinessEnvelope
from .errors import AccessDenied, InvalidInput
from .input_capture import restore_capture

_MAX_ASSESSMENT_ITEMS = 100_000
_MAX_ENVELOPE_BYTES = 512 * 1024


class AlertVariable(Policy):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    column: Identifier
    kind: Literal["decimal", "string", "boolean"]


class AlertSpecification(Policy):
    workspace_id: Identifier
    specification_revision: Identifier
    scenario: Literal["alert"] = "alert"
    variables: tuple[AlertVariable, ...] = Field(min_length=1, max_length=256)
    rules: tuple[AlertRule, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unambiguous_rules(self) -> "AlertSpecification":
        names = frozenset(variable.name for variable in self.variables)
        if len(names) != len(self.variables) or len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("Duplicate alert variable or rule")
        for rule in self.rules:
            validate_expression(rule.expression, names)
        return self


class QualityIdentityColumns(Policy):
    record_id: Identifier
    sample_id: Identifier
    measurement_revision: Identifier
    measured_at: Identifier


class QualityLongMapping(Policy):
    format: Literal["long"] = "long"
    identity: QualityIdentityColumns
    metric: Identifier
    value: Identifier
    unit: Identifier


class QualityWideMetric(Policy):
    metric: Identifier
    value: Identifier
    unit: Identifier | None = None
    unit_column: Identifier | None = None

    @model_validator(mode="after")
    def explicit_unit(self) -> "QualityWideMetric":
        if (self.unit is None) == (self.unit_column is None):
            raise ValueError("Choose exactly one fixed source unit or source unit column")
        return self


class QualityWideMapping(Policy):
    format: Literal["wide"] = "wide"
    identity: QualityIdentityColumns
    metrics: tuple[QualityWideMetric, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_metrics(self) -> "QualityWideMapping":
        if len({item.metric for item in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate wide source metric")
        return self


class ExpectedSample(Policy):
    sample_id: Identifier
    measurement_revision: Identifier


class QualitySpecification(Policy):
    workspace_id: Identifier
    specification_revision: Identifier
    scenario: Literal["quality"] = "quality"
    standard: QualityStandard
    mapping: Annotated[QualityLongMapping | QualityWideMapping, Field(discriminator="format")]
    conversions: tuple[UnitConversion, ...] = ()
    expected_samples: tuple[ExpectedSample, ...] | None = None

    @field_validator("standard", mode="before")
    @classmethod
    def exact_standard_decimals(cls, value: object) -> object:
        # Pydantic's default JSON Decimal coercion accepts lossy binary floats.
        if isinstance(value, dict) and isinstance(value.get("metrics"), (list, tuple)):
            return {
                **value,
                "metrics": tuple(
                    _decimal_fields(metric, ("lower_bound", "upper_bound")) for metric in value["metrics"]
                ),
            }
        return value

    @field_validator("conversions", mode="before")
    @classmethod
    def exact_conversion_decimals(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(_decimal_fields(item, ("factor", "offset")) for item in value)
        return value

    @model_validator(mode="after")
    def unambiguous_scope(self) -> "QualitySpecification":
        if len(self.standard.metrics) > 256:
            raise ValueError("Quality standard exceeds the metric limit")
        units = {(item.source_unit, item.target_unit) for item in self.conversions}
        samples = {(item.sample_id, item.measurement_revision) for item in self.expected_samples or ()}
        if len(units) != len(self.conversions) or len(samples) != len(self.expected_samples or ()):
            raise ValueError("Duplicate conversion or expected sample revision")
        if isinstance(self.mapping, QualityWideMapping) and any(
            item.metric not in {metric.source_metric for metric in self.standard.metrics}
            for item in self.mapping.metrics
        ):
            raise ValueError("Wide mapping references a source metric outside the standard")
        return self


def _decimal_fields(value: object, fields: tuple[str, ...]) -> object:
    if not isinstance(value, dict):
        return value
    return {key: parse_decimal(item) if key in fields else item for key, item in value.items()}


type Specification = AlertSpecification | QualitySpecification
type SpecificationKey = tuple[str, Scenario, str]
_SPECIFICATION: TypeAdapter[Specification] = TypeAdapter(Annotated[Specification, Field(discriminator="scenario")])
_ALERT_ASSESSMENT = TypeAdapter(AlertAssessment)
_BATCH_ASSESSMENT = TypeAdapter(BatchAssessment)
_SAMPLE_ASSESSMENT = TypeAdapter(SampleAssessment)
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _key(specification: Specification) -> SpecificationKey:
    return specification.workspace_id, specification.scenario, specification.specification_revision


class SpecificationRegistry(Protocol):
    def resolve(self, workspace_id: str, scenario: Scenario, specification_revision: str) -> Specification: ...

    def list_revisions(self, workspace_id: str, scenario: Scenario) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True, init=False)
class ImmutableSpecificationCatalog:
    """A version cannot be replaced in this catalog; a new revision requires a new entry."""

    _entries: tuple[Specification, ...]

    def __init__(self, entries: tuple[Specification, ...]) -> None:
        copied = tuple(_SPECIFICATION.validate_json(entry.model_dump_json()) for entry in entries)
        if len({_key(entry) for entry in copied}) != len(copied):
            raise ValueError("Duplicate specification version")
        object.__setattr__(self, "_entries", copied)

    def resolve(self, workspace_id: str, scenario: Scenario, specification_revision: str) -> Specification:
        for entry in self._entries:
            if _key(entry) == (workspace_id, scenario, specification_revision):
                return entry
        raise InvalidInput("assessment_specification_unavailable")

    def list_revisions(self, workspace_id: str, scenario: Scenario) -> tuple[str, ...]:
        """Return identifiers only; do not expose rule expressions or another workspace's catalog."""
        return tuple(
            sorted(
                entry.specification_revision
                for entry in self._entries
                if entry.workspace_id == workspace_id and entry.scenario == scenario
            )
        )


def _require_columns(rows: FrozenRows, columns: set[str]) -> None:
    if rows.rows and not columns <= set(rows.columns):
        raise InvalidInput("assessment_columns_missing")


def _scalar(value: SourceValue) -> JsonValue:
    if type(value) is int:
        return str(value)
    if isinstance(value, (Decimal, date, datetime)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    return value


def _variable(value: SourceValue, kind: Literal["decimal", "string", "boolean"]) -> RuleValue:
    if value is None:
        return None
    if kind == "decimal":
        return parse_decimal(value)
    if (kind == "string" and isinstance(value, str)) or (kind == "boolean" and type(value) is bool):
        return value
    raise InvalidInput("assessment_input_invalid")


def _alert(rows: FrozenRows, spec: AlertSpecification, max_output_bytes: int) -> BusinessResult:
    _require_columns(rows, {variable.column for variable in spec.variables})
    if len(rows.rows) * len(spec.rules) > _MAX_ASSESSMENT_ITEMS:
        raise InvalidInput("assessment_size_limit")
    assessments: list[AlertAssessment] = []
    evidence: list[JsonValue] = []
    evidence_bytes = 0
    for index, record in enumerate(rows.records):
        values = {item.name: _variable(record[item.column], item.kind) for item in spec.variables}
        result = evaluate_alerts(spec.rules, values)
        assessments.append(result)
        row = _JSON_OBJECT.validate_json(_ALERT_ASSESSMENT.dump_json(result))
        row["row_index"] = index
        row["inputs"] = {name: _scalar(value) for name, value in result.inputs}
        row["raw_inputs"] = {
            item.name: {
                "column": item.column,
                "type": type(record[item.column]).__name__,
                "value": _scalar(record[item.column]),
            }
            for item in spec.variables
        }
        evidence_bytes += len(canonical_json(row).encode("utf-8"))
        if evidence_bytes > max_output_bytes:
            raise InvalidInput("assessment_output_limit")
        evidence.append(row)
    complete = bool(assessments) and all(item.complete for item in assessments)
    conclusion: Literal["issues", "normal", "no_data", "incomplete"] = (
        "issues"
        if any(item.conclusion == "issues" for item in assessments)
        else "normal"
        if complete
        else "no_data"
        if all(item.conclusion == "no_data" for item in assessments)
        else "incomplete"
    )
    return BusinessResult(scenario="alert", conclusion=conclusion, complete=complete, evidence={"rows": evidence})


def _text(record: Mapping[str, SourceValue], column: str) -> str:
    return require_identifier(record[column], column)


def _measurement(
    run: Run,
    record: Mapping[str, SourceValue],
    identity: QualityIdentityColumns,
    *,
    record_id: str,
    metric: str,
    value: SourceValue,
    unit: str,
) -> Measurement:
    timestamp = record[identity.measured_at]
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    if not isinstance(timestamp, datetime):
        raise InvalidInput("assessment_input_invalid")
    parse_decimal(value)
    raw: MeasurementValue
    if value is None or isinstance(value, (str, Decimal, int)):
        raw = value
    else:
        raise InvalidInput("assessment_input_invalid")
    return Measurement(
        record_id,
        run.spec.device_id,
        _text(record, identity.sample_id),
        _text(record, identity.measurement_revision),
        metric,
        raw,
        unit,
        timestamp,
    )


def _exact_raw_numbers(value: JsonValue) -> JsonValue:
    """Retain integer source magnitudes even when evidence is consumed by JavaScript."""
    if isinstance(value, list):
        return [_exact_raw_numbers(item) for item in value]
    if isinstance(value, dict):
        return {
            key: str(item) if key == "raw_value" and type(item) is int else _exact_raw_numbers(item)
            for key, item in value.items()
        }
    return value


def _quality(run: Run, rows: FrozenRows, spec: QualitySpecification, max_output_bytes: int) -> BusinessResult:
    mapping = spec.mapping
    identity = mapping.identity
    required = {identity.record_id, identity.sample_id, identity.measurement_revision, identity.measured_at}
    if isinstance(mapping, QualityLongMapping):
        required.update((mapping.metric, mapping.value, mapping.unit))
        count = len(rows.rows)
    else:
        required.update(item.value for item in mapping.metrics)
        required.update(item.unit_column for item in mapping.metrics if item.unit_column is not None)
        count = len(rows.rows) * len(mapping.metrics)
    _require_columns(rows, required)
    if count > _MAX_ASSESSMENT_ITEMS:
        raise InvalidInput("assessment_size_limit")
    measurements: list[Measurement] = []
    lineage: dict[str, str] = {}
    source_metrics = {item.source_metric for item in spec.standard.metrics}
    for record in rows.records:
        source_id = _text(record, identity.record_id)
        if isinstance(mapping, QualityLongMapping):
            metric = _text(record, mapping.metric)
            if metric not in source_metrics:
                raise InvalidInput("assessment_unregistered_metric")
            measurements.append(
                _measurement(
                    run,
                    record,
                    identity,
                    record_id=source_id,
                    metric=metric,
                    value=record[mapping.value],
                    unit=_text(record, mapping.unit),
                )
            )
            lineage[source_id] = source_id
        else:
            for item in mapping.metrics:
                record_id = canonical_hash({"source_record_id": source_id, "source_metric": item.metric})
                unit = item.unit if item.unit is not None else _text(record, item.unit_column or "")
                measurements.append(
                    _measurement(
                        run,
                        record,
                        identity,
                        record_id=record_id,
                        metric=item.metric,
                        value=record[item.value],
                        unit=unit,
                    )
                )
                lineage[record_id] = source_id
    snapshot = MeasurementSnapshot(
        run.id,
        rows.source.source_id,
        rows.source.revision,
        rows.captured_at,
        tuple(measurements),
    )
    expected = (
        None
        if spec.expected_samples is None
        else tuple((run.spec.device_id, item.sample_id, item.measurement_revision) for item in spec.expected_samples)
    )
    sample_keys = {(item.device_id, item.sample_id, item.measurement_revision) for item in measurements}
    sample_count = len(sample_keys) if expected is None else len(set(expected) | sample_keys)
    if sample_count * len(spec.standard.metrics) > _MAX_ASSESSMENT_ITEMS:
        raise InvalidInput("assessment_size_limit")
    batch = evaluate_quality(snapshot, spec.standard, spec.conversions, expected_samples=expected)
    evidence = _JSON_OBJECT.validate_json(_BATCH_ASSESSMENT.dump_json(batch, exclude={"samples"}))
    samples: list[JsonValue] = []
    evidence_bytes = 0
    for sample in batch.samples:
        document = _exact_raw_numbers(_JSON_OBJECT.validate_json(_SAMPLE_ASSESSMENT.dump_json(sample)))
        evidence_bytes += len(canonical_json(document).encode("utf-8"))
        if evidence_bytes > max_output_bytes:
            raise InvalidInput("assessment_output_limit")
        samples.append(document)
    evidence["samples"] = samples
    evidence.update(
        {
            "sample_scope": "observed" if expected is None else "declared",
            "pass_rate_numerator": batch.pass_rate_numerator,
            "pass_rate_denominator": batch.pass_rate_denominator,
            "incomplete_samples": batch.incomplete_samples,
            "record_lineage": [{"record_id": key, "source_record_id": value} for key, value in sorted(lineage.items())],
        }
    )
    return BusinessResult(scenario="quality", conclusion=batch.conclusion, complete=batch.complete, evidence=evidence)


class AssessmentService:
    registry: SpecificationRegistry
    max_output_bytes: int

    def __init__(self, registry: SpecificationRegistry, *, max_output_bytes: int = _MAX_ENVELOPE_BYTES) -> None:
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= _MAX_ENVELOPE_BYTES:
            raise ValueError("Assessment output budget must be between 1 byte and 512 KiB")
        self.registry = registry
        self.max_output_bytes = max_output_bytes

    def assess(self, run: Run) -> BusinessEnvelope:
        """Assess only this persisted capture; callers authenticate and load the Run separately."""
        try:
            run = Run.model_validate_json(run.model_dump_json())
            entry = self.registry.resolve(run.workspace_id, run.spec.scenario, run.spec.specification_revision)
            if _key(entry) != (run.workspace_id, run.spec.scenario, run.spec.specification_revision):
                raise AccessDenied("assessment_specification_scope_mismatch")
            spec = _SPECIFICATION.validate_json(entry.model_dump_json())
            rows = restore_capture(run)
            result = (
                _alert(rows, spec, self.max_output_bytes)
                if isinstance(spec, AlertSpecification)
                else _quality(run, rows, spec, self.max_output_bytes)
            )
            result = BusinessResult.model_validate(
                {
                    **result.model_dump(),
                    "evidence": {
                        **result.evidence,
                        "specification_revision": spec.specification_revision,
                        "specification_digest": canonical_hash(spec.model_dump(mode="json")),
                        "source_id": rows.source.source_id,
                        "source_revision": rows.source.revision,
                        "read_id": rows.read_id,
                        "read_revision": rows.read_revision,
                        "captured_at": rows.captured_at.isoformat(),
                        "source_row_count": len(rows.rows),
                    },
                }
            )
            envelope = BusinessEnvelope(
                run_id=run.id,
                device_id=run.spec.device_id,
                specification_revision=run.spec.specification_revision,
                input_snapshot_digest=canonical_hash(run.input_snapshot),
                result=result,
            )
            if len(envelope.model_dump_json().encode("utf-8")) > self.max_output_bytes:
                raise InvalidInput("assessment_output_limit")
            return envelope
        except ValueError:
            raise InvalidInput("assessment_input_invalid") from None
