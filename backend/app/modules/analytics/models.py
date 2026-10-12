"""Persisted metadata snapshots, query provenance and reusable query templates."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.ids import new_id
from app.models import Base, now
from app.modules.system.models import WorkspaceRecord


class SourceMetadata(OwnedRecord, Base):
    """一张数据源表结构与业务语义的快照（按 workspace + 数据源唯一）。

    不变式：``source_version`` 必须与生成快照时的数据源配置版本一致，读取方据此
    判断缓存是否过期；``tables_json`` 存的是**已授权**的表/字段快照，因此它同时是
    SQL 授权的依据，不能存放未授权表。
    """

    __tablename__ = "analytics_source_metadata"
    __table_args__ = (UniqueConstraint("workspace_id", "datasource_id", name="uq_analysis_source_metadata"),)
    datasource_id: Mapped[str] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=False, comment="原 Test5 数据源标识")
    source_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="采集时的数据源配置版本")
    tables_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="表、列、字段注释、主外键与业务语义")
    status: Mapped[str] = mapped_column(String(30), default="ready", nullable=False, comment="元数据同步状态")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="不含连接凭据的同步错误")


class SourceSemanticDocument(OwnedRecord, Base):
    """Persisted, source-versioned semantic index for one authorized table.

    Keeping one row per table avoids putting a multi-megabyte vector array into
    ``SourceMetadata.tables_json`` and lets a metadata refresh replace only the
    affected source.  Raw business rows are never persisted in this index.
    """

    __tablename__ = "analytics_source_semantic_documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "datasource_id", "table_name",
            name="uq_analytics_source_semantic_table",
        ),
    )
    datasource_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("data_sources.id"), nullable=False, index=True,
        comment="所属分析数据源",
    )
    source_version: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="构建索引时的数据源配置版本",
    )
    table_name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="真实授权表名",
    )
    document_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="表名、表注释及最多 40 个字段说明组成的语义文档",
    )
    keywords_json: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="用于无向量模型时的确定性关键词索引",
    )
    embedding_json: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="归一化表语义向量；未配置向量模型时为空",
    )
    relations_json: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="真实外键及低代码元数据关系，不按同名字段猜测",
    )
    index_status: Mapped[str] = mapped_column(
        String(30), default="keyword_only", nullable=False,
        comment="ready/keyword_only",
    )


class QueryHistory(OwnedRecord, Base):
    """一次只读分析或 HTTP 采集的持久化留痕，是查询结果与审计的唯一来源。

    生命周期：先以 running 落库（拿到 query_id 以便运行中心关联），成功写入
    result_json/model_usage_json，失败写入脱敏 error_message。query_mode=http 的
    记录由采集模块写入，历史接口据此分流，避免把采集结果当 SQL 结果展示。
    撤销数据源后这些记录仍保留用于追溯，但读取时会重新校验数据源可见性。
    """

    __tablename__ = "analytics_query_history"
    run_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("runs.id"), nullable=True, index=True, comment="统一运行中心标识；普通分析不创建聊天会话")
    datasource_id: Mapped[str] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=False, index=True, comment="查询数据源")
    session_id: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True, comment="同一用户、数据源下的多轮查询会话")
    query_text: Mapped[str] = mapped_column(Text, nullable=False, comment="自然语言问题或手工 SQL")
    generated_sql: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="校验后实际执行的只读 SQL")
    query_mode: Mapped[str] = mapped_column(String(20), default="sql", nullable=False, comment="sql 或 natural_language")
    datasource_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="执行时使用的数据源配置版本")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", comment="running/success/failed/cancelled")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="限量查询结果、类型、统计与真实截断标识")
    model_usage_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="模型名称、用量与调用耗时，不存储密钥")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="脱敏查询错误")
    execution_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="查询端到端执行时间毫秒")


class QueryTemplate(OwnedRecord, Base):
    """可复用的查询模板（自然语言提示词或参数化只读 SQL）。

    保存模板只是保存文本，不代表执行过 SQL；复用接口只累加 usage_count，
    真正执行时仍要通过 analytics.execute 与 SQL 授权管线。
    """

    __tablename__ = "analytics_query_templates"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="可复用查询名称")
    category: Mapped[str] = mapped_column(String(100), default="未分类", nullable=False, comment="查询场景分类")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="自然语言或带命名参数的 SQL")
    query_mode: Mapped[str] = mapped_column(String(20), default="natural_language", nullable=False, comment="查询模式")
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="参数类型、说明与非敏感示例")
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="通过接口复用次数")


class DataSourcePolicy(WorkspaceRecord, Base):
    """Versioned execution and health policy for one governed data source."""

    __tablename__ = "analytics_datasource_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "datasource_id", name="uq_analytics_datasource_policy"),
    )
    datasource_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("data_sources.id"), nullable=False, index=True,
        comment="受治理的数据源",
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer, default=4, nullable=False, comment="同一数据源并发分析查询上限",
    )
    daily_query_limit: Mapped[int] = mapped_column(
        Integer, default=10000, nullable=False, comment="UTC 自然日查询尝试上限；0 表示不限",
    )
    health_check_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否启用真实连接健康调度",
    )
    health_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False, comment="健康检查间隔分钟数",
    )
    health_schedule_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("system_scheduled_jobs.id"), nullable=True, index=True,
        comment="统一调度中心任务标识",
    )
    created_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    updated_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class DataSourceHealthCheck(Base):
    """Append-only, redacted evidence from a manual or scheduled health check."""

    __tablename__ = "analytics_datasource_health_checks"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    datasource_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("data_sources.id"), nullable=False, index=True,
    )
    datasource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="manual/schedule",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="healthy/unhealthy",
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    checked_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class DataSourceQueryLease(Base):
    """Short-lived cross-request lease used to enforce source concurrency limits."""

    __tablename__ = "analytics_datasource_query_leases"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    datasource_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("data_sources.id"), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
