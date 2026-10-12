"""Strict request contracts for the asset and semantic catalog."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel


Sensitivity = Literal["public", "internal", "confidential", "restricted"]
RecordStatus = Literal["active", "archived"]


def _clean_strings(values: list[str], *, item_limit: int = 200) -> list[str]:
    """清洗字符串列表：去空白、去空项、截断单项长度并限制数量，供标签/同义词/维度等字段复用。"""
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    if any(len(item) > item_limit for item in cleaned):
        raise ValueError(f"单项最多 {item_limit} 个字符")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError("列表中不能有重复项")
    return cleaned


class AssetRef(ApiModel):
    """资产引用：类型 + id + 可选字段名。用于指标来源、关系两端与生成来源的引用。"""
    type: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    id: str = Field(min_length=1, max_length=80)
    field: str = Field(default="", max_length=240, description="可选的表字段路径")


class AnnotationInput(ApiModel):
    """资产标注入参：别名/分类/业务域/描述/标签/负责人/敏感级/新鲜度阈值/字段说明与乐观锁。"""
    aliases: list[str] = Field(default_factory=list, max_length=100)
    category: str = Field(default="", max_length=120)
    business_domain: str = Field(default="", alias="businessDomain", max_length=120)
    description: str = Field(default="", max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    steward_user_id: str | None = Field(default=None, alias="stewardUserId", max_length=26)
    sensitivity: Sensitivity = "internal"
    freshness_seconds: int | None = Field(default=None, alias="freshnessSeconds", ge=60, le=31536000)
    field_descriptions: dict[str, str] = Field(default_factory=dict, alias="fieldDescriptions", max_length=2000)
    status: RecordStatus = "active"
    version: int | None = Field(default=None, ge=1)

    @field_validator("aliases", "tags")
    @classmethod
    def clean_lists(cls, value: list[str]) -> list[str]:
        """清洗标注内的列表字段（别名、标签），限制数量与单项长度。异常：422。"""
        # 清洗列表字段：去空白、去空项、限制单项长度与总条数，避免脏值写库。异常：422。
        #
        return _clean_strings(value)

    @field_validator("field_descriptions")
    @classmethod
    def clean_fields(cls, value: dict[str, str]) -> dict[str, str]:
        """清洗字段说明映射（字段名→说明）：去空白键、限制条数与长度，避免脏键进入检索。异常：422。"""
        result = {}
        for key, description in value.items():
            field = str(key).strip()
            text = str(description).strip()
            if not field or len(field) > 240 or len(text) > 2000:
                raise ValueError("字段路径不能为空且最多 240 字符，字段说明最多 2000 字符")
            result[field] = text
        return result


class BusinessTermInput(ApiModel):
    """业务术语入参：名称、定义、同义词、所属业务域与乐观锁。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    definition: str = Field(min_length=1, max_length=8000)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    business_domain: str = Field(default="", alias="businessDomain", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=100)
    status: RecordStatus = "active"
    version: int | None = Field(default=None, ge=1)

    @field_validator("aliases", "tags")
    @classmethod
    def clean_lists(cls, value: list[str]) -> list[str]:
        """清洗标注的别名与标签列表（复用统一的 _clean_strings 规则）。异常：422。"""
        return _clean_strings(value)


class MetricDefinitionInput(ApiModel):
    """指标定义入参：名称、口径、单位、维度与来源引用及乐观锁。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=8000)
    formula: str = Field(min_length=1, max_length=12000)
    unit: str = Field(default="", max_length=40)
    aggregation: Literal["sum", "avg", "min", "max", "count", "count_distinct", "ratio", "custom"] = "custom"
    granularity: str = Field(default="", max_length=80)
    dimensions: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[AssetRef] = Field(default_factory=list, alias="sourceRefs", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    status: RecordStatus = "active"
    version: int | None = Field(default=None, ge=1)

    @field_validator("dimensions", "tags")
    @classmethod
    def clean_lists(cls, value: list[str]) -> list[str]:
        """清洗指标的维度与标签列表；维度名可能较长，故单项上限放宽到 240。异常：422。"""
        return _clean_strings(value, item_limit=240)

    @model_validator(mode="after")
    def unique_sources(self):
        """校验指标来源引用不重复（同一来源只应登记一次）。异常：422。"""
        keys = [(item.type, item.id, item.field) for item in self.source_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("指标来源不能重复")
        return self


class AssetRelationInput(ApiModel):
    """资产关系入参：源资产、目标资产、关系类型及乐观锁。"""
    source: AssetRef
    target: AssetRef
    relation_kind: Literal["depends_on", "derived_from", "contains", "related_to"] = Field(alias="relationKind")
    description: str = Field(default="", max_length=4000)
    status: RecordStatus = "active"
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def no_self_edge(self):
        """校验关系两端不是同一个资产（禁止自环）。异常：422。"""
        if self.source.type == self.target.type and self.source.id == self.target.id:
            raise ValueError("资源不能与自身建立关系")
        if self.source.field or self.target.field:
            raise ValueError("资源关系不接受字段级端点；字段请登记在指标来源中")
        return self

