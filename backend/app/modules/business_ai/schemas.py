"""Typed contracts for the independent AI workbench's real business actions."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.reports.schemas import DashboardRequirementDocument


# 结果卡片上的操作链接：permission 只是提示，前端隐藏不构成安全边界，
# 后端仍会按该权限重新校验。
class BusinessActionLink(BaseModel):
    label: str = Field(description="用户可见操作名称")
    href: str = Field(description="同一业务资源的普通模块独立路由")
    kind: Literal["primary", "secondary"] = Field(default="secondary", description="结果卡片操作层级")
    permission: str = Field(default="", description="后端仍会执行的动作权限；前端隐藏不构成安全边界")


# 业务能力目录项：AI、接口合集与审计共用同一契约；inputSchema 由真实入参模型生成，
# 因此“目录里能看到的参数”与“直接调 API 能传的参数”始终一致。
class BusinessActionCapability(BaseModel):
    capabilityId: str = Field(description="AI、接口合集与审计共用的稳定能力标识")
    operationId: str = Field(description="主要外部 API 的稳定 OpenAPI operationId")
    name: str = Field(description="中文业务名称")
    description: str = Field(description="能力用途和不会执行的事项")
    utterances: list[str] = Field(description="用户常见说法，仅用于意图提示，不绕过参数校验")
    inputSchema: dict = Field(description="与直接 API 和工具参数一致的 JSON Schema")
    requiredPermissions: list[str] = Field(description="执行前和最终业务服务中都会复核的最小权限")
    readWrite: Literal["read", "create"] = Field(description="是否产生持久化业务资源")
    confirmationPolicy: str = Field(description="当前动作的确认策略；发布和生产写回不属于这些动作")
    idempotencyPolicy: str = Field(description="防重复策略")
    resultComponent: str = Field(description="AI 消息中的受支持结果组件类型")
    available: bool = Field(description="当前用户、工作区和权限下是否可执行")
    unavailableReasons: list[str] = Field(description="不可执行时缺失的权限，不包含其他用户资源信息")
    modulePath: str = Field(description="对应普通业务模块入口")
    version: int = Field(default=1, description="能力契约版本")


# 数据源发现入参：空关键词列出当前授权范围，带关键词时按名称/说明/元数据排序；
# limit 有上限，且**不会**静默选择第一项——候选必须由用户明确勾选。
class DataSourceDiscoveryInput(ApiModel):
    keyword: str = Field(default="", max_length=200, description="按数据源业务名称和用途说明筛选；空值列出当前授权范围")
    limit: int = Field(default=8, ge=1, le=20, description="最多返回的数据源候选数；不会静默选择第一项")


# 单个数据源摘要：只含已授权且已缓存的元数据；未同步时 tables 为空、状态为 not_synced，
# 不伪造表结构或行数。
class BusinessDataSourceSummary(BaseModel):
    id: str = Field(description="显式传给后续分析或大屏动作的数据源标识")
    name: str = Field(description="数据源业务名称")
    provider: str = Field(description="mysql/postgres/sqlite/elasticsearch/http")
    resourceType: Literal["database", "http"] = Field(
        default="database", description="数据库分析源或固定目标 HTTP/API 数据源",
    )
    description: str = Field(description="管理员配置的业务用途或口径说明")
    descriptionSource: Literal["configured", "metadata", "empty"] = Field(default="empty", description="用途说明来自管理员或数据库元数据")
    environmentSummary: str = Field(default="", description="不含凭据的数据库环境")
    version: int = Field(description="当前连接配置版本")
    metadataStatus: Literal["ready", "stale", "not_synced"] = Field(description="元数据是否已按当前连接版本同步")
    tableCount: int = Field(default=0, description="全部授权表/视图数")
    businessTableCount: int = Field(default=0, description="排除系统表后的业务表数")
    fieldCount: int = Field(default=0, description="业务字段数")
    commentCoverage: dict = Field(default_factory=dict, description="表和字段注释覆盖情况")
    semanticIndexStatus: Literal["ready", "keyword_only", "failed", "not_indexed"] = Field(default="not_indexed", description="语义索引可用性")
    semanticIndexedTableCount: int = Field(default=0, description="已索引业务表数")
    businessDomains: list[str] = Field(default_factory=list, description="从数据库注释识别的业务域")
    automaticReady: bool = Field(
        default=True,
        description="是否已确认允许 AI、报表或工作流自动只读调用；数据库启用后为 true",
    )
    matchEvidence: dict = Field(default_factory=dict, description="本次关键词与数据源匹配依据")
    returnedTableCount: int = Field(default=0, description="本次摘要实际返回的表数")
    tablesTruncated: bool = Field(default=False, description="是否只返回部分授权业务表；不能据此断言其他表不存在")
    tables: list[dict] = Field(description="已缓存且授权的表、字段摘要；未同步时为空，不会伪造")


# 数据源发现结果：truncated=true 表示还有候选未返回，此时应让用户缩小范围，
# 而不是假设“只有这些数据源”。
class DataSourceDiscoveryView(BaseModel):
    resultType: Literal["resource_resolution"] = "resource_resolution"
    sources: list[BusinessDataSourceSummary] = Field(description="当前用户可见且启用的数据源候选")
    returnedCount: int = Field(description="本次实际返回数")
    truncated: bool = Field(description="是否还有候选未返回；为 true 时应让用户缩小范围")
    selectionPolicy: str = Field(description="资源选择规则")


# 告警总览的报告、问题各有独立分页；旧 offset 仍可同时设置两者。
# 指定 problemId 时 offset 只用于处置日志，报告与问题本身各返回一条。
class AlertOverviewInput(ApiModel):
    limit: int = Field(default=20, ge=1, le=50, description="分别读取最近报告和问题的条数；统计仅针对本次已授权结果")
    offset: int = Field(default=0, ge=0, le=10000, description="返回页起点；指定问题时用于处置日志分页")
    reports_offset: int | None = Field(default=None, alias="reportsOffset", ge=0, le=10000,
                                      description="报告列表独立分页起点；省略时使用 offset，指定报告时固定读取该报告")
    problems_offset: int | None = Field(default=None, alias="problemsOffset", ge=0, le=10000,
                                       description="问题列表独立分页起点；省略时使用 offset，指定问题时固定读取该问题")
    problem_id: str | None = Field(default=None, alias="problemId", min_length=26, max_length=26,
                                  pattern=r"^[A-Z0-9]+$", description="跟进某个实际问题及其处置变更日志")
    report_id: str | None = Field(default=None, alias="reportId", min_length=26, max_length=26,
                                 pattern=r"^[A-Z0-9]+$", description="限定实际预警报告及其问题")


# 告警总览结果：summary 里的数量只是本次返回页的数量（不冒充全工作区总数），
# message 由 alert_evidence 依据真实行渲染，区分“报告判定”“执行状态”与“人工处置”。
class AlertOverviewView(BaseModel):
    resultType: Literal["alert_problem_list"] = "alert_problem_list"
    title: str = Field(description="结果卡片标题")
    summary: dict = Field(description="本次返回报告数、问题数、状态分布和是否可能还有更多数据")
    reports: list[dict] = Field(description="最近报告摘要，不含未经授权的设备或来源")
    problems: list[dict] = Field(description="最近问题摘要及人工处置状态")
    actions: list[BusinessActionLink] = Field(description="打开正常预警模块的操作")
    provenance: dict = Field(description="读取时间、工作区、权限和数据性质")
    processingHistory: dict | None = Field(default=None, description="实际问题的人工处置日志；未读取时为null")
    message: str = Field(default="", description="依据本次返回记录生成的事实说明，区分报告判定与人工处置")


# 大屏草稿入参：requestKey 是防重复创建的唯一凭据（同键同参回放、同键不同参 409）；
# requirement 与 requirementDocument 可同时提供并合并追溯；datasourceIds 为空表示
# 由服务端按元数据发现候选，而不是固定取第一个数据源。
class DashboardDraftInput(ApiModel):
    request_key: str = Field(
        alias="requestKey", min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_-]+$",
        description="防止超时或重试重复创建大屏；同键不同配置返回 409",
    )
    requirement: str = Field(
        default="", max_length=40000,
        description="客户需求文本；也可改用 requirementDocument 提交版本化 JSON",
    )
    requirement_document: DashboardRequirementDocument | None = Field(
        default=None,
        alias="requirementDocument",
        description="结构化客户需求 JSON；与文本同时存在时合并使用并完整追溯",
    )
    title: str = Field(default="", max_length=200, description="草稿名称；空值采用经验证生成结果的标题")
    datasource_ids: list[str] = Field(
        default_factory=list, alias="datasourceIds", max_length=8,
        description="可选的明确授权数据源；空值由服务发现候选，不使用固定第一项",
    )
    template_mode: Literal["auto", "manual", "none"] = Field(
        default="auto", alias="templateMode",
        description="auto 动态匹配、manual 明确模板、none 无模板；均不使用固定默认模板",
    )
    template_id: str | None = Field(default=None, alias="templateId", max_length=100, description="manual 模式必填的模板标识")
    width: int = Field(default=1920, ge=320, le=16384, description="画布宽度像素")
    height: int = Field(default=1080, ge=200, le=16384, description="画布高度像素")

    @field_validator("requirement", "title")
    @classmethod
    def trim_text(cls, value: str) -> str:
        """去首尾空白：避免用空格绕过“空需求”校验，也避免标题带隐形空白。"""
        return value.strip()

    @model_validator(mode="after")
    def validate_template_selection(self):
        """跨字段校验：需求非空、manual 必须给模板、非 manual 禁止给模板、数据源不重复。

        这些约束保证“模板策略”与“模板标识”始终一致，避免出现声明 manual 却没模板
        （生成时静默退回自动匹配）这类口径不一致的情况。
        """
        if not self.requirement and not (
            self.requirement_document and self.requirement_document.has_business_content()
        ):
            raise ValueError("请输入客户需求，或提交含业务内容的 requirementDocument")
        if self.template_mode == "manual" and not self.template_id:
            raise ValueError("手动模板模式必须明确选择模板")
        if self.template_mode != "manual" and self.template_id:
            raise ValueError("只有手动模板模式可以指定模板标识")
        if len(set(self.datasource_ids)) != len(self.datasource_ids):
            raise ValueError("数据源标识不能重复")
        return self


# 大屏草稿结果：reused=true 表示由相同 requestKey 回放到既有草稿（未新建）；
# datasourceIds/queryIds 只包含**真正被组件使用**的数据源与查询，便于逐项核对。
class DashboardDraftView(BaseModel):
    resultType: Literal["dashboard"] = "dashboard"
    receiptId: str = Field(description="本次持久化业务动作的防重复凭据")
    reportId: str = Field(description="报表模块中同一草稿的资源标识")
    generationId: str = Field(description="AI 大屏生成阶段记录")
    generationRunId: str = Field(description="真实元数据、模型和查询生成 Run")
    invocationRunId: str | None = Field(description="发起动作的对话 Run；直接 API 时可为空")
    title: str = Field(description="草稿名称")
    status: Literal["draft"] = "draft"
    version: int = Field(description="报表草稿当前版本")
    templateMode: Literal["auto", "manual", "none"] = Field(description="实际使用的模板策略")
    templateId: str | None = Field(description="实际匹配的模板；无适配模板时为空")
    datasourceIds: list[str] = Field(description="真正被组件查询使用的数据源，不包含未采用候选")
    datasourceNames: list[str] = Field(description="真正被组件查询使用的数据源名称")
    queryIds: list[str] = Field(description="每个真实只读查询的追溯标识")
    widgetCount: int = Field(description="已保存的可编辑组件数")
    previewPath: str = Field(description="AI 结果区可加载的交互预览路由")
    actions: list[BusinessActionLink] = Field(description="打开预览、完整设计器或列表的操作")
    provenance: dict = Field(description="模板、生成、数据与幂等来源")
    reused: bool = Field(description="是否由相同防重复键恢复既有结果")
