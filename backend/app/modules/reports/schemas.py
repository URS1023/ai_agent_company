"""Typed contracts for complete editable screens, assets and AI generation."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.common.contracts import ApiModel


class ReportInput(ApiModel):
    """新建 / 保存报表的写入契约（``POST /api/v1/reports``、``PUT /api/v1/reports/{id}``）。

    全部字段可省略，省略表示"不修改该字段"，因此重命名时可以只提交 ``report_name``。
    ``version`` 是调用方首次加载时看到的版本：服务层用它做乐观锁，冲突返回 409，
    不允许保存前偷偷重新取最新版本。``template_mode``/``template_id`` 表示显式模板
    意图，``manual`` 或明确给出 ``template_id`` 时不会回退到列表第一项。
    """

    id: str | None = Field(default=None, description="兼容编辑表单中的资源标识")
    report_name: str = Field(alias="reportName", min_length=1, max_length=200, description="报表或数字大屏名称")
    description: str = Field(default="", max_length=10000, description="业务用途说明")
    datasource_id: str | None = Field(default=None, alias="datasourceId", description="主要数据源，可为空")
    datasource_ids: list[str] = Field(default_factory=list, alias="datasourceIds", max_length=32, description="同屏使用的其他已授权数据源")
    content: dict | None = Field(default=None, description="完整画布和组件 JSON；兼容 JSON 字符串；重命名时省略以保留原内容")
    width: int | None = Field(default=None, ge=320, le=16384, description="画布宽度")
    height: int | None = Field(default=None, ge=200, le=16384, description="画布高度")
    background_color: str | None = Field(default=None, alias="backgroundColor", max_length=100, description="背景颜色")
    background_image: str | None = Field(default=None, alias="backgroundImage", max_length=1000000, description="背景图片资源地址")
    thumbnail: str | None = Field(default=None, max_length=1000000, description="缩略图或安全预览地址")
    refresh_seconds: int | None = Field(default=None, alias="refreshSeconds", ge=0, le=86400, description="数据刷新秒数；0 表示手动")
    enabled: bool | None = Field(default=None, description="兼容旧列表的启用状态，不等同于公开发布")
    status: Literal["draft", "published", "archived"] | None = Field(default=None, description="草稿、已发布或归档；发布需单独权限")
    template_mode: Literal["auto", "manual", "none"] | None = Field(default=None, alias="templateMode", description="显式模板模式，无固定默认模板")
    template_id: str | None = Field(default=None, alias="templateId", max_length=100, description="手选或 AI 动态适配的模板；不允许首项兜底")
    version: int | None = Field(default=None, ge=1, description="编辑提交首次加载时的版本，不在保存前偷偷获取最新版本")
    edit_template_layout: bool = Field(default=False, alias="editTemplateLayout", description="设计器保存用户编辑的布局；保留模板来源，不修改源模板")

    @field_validator("content", mode="before")
    @classmethod
    def parse_content(cls, value):
        """兼容老前端以 JSON 字符串提交画布；解析失败按业务错误返回。"""

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("报表内容不是合法 JSON") from exc
        return value


class ReportView(BaseModel):
    """报表读取视图（列表与详情共用）。

    ``content`` 有意以 JSON 字符串返回，保持与智慧云脑 Cloud Brain 设计器既有契约一致；
    ``version`` 同时是乐观锁版本和修订号，前端必须原样回传才能保存。
    """

    id: str = Field(description="报表标识")
    reportName: str = Field(description="名称")
    description: str = Field(description="说明")
    datasourceId: str | None = Field(description="主要数据源")
    datasourceIds: list[str] = Field(description="全部关联数据源")
    content: str = Field(description="完整画布与组件的 JSON 字符串，兼容智慧云脑 Cloud Brain 设计器")
    width: int = Field(description="画布宽度")
    height: int = Field(description="画布高度")
    backgroundColor: str = Field(description="背景色")
    backgroundImage: str = Field(description="背景图片")
    thumbnail: str = Field(description="缩略图")
    refreshSeconds: int = Field(description="数据刷新周期")
    enabled: bool = Field(description="是否未归档")
    status: str = Field(description="草稿、发布或归档状态")
    templateMode: str = Field(description="auto/manual/none")
    templateId: str | None = Field(description="明确采用的模板")
    version: int = Field(description="乐观锁与修订版本")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="修改时间 UTC")
    lineage: dict = Field(description="数据、模板、生成任务的追溯信息")


class TemplateInput(ApiModel):
    """自定义大屏模板写入契约。

    提交的 ``content`` 允许带着数据源绑定（用户在报表里直接另存为模板），服务层保存前
    会统一剥离数据源、SQL、查询参数、结果与凭据；``version`` 用于更新时的乐观锁。
    """

    name: str = Field(min_length=1, max_length=200, description="自定义大屏模板名称")
    description: str = Field(default="", max_length=10000, description="适用场景")
    category: str = Field(default="自定义", max_length=100, description="模板分类")
    tags: list[str] = Field(default_factory=list, max_length=40, description="搜索与动态匹配标签")
    content: dict = Field(description="完整画布骨架；保存时移除数据源绑定和业务数据")
    preview: str = Field(default="", max_length=1000000, description="安全预览图片")
    enabled: bool = Field(default=True, description="是否可选择")
    version: int | None = Field(default=None, ge=1, description="修改时的乐观锁版本")


class TemplateFromReportInput(ApiModel):
    """Metadata used when an approved report becomes a reusable template."""

    name: str = Field(default="", max_length=200, description="模板名称；空值沿用报表名称并追加“模板”")
    description: str = Field(default="", max_length=10000, description="适用场景；空值沿用报表说明")
    category: str = Field(default="业务大屏", max_length=100, description="模板分类")
    tags: list[str] = Field(default_factory=list, max_length=40, description="模板检索标签")
    preview: str | None = Field(default=None, max_length=1000000, description="预览图片；空值沿用报表真实缩略图")
    enabled: bool = Field(default=True, description="创建后是否可立即选择")


class DashboardRequirementDocument(ApiModel):
    """Versioned customer requirement JSON consumed as data, never as layout instructions.

    结构化客户需求（``dashboard-requirement-v1``）：``metrics``/``dimensions``/``filters``
    是生成结果必须逐项对齐的**业务覆盖口径**，与模板外观无关；``notes`` 允许写业务说明，
    但不能覆盖权限、数据源和模板锁定规则。该文档始终作为数据使用，不当作布局或系统指令。
    """

    schema_version: Literal["dashboard-requirement-v1"] = Field(
        default="dashboard-requirement-v1",
        alias="schemaVersion",
        description="客户需求 JSON 契约版本",
    )
    objective: str = Field(default="", max_length=10000, description="客户希望解决的业务问题")
    metrics: list[str] = Field(default_factory=list, max_length=100, description="希望展示的指标口径")
    dimensions: list[str] = Field(default_factory=list, max_length=100, description="分析维度，例如部门、地区、设备")
    filters: list[str] = Field(default_factory=list, max_length=100, description="筛选条件的自然语言表达")
    time_range: str = Field(default="", alias="timeRange", max_length=1000, description="时间范围与粒度")
    notes: str = Field(default="", max_length=10000, description="其他业务说明；不能覆盖权限与模板锁定规则")

    @field_validator("objective", "time_range", "notes")
    @classmethod
    def trim_requirement_text(cls, value: str) -> str:
        """去掉首尾空白；空白输入会被视为"未提供"而不是空字符串口径。"""

        return value.strip()

    @field_validator("metrics", "dimensions", "filters")
    @classmethod
    def normalize_requirement_items(cls, values: list[str]) -> list[str]:
        """按顺序去空、去重并限制单项长度；顺序保留用户书写优先级供覆盖度检查使用。"""

        result: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in result:
                if len(normalized) > 1000:
                    raise ValueError("客户需求单项不能超过 1000 个字符")
                result.append(normalized)
        return result

    def has_business_content(self) -> bool:
        """判断需求文档是否真的带了业务信息；全空时不应影响生成分支。"""

        return bool(
            self.objective or self.metrics or self.dimensions
            or self.filters or self.time_range or self.notes
        )

    def search_text(self) -> str:
        """拼出用于模板/指标匹配的检索文本；前缀（指标/维度/筛选）用于区分同名词。"""

        return "\n".join(filter(None, [
            self.objective,
            *(f"指标：{value}" for value in self.metrics),
            *(f"维度：{value}" for value in self.dimensions),
            *(f"筛选：{value}" for value in self.filters),
            f"时间：{self.time_range}" if self.time_range else "",
            self.notes,
        ]))


class TemplatePackageInput(ApiModel):
    """模板包导入契约（``POST /api/v1/report-templates/import``）。

    ``checksum`` 是对模板载荷计算的 SHA-256，服务层校验通过后才会剥离绑定、查询、数据
    和凭据再落库——校验失败即整包拒绝，不做部分导入。
    """

    schema_version: Literal["report-template-package-v1"] = Field(
        alias="schemaVersion", description="可校验的模板交换格式版本",
    )
    exported_at: str = Field(alias="exportedAt", min_length=1, max_length=80, description="导出时间，仅用于追溯")
    source: dict = Field(default_factory=dict, description="来源模板标识、版本与锁定状态，不作为授权依据")
    template: TemplateInput = Field(description="已经移除数据、SQL、绑定与凭据的模板骨架")
    checksum: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$", description="模板载荷 SHA-256")


class TemplatePublishInput(ApiModel):
    """模板重新发布契约；重复发布同一个版本无需参数，版本不一致时以 409 要求刷新。"""

    version: int = Field(ge=1, description="页面加载时的模板版本；冲突时必须刷新")


class TemplateRestoreInput(ApiModel):
    """模板历史恢复契约；恢复到旧修订会生成一个**新**版本，历史修订本身不变。"""

    version: int = Field(ge=1, description="当前模板版本")
    revision: int = Field(ge=1, description="要恢复的历史修订；恢复会产生新版本")


class CategoryInput(ApiModel):
    """提示词分类写入契约；系统预置分类（builtin）不可通过此契约覆盖。"""

    name: str = Field(min_length=1, max_length=160, description="提示词分类名称")
    description: str = Field(default="", max_length=2000, description="分类说明")
    color: str = Field(default="#2364c6", max_length=40, description="分类标识颜色")
    icon: str = Field(default="fa-chart-pie", max_length=100, description="图标类名")
    sort_order: int = Field(default=0, alias="sortOrder", description="显示顺序")
    version: int | None = Field(default=None, ge=1, description="编辑所需版本")


class PromptInput(ApiModel):
    """提示词写入契约；``content`` 只作为文本复用，服务层不会执行其中的指令。"""

    category_id: str = Field(alias="categoryId", min_length=1, max_length=26, description="当前工作区的提示词分类标识")
    name: str = Field(min_length=1, max_length=200, description="提示词名称")
    content: str = Field(min_length=1, max_length=30000, description="可复用的提示词正文")
    description: str = Field(default="", max_length=5000, description="使用说明")
    tags: str = Field(default="", max_length=1000, description="逗号分隔标签")
    mode: Literal["screen", "widget", "query"] = Field(default="screen", description="整屏、单组件或查询提示词")
    version: int | None = Field(default=None, ge=1, description="编辑版本")


class GenerationInput(ApiModel):
    """单个可编辑组件的生成请求（``POST /api/v1/reports/widget/ai-generate``）。

    ``datasource_id``/``datasource_ids`` 只能引用调用方已获授权的数据源；``theme`` 与
    ``requirement`` 都只是生成需求文本。默认 ``template_mode="none"``，即单组件生成
    默认不使用模板。
    """

    datasource_id: str | None = Field(default=None, alias="datasourceId", description="主要已授权数据源")
    datasource_ids: list[str] = Field(default_factory=list, alias="datasourceIds", max_length=8, description="可协同分析的其他数据源")
    theme: str = Field(default="", max_length=40000, description="整屏生成需求与业务口径")
    requirement: str = Field(default="", max_length=40000, description="单组件生成需求")
    title: str | None = Field(default=None, max_length=200, description="显式指定的大屏草稿标题")
    requirement_document: DashboardRequirementDocument | None = Field(
        default=None,
        alias="requirementDocument",
        description="结构化客户需求；服务端校验后作为数据绑定依据，不允许修改模板视觉属性",
    )
    width: int = Field(default=1920, ge=320, le=16384, description="画布宽度")
    height: int = Field(default=1080, ge=200, le=16384, description="画布高度")
    template_mode: Literal["auto", "manual", "none"] = Field(default="none", alias="templateMode", description="模板策略；单组件默认none，整屏接口默认auto")
    template_id: str | None = Field(default=None, alias="templateId", max_length=100, description="手动模式必须明确选择")
    generation_seed: str = Field(default="", alias="generationSeed", max_length=160, description="本次设计变体标识")
    session_id: str = Field(default="", alias="sessionId", max_length=120, description="生成会话标识")


class ScreenGenerationInput(GenerationInput):
    """整屏生成请求（``POST /api/v1/reports/screen/ai-generate``）。

    与单组件唯一的契约差异是模板策略默认值：整屏未指定模板时用 ``auto``（按需求自动
    匹配模板），``manual`` 只往选定模板里填数据，``none`` 仅为兼容旧集成保留。
    """

    template_mode: Literal["auto", "manual", "none"] = Field(
        default="auto", alias="templateMode",
        description="未指定模板时自动参考模板；manual只填数据，显式none仅兼容旧集成",
    )


class DrilldownInput(ApiModel):
    """钻取请求契约（``POST /api/v1/reports/drilldown``）。

    SQL 始终由服务端按 ``report_id``+``widget_id`` 从已保存报表中读取，``point`` 只用于
    取值不参与拼接，``parameters`` 必须是该组件钻取配置里声明过的命名参数。
    """

    report_id: str = Field(alias="reportId", description="已保存报表标识；服务端从该报表读取钻取配置")
    widget_id: str = Field(alias="widgetId", description="报表内组件标识")
    point: dict[str, Any] = Field(default_factory=dict, description="点击的数据点，不作为 SQL 指令")
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="配置允许的命名过滤参数")
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000, description="明细最大行数")


class RestoreInput(ApiModel):
    """报表历史恢复契约（``POST /api/v1/reports/{id}/restore``）。

    恢复结果一定是一个**新的草稿修订**：旧修订保持原样，恢复出来的报表需重新校验其
    引用的数据源与组件权限，因此不会直接回到已发布状态。
    """

    version: int = Field(ge=1, description="当前报表版本，防止覆盖并发修改")
    revision: int = Field(ge=1, description="要恢复的历史修订；恢复会新建版本，不改写历史")
