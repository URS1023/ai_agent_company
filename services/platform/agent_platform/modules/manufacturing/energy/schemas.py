"""节能分析映射明确区分期间电量、平均功率和累计表，不接受客户端结论。"""
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..schemas import Input, decimal_text


class EnergyInput(Input):
    name: str = Field(default="节能分析", min_length=1, max_length=160)
    device_id: str
    query_id: str
    time_field: str = "timestamp"
    period_field: str = "period"
    baseline_value: str = Field(default="baseline", min_length=1, max_length=100)
    recent_value: str = Field(default="recent", min_length=1, max_length=100)
    energy_field: str = "energy"
    energy_mode: Literal["interval", "power", "cumulative"] = "interval"
    energy_unit: Literal["kWh", "Wh", "MWh", "kW", "W"] = "kWh"
    energy_unit_field: str | None = None
    production_field: str = "production"
    production_unit: str = Field(default="件", min_length=1, max_length=40)
    production_unit_field: str | None = None
    duration_field: str | None = None
    duration_unit: Literal["h", "min", "s"] = "h"
    price_field: str | None = None
    load_field: str = "load"
    load_bin_width: str = "10"
    condition_fields: list[str] = Field(default_factory=list, max_length=10)
    device_field: str | None = None
    device_value: str | None = Field(default=None, max_length=200)
    min_samples: int = Field(default=5, ge=2, le=1000)
    minimum_coverage: str = "0.8"
    expected_interval_seconds: int | None = Field(default=None, ge=1)
    planned_hours: str | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @field_validator("load_bin_width", "minimum_coverage", "planned_hours")
    @classmethod
    def precise_positive(cls, value):
        if value is None:
            return value
        decimal_text(value)
        if Decimal(value) <= 0:
            raise ValueError("请输入大于0的数值")
        return value

    @model_validator(mode="after")
    def compatible_fields(self):
        if (self.energy_mode == "power") != (self.energy_unit in ("kW", "W")):
            raise ValueError("平均功率使用kW/W，电量使用kWh/Wh/MWh")
        if self.energy_mode == "power" and not self.duration_field:
            raise ValueError("平均功率分析必须选择同期运行时长字段")
        if Decimal(self.minimum_coverage) > 1:
            raise ValueError("覆盖率不能大于1")
        if self.baseline_value == self.recent_value:
            raise ValueError("基准期和近期标记不能相同")
        if self.planned_hours and Decimal(self.planned_hours) > 8784:
            raise ValueError("计划运行时长不能超过8784小时")
        if bool(self.device_field) != bool(self.device_value):
            raise ValueError("设备标识字段和原始编号需要同时填写")
        critical = [self.time_field, self.period_field, self.energy_field, self.production_field, self.load_field]
        if len(set(critical)) != len(critical) or len(set(self.condition_fields)) != len(self.condition_fields):
            raise ValueError("核心字段和工况字段不能重复")
        if set(self.condition_fields) & set(critical):
            raise ValueError("工况字段应选择配方、阶段等独立字段")
        if any(not name.strip() or len(name) > 200 for name in critical + self.condition_fields):
            raise ValueError("字段名称不能为空或超过200字符")
        return self


class EnergyPatch(Input):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
