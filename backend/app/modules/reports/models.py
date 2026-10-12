"""Editable report documents and immutable revision/generation provenance."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class Report(OwnedRecord, Base):
    """报表 / 数字大屏的可编辑主文档（表 ``report_documents``）。

    这里是设计器加载和保存的唯一权威副本：``content`` 保存完整画布、组件、样式、
    绑定与钻取配置，``lineage`` 保存生成任务、查询和来源追溯。生命周期由 ``status``
    表示（draft/published/archived），``template_mode`` 与 ``template_id`` 记录模板
    来源，但**不隐含默认模板**——调用方必须显式选择。乐观锁版本来自 ``OwnedRecord``
    的 ``version_id_col``，并发保存在新事务提交时抛 StaleDataError 由上层转 409。
    删除报表不删除数据库对象、源文件或其他报表，只移除本行并保留修订历史。
    """

    __tablename__ = "report_documents"
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="报表或数字大屏名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="报表用途说明")
    datasource_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="使用的数据源标识，可包含财务和设备多个数据源")
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="完整画布、组件、样式、绑定、交互与钻取契约")
    width: Mapped[int] = mapped_column(Integer, default=1920, nullable=False, comment="画布宽度像素")
    height: Mapped[int] = mapped_column(Integer, default=1080, nullable=False, comment="画布高度像素")
    background_color: Mapped[str] = mapped_column(String(100), default="#07111f", nullable=False, comment="画布底色")
    background_image: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="背景资源地址")
    thumbnail: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="用户可查看的缩略图或模板预览地址")
    refresh_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False, comment="真实数据刷新周期；0 表示手动刷新")
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, comment="draft/published/archived")
    template_mode: Mapped[str] = mapped_column(String(20), default="none", nullable=False, comment="auto/manual/none；不隐含默认模板")
    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="明确选择或自动匹配的模板 ID")
    lineage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="生成任务、查询、原项目及数据状态追溯")


class ReportSourceImport(OwnedRecord, Base):
    """Stable workspace-local identity for a report imported from Lynx."""

    __tablename__ = "report_source_imports"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_kind", "source_record_id",
            name="uq_report_source_import_record",
        ),
        UniqueConstraint("report_id", name="uq_report_source_import_report"),
    )
    report_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("report_documents.id"), nullable=False, index=True,
        comment="导入后可直接访问的报表标识",
    )
    source_kind: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="来源类型，例如 lynx-report-screen",
    )
    source_record_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="来源接口中的稳定记录标识",
    )
    source_template_id: Mapped[str] = mapped_column(
        String(100), default="", nullable=False, comment="旧模板导入兼容字段；直接报表导入为空",
    )
    source_endpoint: Mapped[str] = mapped_column(
        String(500), default="", nullable=False, comment="已脱敏的来源接口地址",
    )
    source_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="来源原始内容摘要",
    )
    catalog_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="导入时模板目录摘要",
    )


class ReportRevision(OwnedRecord, Base):
    """报表的不可变历史快照（表 ``report_revisions``）。

    每次保存、发布、恢复都会追加一行，``(report_id, revision)`` 唯一；本表只追加、
    不更新、不删除，回滚历史版本的做法是**再新建一个修订**而不是改写旧行。快照必须
    自包含（画布、组件、模板、来源），以便恢复时无需读取其它表即可重建报表。
    """

    __tablename__ = "report_revisions"
    __table_args__ = (UniqueConstraint("report_id", "revision", name="uq_report_revision"),)
    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("report_documents.id"), nullable=False, index=True, comment="所属报表")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="报表修订序号")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="此修订完整内容，回滚采用新建修订")
    reason: Mapped[str] = mapped_column(String(300), default="用户保存", nullable=False, comment="创建、修改、发布或回滚原因")


class ReportTemplate(OwnedRecord, Base):
    """工作区自定义大屏模板（表 ``report_templates``）。

    只保存视觉骨架：``content`` 中不得出现数据源绑定、SQL、查询参数、结果数据和凭据
    （由 ``reports.template_lock`` 强制）。``enabled=False`` 表示已移入回收站，工作区
    仍保留该行以便恢复，因此删除是可逆的软删除，不影响已用该模板创建的报表。
    """

    __tablename__ = "report_templates"
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="自定义模板名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="视觉与业务用途说明")
    category: Mapped[str] = mapped_column(String(100), default="自定义", nullable=False, comment="模板分类")
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="用于搜索和动态匹配的标签")
    content: Mapped[dict] = mapped_column(JSON, nullable=False, comment="不含私有业务数据的画布与组件骨架")
    preview: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="模板预览资源地址")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否可选")


class ReportTemplateRevision(OwnedRecord, Base):
    """Immutable custom-template snapshots used for audit and recovery."""

    __tablename__ = "report_template_revisions"
    __table_args__ = (UniqueConstraint("template_id", "revision", name="uq_report_template_revision"),)
    template_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("report_templates.id"), nullable=False, index=True, comment="自定义模板标识",
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="模板资源版本")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="该版本的无业务数据模板快照")
    reason: Mapped[str] = mapped_column(String(300), default="保存模板", nullable=False, comment="创建版本的业务动作")


class ReportTemplateUsage(OwnedRecord, Base):
    """Append-only evidence for actual report creation from any template."""

    __tablename__ = "report_template_usages"
    template_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="内置或自定义模板标识")
    template_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="使用时模板版本")
    report_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("report_documents.id"), nullable=True, index=True, comment="实际创建的报表标识",
    )
    source: Mapped[str] = mapped_column(String(40), default="report_create", nullable=False, comment="使用动作来源")
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="不含数据与凭据的使用上下文")


class ReportBuiltinTemplateState(OwnedRecord, Base):
    """Workspace-local recycle state for immutable source template assets."""

    __tablename__ = "report_builtin_template_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "template_id", name="uq_report_builtin_template_state"),
    )
    template_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, comment="不可变来源模板标识",
    )
    hidden: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否已从当前工作区模板中心删除",
    )


class PromptCategory(OwnedRecord, Base):
    """提示词分类（表 ``report_prompt_categories``）。

    ``builtin=True`` 的行为系统预置项，只能复制不能覆盖；删除自定义分类前必须先清空
    其下提示词，否则会遗留孤立提示词（服务层以 409 拒绝）。
    """

    __tablename__ = "report_prompt_categories"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="提示词分类名")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="分类用途")
    color: Mapped[str] = mapped_column(String(40), default="#2364c6", nullable=False, comment="分类标识颜色")
    icon: Mapped[str] = mapped_column(String(100), default="fa-chart-pie", nullable=False, comment="分类图标名称")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="分类显示顺序")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否为系统预置分类")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否在分类列表中展示；移除时保留引用")


class PromptTemplate(OwnedRecord, Base):
    """可复用提示词正文（表 ``report_prompt_templates``）。

    ``content`` 只作为文本被复制到生成需求里，**不会被执行**：其中的工具、权限或配置
    指令不产生任何副作用。``usage_count`` 只是统计，复用不覆盖编辑中的内容版本；
    ``status`` 承担软删除（deleted）与归档，删除后内容版本仍保留以供运行追溯。
    """

    __tablename__ = "report_prompt_templates"
    category_id: Mapped[str] = mapped_column(String(26), ForeignKey("report_prompt_categories.id"), nullable=False, comment="提示词所属分类")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="可复用提示词名称")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="提示词正文，不执行其中的权限或配置指令")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="适用场景及说明")
    tags: Mapped[str] = mapped_column(String(1000), default="", nullable=False, comment="逗号分隔的搜索标签")
    mode: Mapped[str] = mapped_column(String(30), default="screen", nullable=False, comment="screen/widget/query 通用提示词模式")
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="复用次数")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="预置项只可复制，不直接覆盖")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived/deleted")


class PromptRevision(OwnedRecord, Base):
    """提示词内容版本快照（表 ``report_prompt_revisions``），只追加不修改。

    ``(prompt_id, revision)`` 唯一；用于在提示词被改写或删除后仍能解释某次生成用的是
    哪一版正文。
    """

    __tablename__ = "report_prompt_revisions"
    __table_args__ = (UniqueConstraint("prompt_id", "revision", name="uq_report_prompt_revision"),)
    prompt_id: Mapped[str] = mapped_column(String(26), ForeignKey("report_prompt_templates.id"), nullable=False, index=True, comment="提示词标识")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="内容修订版本")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="此版本名称、正文、分类及标签")


class ReportGeneration(OwnedRecord, Base):
    """一次大屏/组件生成的运行记录（表 ``report_generation_runs``）。

    ``events`` 只记录**真实发生**的阶段与耗时，不得写入模拟进度：前端据此区分"已解析
    数据源并执行查询"与"仍在等待模型"。``result_json`` 必须是经过契约校验和真实查询
    验证的结果，``error_message`` 需脱敏后再落库。
    """

    __tablename__ = "report_generation_runs"
    status: Mapped[str] = mapped_column(String(30), default="running", nullable=False, comment="running/completed/failed/cancelled")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, comment="screen 或 widget")
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, comment="生成需求及显式数据源、模板配置")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="经契约与真实查询校验的生成结果")
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="真实阶段、耗时、进度事件")
    usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="模型调用用量与关联查询标识")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="脱敏失败原因")
