"""Strict request contracts for ontology, extraction, graph and Bot operations."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from app.common.contracts import ApiModel


ScalarType = Literal["string", "integer", "number", "boolean", "date", "datetime", "json"]


def property_value_key(expected: str, value):
    """验证属性原生值并返回比较键；不改写用户值或固定修订。

    日期只接受 YYYY-MM-DD；时间要求 T 和时分，可带秒、六位以内小数及 Z/偏移。
    无时区时间按本地墙钟值比较；有时区时间按同一时刻比较。数值排除布尔和非有限数。
    """
    if expected == "string" and isinstance(value, str):
        return value
    if expected == "integer" and type(value) is int:
        return value
    if expected == "number" and type(value) in (int, float):
        if type(value) is int or math.isfinite(value):
            return Decimal(str(value))
    if expected == "boolean" and type(value) is bool:
        return value
    if expected == "date" and isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if expected == "datetime" and isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?", value,
    ):
        return datetime.fromisoformat(value)
    if expected == "json" and isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    raise ValueError(f"属性值必须为合法的 {expected}")


def normalize_property_enums(expected: str, values: list) -> list:
    """按声明类型解释枚举，兼容历史字符串标量，拒绝歧义和无效定义。"""
    cleaned, keys = [], []
    for value in values:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            # 旧定义只允许字符串枚举；严格解释数值/布尔，不对业务属性做隐式转换。
            if expected == "integer" and re.fullmatch(r"-?(?:0|[1-9]\d*)", value):
                value = int(value)
            elif expected == "number" and re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
                # 先读十进制原值，避免大整值带 .0 或指数时被浮点舍入成另一个枚举。
                try:
                    exact = Decimal(value)
                    approximate = float(exact)
                except (InvalidOperation, OverflowError) as exc:
                    raise ValueError("数值枚举超出支持范围") from exc
                if not math.isfinite(approximate):
                    raise ValueError("数值枚举必须是有限数")
                if exact == exact.to_integral_value():
                    value = int(exact)
                elif Decimal(str(approximate)) == exact:
                    value = approximate
                else:
                    raise ValueError("数值枚举的十进制精度无法无损表示")
            elif expected == "boolean" and value in {"true", "false"}:
                value = value == "true"
        key = property_value_key(expected, value)
        if key in keys:
            raise ValueError("枚举值不能重复")
        cleaned.append(value)
        keys.append(key)
    return cleaned


class AttributeDefinition(ApiModel):
    """实体的一个属性定义：名称、类型、是否必填、枚举取值等，是实体属性校验的唯一依据。"""
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="属性稳定字段名")
    label: str = Field(min_length=1, max_length=160, description="界面显示名称")
    type: ScalarType = "string"
    required: bool = False
    unique: bool = False
    description: str = Field(default="", max_length=1000)
    enum_values: list[StrictStr | StrictInt | StrictFloat | StrictBool] = Field(
        default_factory=list, alias="enumValues", max_length=200,
    )

    @model_validator(mode="after")
    def clean_enum(self):
        """按字段类型校验并规整枚举；新定义保存原生标量，历史快照仅在读取时解释。"""
        self.enum_values = normalize_property_enums(self.type, self.enum_values)
        return self


class OntologyInput(ApiModel):
    """本体新建/更新入参（名称 + 描述 + 标签）。"""
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    version: int | None = Field(default=None, ge=1, description="修改时加载到的乐观锁版本")


class ConceptInput(ApiModel):
    """本体概念入参：名称、别名、业务键与属性定义列表。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    color: str = Field(default="#3b73d1", pattern=r"^#[0-9A-Fa-f]{6}$")
    icon: str = Field(default="entity", min_length=1, max_length=80)
    attributes: list[AttributeDefinition] = Field(default_factory=list, max_length=200)
    recognition_terms: list[str] = Field(default_factory=list, alias="recognitionTerms", max_length=200)
    constraints: dict = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)

    @field_validator("recognition_terms")
    @classmethod
    def clean_terms(cls, value: list[str]) -> list[str]:
        """规整术语/别名列表：去空白、去重、限制单项长度，供检索匹配时消费。"""
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if any(len(item) > 300 for item in cleaned):
            raise ValueError("单个识别词最多 300 个字符")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("识别词不能重复")
        return cleaned

    @model_validator(mode="after")
    def unique_attributes(self):
        """校验属性定义不重名（重名会让实体属性校验出现歧义）。异常：422。"""
        names = [item.name for item in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("概念属性字段名不能重复")
        business_key = self.constraints.get("businessKey") or self.constraints.get("business_key")
        if business_key and business_key not in names and business_key != "name":
            raise ValueError("业务键必须是已定义属性或 name")
        return self


class RelationTypeInput(ApiModel):
    """本体关系类型入参：源/目标概念 + 名称 + 基数约束。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    source_concept_id: str = Field(alias="sourceConceptId", min_length=1, max_length=26)
    target_concept_id: str = Field(alias="targetConceptId", min_length=1, max_length=26)
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_many"
    directed: bool = True
    attributes: list[AttributeDefinition] = Field(default_factory=list, max_length=100)
    constraints: dict = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def unique_attributes(self):
        """关系属性同样禁止重名，避免后一个定义静默覆盖前一个。"""
        names = [item.name for item in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("关系属性字段名不能重复")
        return self


class PublishInput(ApiModel):
    """发布类操作的通用入参：只带乐观锁 version 与发布说明（reason）。"""
    version: int = Field(ge=1)
    reason: str = Field(default="发布正式版本", min_length=1, max_length=300)


class StructuredEntityMapping(ApiModel):
    """结构化抽取的实体映射：把源记录字段映射到概念/业务键/属性。"""
    concept_id: str = Field(alias="conceptId", min_length=1, max_length=26)
    key_field: str = Field(alias="keyField", min_length=1, max_length=160)
    name_field: str = Field(alias="nameField", min_length=1, max_length=160)
    property_fields: dict[str, str] = Field(default_factory=dict, alias="propertyFields", max_length=200)


class StructuredRelationMapping(ApiModel):
    """结构化抽取的关系映射：把源记录字段映射到关系类型与其两端业务键。"""
    relation_type_id: str = Field(alias="relationTypeId", min_length=1, max_length=26)
    source_key_field: str = Field(alias="sourceKeyField", min_length=1, max_length=160)
    target_key_field: str = Field(alias="targetKeyField", min_length=1, max_length=160)
    source_concept_id: str = Field(alias="sourceConceptId", min_length=1, max_length=26)
    target_concept_id: str = Field(alias="targetConceptId", min_length=1, max_length=26)
    property_fields: dict[str, str] = Field(default_factory=dict, alias="propertyFields", max_length=100)


class ExtractionMappings(ApiModel):
    """抽取任务的完整映射配置（实体映射 + 关系映射）。"""
    entity_mappings: list[StructuredEntityMapping] = Field(default_factory=list, alias="entityMappings", max_length=100)
    relation_mappings: list[StructuredRelationMapping] = Field(default_factory=list, alias="relationMappings", max_length=100)


class ExtractionJobInput(ApiModel):
    """抽取任务入参：名称、抽取模式、来源知识库与文档、映射配置。"""
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    mode: Literal["structured", "unstructured"]
    ontology_id: str = Field(alias="ontologyId", min_length=1, max_length=26)
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId", max_length=26)
    document_ids: list[str] = Field(default_factory=list, alias="documentIds", max_length=500)
    mappings: ExtractionMappings = Field(default_factory=ExtractionMappings)
    records: list[dict] = Field(default_factory=list, max_length=1000)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source(self):
        """校验抽取来源必填：至少要指定知识库（文档可留空表示整库）。异常：422。"""
        if self.mode == "structured":
            if not self.records:
                raise ValueError("结构化任务至少需要一条输入记录")
            if not self.mappings.entity_mappings:
                raise ValueError("结构化任务至少需要一个实体字段映射")
            if self.knowledge_base_id or self.document_ids:
                raise ValueError("结构化任务不能同时配置知识库文档来源")
        else:
            if not self.knowledge_base_id:
                raise ValueError("非结构化任务必须选择知识库")
            if self.records:
                raise ValueError("非结构化任务从已解析知识文档读取，不能提交内联记录")
        return self


class ExtractionExecuteInput(ApiModel):
    """执行抽取的入参（可覆盖部分运行参数，如条数上限）。"""
    version: int = Field(ge=1, description="任务当前版本")


class CandidateReviewItem(ApiModel):
    """单条候选的审核入参：候选 id、是否通过、以及审核时允许修正的业务键与属性。"""
    candidate_id: str = Field(alias="candidateId", min_length=1, max_length=26)
    version: int = Field(ge=1)
    decision: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)
    payload: dict | None = Field(
        default=None,
        description="复核时可修正候选名称、业务键、属性或关系端点；不提交则保留抽取原值",
    )


class CandidateBatchReview(ApiModel):
    """批量审核入参：一次提交多条审核结论。"""
    items: list[CandidateReviewItem] = Field(min_length=1, max_length=500)


class GraphInput(ApiModel):
    """图谱新建/更新入参：名称、描述与绑定的本体（含本体修订号）。"""
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    ontology_id: str = Field(alias="ontologyId", min_length=1, max_length=26)
    version: int | None = Field(default=None, ge=1)


class GraphEntityInput(ApiModel):
    """图谱实体入参：概念、业务键、名称、属性值与乐观锁 version。"""
    concept_id: str = Field(alias="conceptId", min_length=1, max_length=26)
    business_key: str = Field(alias="businessKey", min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=300)
    properties: dict = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class GraphRelationInput(ApiModel):
    """图谱关系入参：关系类型、源/目标实体与乐观锁 version。"""
    relation_type_id: str = Field(alias="relationTypeId", min_length=1, max_length=26)
    source_entity_id: str = Field(alias="sourceEntityId", min_length=1, max_length=26)
    target_entity_id: str = Field(alias="targetEntityId", min_length=1, max_length=26)
    properties: dict = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class ExtractionMaterializeInput(ApiModel):
    """物化入参：指定把审核通过的候选写入哪个目标图谱（可选本体修订）。"""
    graph_id: str = Field(alias="graphId", min_length=1, max_length=26)
    graph_version: int = Field(alias="graphVersion", ge=1)
    run_id: str = Field(alias="runId", min_length=1, max_length=26)


class BotInput(ApiModel):
    """Bot 新建/更新入参：类型、模型配置、指令、欢迎语与五类绑定 id 列表。"""
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    bot_type: Literal["agent", "knowledge", "chatflow"] = Field(default="agent", alias="botType")
    instructions: str = Field(default="", max_length=20000)
    model_profile: str = Field(default="chat", alias="modelProfile", min_length=1, max_length=80)
    knowledge_base_ids: list[str] = Field(default_factory=list, alias="knowledgeBaseIds", max_length=100)
    graph_ids: list[str] = Field(default_factory=list, alias="graphIds", max_length=100)
    workflow_ids: list[str] = Field(default_factory=list, alias="workflowIds", max_length=100)
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds", max_length=100)
    tool_ids: list[str] = Field(default_factory=list, alias="toolIds", max_length=100)
    welcome_message: str = Field(default="", alias="welcomeMessage", max_length=1000)
    api_enabled: bool = Field(default=False, alias="apiEnabled")
    version: int | None = Field(default=None, ge=1)


class BotDebugInput(ApiModel):
    """Bot 调试入参：问题、回答模式（模型回答/证据清单）与是否使用正式版本。"""
    query: str = Field(min_length=1, max_length=8000)
    mode: Literal["retrieval", "model"] = "retrieval"
    use_published: bool = Field(default=False, alias="usePublished", description="调试正式修订而非当前草稿")


class BotApiKeyInput(ApiModel):
    """Bot API Key 创建入参：名称与可选过期时间。"""
    name: str = Field(min_length=1, max_length=160)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")


class PublicBotInvokeInput(ApiModel):
    """公开调用入参：问题与回答模式。API Key 走请求头而非请求体，避免出现在日志与 URL 里。"""
    query: str = Field(min_length=1, max_length=8000)
    mode: Literal["retrieval", "model"] = "retrieval"
