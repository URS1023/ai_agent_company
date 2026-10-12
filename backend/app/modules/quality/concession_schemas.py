"""Typed contracts for configurable incoming-quality concessions.

职责：让步（特采）业务的请求/响应契约 —— 规则定义、可追溯来源、实测证据与审批请求。

位置与依赖：与 ``quality.concessions`` 配对使用（服务层负责授权、匹配与状态机）；
数值校验复用 ``quality.schemas.decimal_text``，保证全模块精度口径一致。

约束：
- 规则必须**显式到可审计**，不允许隐含默认值：每条规则只绑定一个指标（多指标请建多条规则）、
  至少匹配一个标准界限，扣量值必须 > 0，比例模式 ≤ 1；
- 舍入要么"精确"（不配小数位），要么明确给出方式 + 小数位，禁止两者混用；
- 来源必须可追溯（``ConcessionSource``），字段映射不得含空白，且来源类型与标识不得混用；
- 所有精确数值都是字符串（不经过 float），审批意见必填。
"""
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.quality.schemas import decimal_text


class ConcessionRuleInput(ApiModel):
    """A rule is explicit enough to be audited and cannot encode a hidden default.

    （中文补充）让步规则入参。关键约束（``valid_rule`` 强制）：
    每条规则只绑定**一个**指标（单位/标准不同请分别建规则）、至少配置一个标准界限、
    扣量值必须 > 0、比例模式扣量 ≤ 1；
    ``roundingMode='exact'`` 时不得配小数位，反之必须配；
    偏差上下限与标准上下限各自不得倒置。``productCodes`` 为空表示**明确配置的全产品范围**
    （不是"未配置"）。
    """

    version: int | None = Field(default=None, ge=1)
    code: str = Field(min_length=1, max_length=80, description="业务规则编码")
    name: str = Field(min_length=1, max_length=160, description="规则名称")
    productCodes: list[str] = Field(default_factory=list, max_length=200, description="适用产品编码；空表示全产品范围")
    metricIds: list[str] = Field(min_length=1, max_length=50, description="必须同时出现且不合格的质检指标")
    weightUnit: str = Field(min_length=1, max_length=40, description="原始重量单位")
    metricUnit: str = Field(min_length=1, max_length=40, description="单个实测指标单位；多个指标需分别建规则")
    standardMin: str | None = Field(default=None, max_length=120, description="可选精确标准下限匹配")
    standardMax: str | None = Field(default=None, max_length=120, description="可选精确标准上限匹配")
    minDeviation: str | None = Field(default=None, max_length=120, description="可选实测偏差下限")
    maxDeviation: str | None = Field(default=None, max_length=120, description="可选实测偏差上限")
    deductionMode: Literal["rate", "weight"] = Field(description="按比例或固定重量扣量")
    deductionValue: str = Field(max_length=120, description="精确扣量比例或重量")
    roundingScale: int | None = Field(default=None, ge=0, le=12, description="可选结算重量小数位")
    roundingMode: Literal["exact", "half_up", "half_even", "down", "up"] = Field(default="exact")
    status: Literal["active", "inactive"] = Field(default="active")
    description: str = Field(default="", max_length=3000)

    @field_validator("code", "name", "weightUnit", "metricUnit")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("规则编码、名称和单位不能是空白")
        return value

    @field_validator("productCodes", "metricIds")
    @classmethod
    def distinct_nonblank(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("规则匹配项不能重复或为空白")
        return cleaned

    @field_validator("standardMin", "standardMax", "minDeviation", "maxDeviation", "deductionValue")
    @classmethod
    def finite_decimal(cls, value: str | None) -> str | None:
        return decimal_text(value.strip()) if value is not None else None

    @model_validator(mode="after")
    def valid_rule(self):
        if self.standardMin is None and self.standardMax is None:
            raise ValueError("规则至少要匹配一个标准界限")
        if len(self.metricIds) != 1:
            raise ValueError("每条让步规则只绑定一个指标；不同单位或标准请分别配置规则")
        if self.standardMin is not None and self.standardMax is not None and Decimal(self.standardMin) > Decimal(self.standardMax):
            raise ValueError("标准下限不得高于上限")
        if self.minDeviation is not None and self.maxDeviation is not None and Decimal(self.minDeviation) > Decimal(self.maxDeviation):
            raise ValueError("偏差下限不得高于上限")
        value = Decimal(self.deductionValue)
        if value <= 0:
            raise ValueError("扣量值必须大于 0")
        if self.deductionMode == "rate" and value > 1:
            raise ValueError("扣量比例必须介于 0 和 1；请使用精确小数配置")
        if self.roundingMode == "exact" and self.roundingScale is not None:
            raise ValueError("精确模式不能配置小数位；请明确选择舍入方式")
        if self.roundingMode != "exact" and self.roundingScale is None:
            raise ValueError("选择舍入方式时必须配置小数位")
        return self


class ConcessionRuleView(ConcessionRuleInput):
    """规则的输出视图：在入参之上补 id 与时间戳（``version`` 为必填的乐观锁版本）。"""
    id: str
    version: int
    createTime: str
    updateTime: str


class ConcessionSource(ApiModel):
    """A traceable source reference; values are never fetched from an arbitrary URL."""

    kind: Literal["datasource", "workflow", "inspection", "manual"]
    recordId: str = Field(min_length=1, max_length=200)
    datasourceId: str | None = Field(default=None, max_length=26)
    workflowId: str | None = Field(default=None, max_length=26)
    fields: dict[str, str] = Field(min_length=1, max_length=50, description="原始字段名到字段路径的映射")
    capturedAt: str | None = Field(default=None, max_length=80)

    @field_validator("recordId")
    @classmethod
    def record_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("来源记录标识不能是空白")
        return value.strip()

    @model_validator(mode="after")
    def source_reference(self):
        if self.kind == "datasource" and not self.datasourceId:
            raise ValueError("数据源来源必须提供 datasourceId")
        if self.kind == "workflow" and not self.workflowId:
            raise ValueError("工作流来源必须提供 workflowId")
        if self.kind == "inspection" and (self.datasourceId or self.workflowId):
            raise ValueError("质检记录来源不能混用数据源或工作流标识")
        if any(not key.strip() or not value.strip() for key, value in self.fields.items()):
            raise ValueError("来源字段映射不能包含空白")
        return self


class ConcessionSourceField(ApiModel):
    """来源中的一个**可选字段路径**（下拉选项用）：键名、JSONPath 与推断出的类型。

    只描述"可以取什么"，不含任何实际值 —— 执行结果的值不随选项外传。
    """
    key: str
    path: str
    dataType: Literal["number", "text", "boolean", "empty"]


class ConcessionSourceOption(ApiModel):
    """一条可选来源（工作流执行或质检记录），供申请页选择并声明字段路径。

    ``fields`` 最多 120 个路径；``hasOriginalWeight`` 决定能否自动计算结算重量；
    ``measurementCount`` 是可用实测项数量（工作流来源为路径数，质检来源为结果条数）。
    """
    kind: Literal["workflow", "inspection"]
    recordId: str
    workflowId: str | None = None
    inspectionId: str | None = None
    sampleId: str | None = None
    label: str
    status: str
    fields: list[ConcessionSourceField] = Field(default_factory=list, max_length=120)
    hasOriginalWeight: bool = False
    measurementCount: int = 0


class ConcessionSourcePage(ApiModel):
    """来源选项分页：``total`` 为已授权总数，**未扫描到末页时为 None**（表示未知，不是 0）。"""
    items: list[ConcessionSourceOption]
    total: int | None = Field(default=None, ge=0, description="未扫描到末页时总数未知")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    hasMore: bool


class ConcessionMeasurement(ApiModel):
    """一条实测值 + 其当时的标准上下限与来源字段路径。

    ``sourceField`` 必填：没有来源路径的数值不允许进入审批（见 ``concessions.create_request``）；
    ``value`` / 标准值都必须是精确小数字符串。
    """
    metricId: str = Field(min_length=1, max_length=26)
    value: str | None = Field(default=None, max_length=120, description="实际测量值")
    unit: str = Field(min_length=1, max_length=40)
    standardMin: str | None = Field(default=None, max_length=120)
    standardMax: str | None = Field(default=None, max_length=120)
    sourceField: str = Field(min_length=1, max_length=200, description="实测值原始字段路径")

    @field_validator("value", "standardMin", "standardMax")
    @classmethod
    def measurement_decimal(cls, value: str | None) -> str | None:
        return decimal_text(value.strip()) if value is not None else None


class ConcessionEvidenceInput(ApiModel):
    """让步申请入参：来源声明 + 原始重量 + 实测值（+ 可选指定规则）。

    ``ruleId`` 为空时由服务端按证据自动匹配唯一规则；``requestKey`` 用于幂等
    （同键同证据复用既有申请）。注意：提交的实测值与重量只用于**与来源记录交叉核对**，
    最终以来源记录（已保存执行结果/质检快照）为准。
    """
    requestKey: str = Field(min_length=8, max_length=160)
    sampleId: str = Field(min_length=1, max_length=160)
    productCode: str = Field(default="", max_length=160)
    inspectionId: str | None = Field(default=None, max_length=26)
    ruleId: str | None = Field(default=None, max_length=26)
    source: ConcessionSource
    originalWeight: str | None = Field(default=None, max_length=120, description="实际原始重量")
    weightUnit: str = Field(min_length=1, max_length=40)
    measurements: list[ConcessionMeasurement] = Field(default_factory=list, max_length=100)

    @field_validator("requestKey", "sampleId", "weightUnit")
    @classmethod
    def evidence_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("标识和重量单位不能是空白")
        return value

    @field_validator("originalWeight")
    @classmethod
    def weight_decimal(cls, value: str | None) -> str | None:
        return decimal_text(value.strip()) if value is not None else None


class ConcessionDecisionInput(ApiModel):
    """审批入参：明确的版本 + approved/rejected + **必填**审批意见。

    ``version`` 必须等于读到的申请版本（过期 409，防止覆盖他人决策）；
    驳回也需要写明理由（``decision_note`` 非空白）。
    """
    version: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=3000)

    @field_validator("note")
    @classmethod
    def decision_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("审批意见不能为空")
        return value


class ConcessionRequestView(ApiModel):
    """让步申请输出视图：证据 + 规则快照 + 候选值 + 最终值 + 审批信息。

    状态取值（``status``）：``needs_confirmation``（证据不足/规则不唯一，需人工确认）、
    ``pending``（唯一匹配，等待审批）、``approved`` / ``rejected``（已决策）。
    ``proposedSettlementWeight`` 只是候选值；**只有 ``settlementWeight``（批准后）可用于结算**。
    ``decidedAt`` 为 UTC 时间字符串，展示时按用户时区换算。
    """
    id: str
    version: int
    requestKey: str
    inspectionId: str | None
    ruleId: str | None
    ruleVersion: int | None
    ruleSnapshot: dict
    sampleId: str
    productCode: str
    weightUnit: str
    source: dict
    measurements: list[dict]
    originalWeight: str | None
    deviation: str | None
    proposedDeduction: str | None
    proposedSettlementWeight: str | None
    settlementWeight: str | None
    status: Literal["needs_confirmation", "pending", "approved", "rejected"]
    reason: str
    decisionNote: str
    decidedBy: str | None
    decidedAt: str | None
    createTime: str
    updateTime: str
