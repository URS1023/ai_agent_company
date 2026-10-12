"""Strict contracts for unified document generation and staged PPT authoring."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.assets.schemas import AssetRef


OutputType = Literal["document", "presentation", "spreadsheet", "pdf"]


class GenerationInput(ApiModel):
    """生成记录的新建/更新入参。
    关键约束（model_validator 集中校验）：PPT 必须显式选模板、且模板/页数只能用于 PPT；
    AI 模式必须填生成要求；来源引用 (type,id,field) 三元组不能重复。
    """
    name: str = Field(min_length=1, max_length=240)
    output_type: OutputType = Field(alias="outputType")
    mode: Literal["manual", "ai"] = "manual"
    prompt: str = Field(default="", max_length=20000)
    spec: dict = Field(default_factory=dict)
    template_id: str | None = Field(default=None, alias="templateId", max_length=100)
    page_count: int | None = Field(default=None, alias="pageCount", ge=3, le=50)
    source_refs: list[AssetRef] = Field(default_factory=list, alias="sourceRefs", max_length=50)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def compatible_options(self):
        """跨字段校验：PPT 必选模板、非 PPT 不得带模板或页数、AI 模式必须有要求、来源不得重复。"""
        if self.output_type == "presentation" and not (self.template_id or "").strip():
            raise ValueError("PPT 必须明确选择模板；系统不会套用默认模板")
        if self.output_type != "presentation" and (self.template_id or self.page_count):
            raise ValueError("模板和总页数只适用于 PPT")
        if self.mode == "ai" and not self.prompt.strip():
            raise ValueError("AI 生成必须填写明确要求")
        keys = [(item.type, item.id, item.field) for item in self.source_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("来源不能重复")
        return self


class ExecuteInput(ApiModel):
    """执行生成入参：只带乐观锁 version（执行会改变状态，必须防止并发重跑）。"""
    version: int = Field(ge=1)


class CopyInput(ApiModel):
    """复制生成记录入参：新名称（留空则沿用原名）。"""
    name: str = Field(default="", max_length=240)


class PresentationPlanInput(ApiModel):
    """分阶段编写第一步入参：整套标题信息 + 正文页大纲（1~48 页）。"""
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=160)
    footer: str = Field(default="", max_length=160)
    closing_title: str = Field(default="谢谢", alias="closingTitle", max_length=160)
    closing_subtitle: str = Field(default="期待进一步交流", alias="closingSubtitle", max_length=300)
    slides: list[dict] = Field(min_length=1, max_length=48)

    @field_validator("slides")
    @classmethod
    def valid_outline(cls, value: list[dict]) -> list[dict]:
        """逐页校验大纲：每页必须是对象且有非空标题，报错带页码便于定位。"""
        for index, item in enumerate(value, 1):
            if not isinstance(item, dict) or not str(item.get("title") or "").strip():
                raise ValueError(f"第 {index} 个正文页缺少标题")
        return value


class PresentationSlideBatchInput(ApiModel):
    """分阶段编写第二步入参：一批正文页内容（1~4 页，限制单批规模以控制模型与渲染开销）。"""
    version: int = Field(ge=1)
    slides: list[dict] = Field(min_length=1, max_length=4)
