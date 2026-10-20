"""报表编辑、版本恢复和真实导出入参。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportInput(Input):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    template_id: str | None = None
    layout: dict | None = None


class ReportPatch(Input):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    layout: dict | None = None


class RevisionInput(Input):
    revision: int = Field(ge=1)


class RestoreInput(RevisionInput):
    target_revision: int = Field(ge=1)


class ExportInput(Input):
    format: Literal["html", "csv", "json", "pdf", "png", "docx", "xlsx", "pptx"] = "html"
    revision: int | None = Field(default=None, ge=1)


class DrilldownInput(Input):
    widget_id: str
    filters: dict = Field(default_factory=dict)


class GenerationQueryBinding(Input):
    """模板换数保留显式字段映射，不能混入检验来源或客户端业务事实。"""
    query_id: str = Field(min_length=1, max_length=100)
    label_field: str | None = Field(default=None, min_length=1, max_length=200)
    value_field: str | None = Field(default=None, min_length=1, max_length=200)
    value_fields: list[str] | None = Field(default=None, min_length=1, max_length=8)
    secondary_value_fields: list[str] | None = Field(default=None, max_length=7)
    fields: list[str] | None = Field(default=None, min_length=1, max_length=20)
    description_field: str | None = Field(default=None, min_length=1, max_length=200)
    time_field: str | None = Field(default=None, min_length=1, max_length=200)
    unit_field: str | None = Field(default=None, min_length=1, max_length=200)
    total_field: str | None = Field(default=None, min_length=1, max_length=200)


class GenerationInput(Input):
    name: str = Field(min_length=1, max_length=160)
    template_id: str
    bindings: dict[str, str | GenerationQueryBinding] = Field(default_factory=dict)


class InspectionReportGenerationInput(Input):
    """报告只接收检验引用，业务事实必须在服务端读取。"""
    name: str = Field(min_length=1, max_length=160)
    inspection_id: str = Field(min_length=1, max_length=100)
    run_id: str = Field(min_length=1, max_length=100)
    inspection_revision: int = Field(ge=1)


class ComponentInstanceInput(Input):
    source_id: str = Field(min_length=1, max_length=500)
    id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    x: float = Field(default=0, ge=0, le=10000)
    y: float = Field(default=0, ge=0, le=10000)
    order_num: int = Field(default=1, ge=0, le=10000)
