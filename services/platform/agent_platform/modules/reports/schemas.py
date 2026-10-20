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


class GenerationInput(Input):
    name: str = Field(min_length=1, max_length=160)
    template_id: str
    bindings: dict[str, str] = Field(default_factory=dict)


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
