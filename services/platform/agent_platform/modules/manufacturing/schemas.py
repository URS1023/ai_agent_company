"""制造领域入参；精确测量值保留小数字符串，历史标准按版本固定。"""
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


def decimal_text(value: str) -> str:
    """拒绝非有限值和过大指数，不隐式舍入实测证据。"""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("请输入有效的小数字符串") from exc
    if not number.is_finite() or len(number.as_tuple().digits) > 100 or abs(number.adjusted()) > 100:
        raise ValueError("数值须有限，且不超过100位有效数字或指数")
    return value


class ResourceInput(Input):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=2000)
    status: Literal["active", "inactive"] = "active"
    revision: int | None = Field(default=None, ge=1)


class DeviceInput(ResourceInput):
    device_type: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=160)


class ProductInput(ResourceInput):
    specification: str = Field(default="", max_length=300)


class PointInput(ResourceInput):
    device_id: str
    unit: str = Field(default="", max_length=40)
    datasource_id: str | None = None
    mapping: dict = Field(default_factory=dict)


class Metric(Input):
    """提取自Test5 quality.schemas.MetricInput的判定字段。"""
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    category: Literal["numeric", "component", "appearance"] = "numeric"
    unit: str = Field(default="", max_length=40)
    standard_min: str | None = None
    standard_max: str | None = None
    component_name: str = Field(default="", max_length=120)
    minimum_confidence: str = "0.8"
    blocking_severities: list[Literal["minor", "moderate", "severe"]] = Field(default_factory=lambda: ["moderate", "severe"])

    @field_validator("standard_min", "standard_max", "minimum_confidence")
    @classmethod
    def finite(cls, value):
        return decimal_text(value) if value is not None else None

    @model_validator(mode="after")
    def validate_standard(self):
        if not 0 <= Decimal(self.minimum_confidence) <= 1:
            raise ValueError("最低置信度须为0到1")
        if self.standard_min is not None and self.standard_max is not None and Decimal(self.standard_min) > Decimal(self.standard_max):
            raise ValueError("标准下限不得高于上限")
        if self.category != "appearance" and self.standard_min is None and self.standard_max is None:
            raise ValueError("数值或成分指标至少填写一侧标准")
        if self.category == "appearance" and (self.standard_min is not None or self.standard_max is not None):
            raise ValueError("外观指标不接受数值范围")
        if self.category == "component" and not self.component_name:
            raise ValueError("成分指标必须指定成分名称")
        return self


class TemplateInput(ResourceInput):
    target_type: Literal["device", "product"]
    metrics: list[Metric] = Field(min_length=1, max_length=100)

    @field_validator("metrics")
    @classmethod
    def unique_metrics(cls, values):
        if len({v.id for v in values}) != len(values):
            raise ValueError("指标标识不能重复")
        return values


class BatchInput(Input):
    name: str = Field(min_length=1, max_length=160)
    product_id: str
    external_ref: str = Field(default="", max_length=300)
    quantity: int | None = Field(default=None, ge=1)


class InspectionInput(Input):
    name: str = Field(min_length=1, max_length=160)
    target_type: Literal["device", "product"]
    template_id: str
    template_revision: int = Field(ge=1)
    device_id: str | None = None
    product_id: str | None = None
    batch_id: str | None = None
    expected_samples: int = Field(default=1, ge=1, le=10000)


class SampleInput(Input):
    external_ref: str = Field(min_length=1, max_length=160)
    metadata: dict = Field(default_factory=dict)


class SamplePatch(SampleInput):
    revision: int = Field(ge=1)


class InspectionPatch(Input):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    expected_samples: int = Field(ge=1, le=10000)


class Defect(Input):
    defect_type: str = Field(min_length=1, max_length=160)
    confidence: str
    severity: Literal["minor", "moderate", "severe", "review"]
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, value):
        decimal_text(value)
        if not 0 <= Decimal(value) <= 1:
            raise ValueError("置信度须为0到1")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_range(cls, value):
        if value and (any(not 0 <= n <= 1 for n in value) or value[0] + value[2] > 1 or value[1] + value[3] > 1):
            raise ValueError("缺陷框须为有效归一化坐标")
        return value


class Observation(Input):
    metric_id: str
    value: str | None = None
    unit: str = Field(default="", max_length=40)
    image_ref: str = Field(default="", max_length=1000)
    defects: list[Defect] | None = Field(default=None, max_length=300)
    note: str = Field(default="", max_length=2000)

    @field_validator("value")
    @classmethod
    def finite(cls, value):
        return decimal_text(value) if value is not None else None


class ResultInput(Input):
    revision: int = Field(ge=1)
    sample_id: str
    observations: list[Observation] = Field(max_length=100)
    query_id: str | None = None


class SourceMapping(Input):
    metric_id: str
    value_field: str | None = None
    unit_field: str | None = None
    unit: str = ""
    image_ref_field: str | None = None
    defects_field: str | None = None


class InspectionImport(Input):
    revision: int = Field(ge=1)
    query_id: str
    sample_field: str = "sample_id"
    mappings: list[SourceMapping] = Field(min_length=1, max_length=100)


class RevisionInput(Input):
    revision: int = Field(ge=1)


class ReviewInput(RevisionInput):
    outcome: Literal["passed", "failed", "review"]
    note: str = Field(min_length=5, max_length=3000)


class AnalysisInput(Input):
    device_id: str
    name: str = Field(default="维护分析", max_length=160)
    datasource_id: str | None = None
    sql: str | None = None
    parameters: dict = Field(default_factory=dict)
    rows: list[dict] | None = Field(default=None, max_length=10000)
    value_field: str = "value"
    time_field: str = "timestamp"
    load_field: str = "load"
    period_field: str = "period"
    bin_width: float = Field(default=10, gt=0)
    min_samples: int = Field(default=5, ge=2, le=1000)
    minimum_coverage: float = Field(default=0.8, gt=0, le=1)
    change_threshold: float = Field(default=0.15, ge=0)
    change_direction: Literal["increase", "decrease", "either"] = "increase"
    condition_fields: list[str] = Field(default_factory=list, max_length=10)
    expected_interval_seconds: int | None = Field(default=None, ge=1)


class AlertRuleInput(ResourceInput):
    device_id: str
    point_id: str | None = None
    operator: Literal["gt", "gte", "lt", "lte"] = "gt"
    threshold: str
    unit: str = ""
    dedupe_minutes: int = Field(default=60, ge=1, le=10080)

    @field_validator("threshold")
    @classmethod
    def finite(cls, value):
        return decimal_text(value)


class AlertEvaluation(Input):
    value: str
    unit: str
    observed_at: str
    create_incident: bool = False

    @field_validator("value")
    @classmethod
    def finite(cls, value):
        return decimal_text(value)


class SourceAlertEvaluation(Input):
    query_id: str
    value_field: str
    time_field: str
    unit_field: str | None = None
    unit: str | None = None
    create_incident: bool = True


class IncidentAction(RevisionInput):
    action: Literal["acknowledge", "resolve", "reopen", "comment"]
    note: str = Field(min_length=3, max_length=3000)


class SimulationInput(Input):
    name: str = Field(default="食品设备合成数据", min_length=1, max_length=160)
    start_at: str
    steps: int = Field(default=24, ge=1, le=120)
    interval_seconds: int = Field(default=300, ge=60, le=3600)
    compute_budget_seconds: float = Field(default=5, ge=1, le=15)


class SimulationAdvance(RevisionInput):
    steps: int = Field(default=10, ge=1, le=10)
