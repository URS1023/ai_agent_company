"""受控数据来源、导入批次、查询快照与处理执行记录。"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def now():
    return datetime.now(timezone.utc)


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    archived: Mapped[bool] = mapped_column(default=False)


class DataSource(Entity, Base):
    __tablename__ = "data_sources"
    name: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(30), default="sqlite")
    managed_filename: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="active")
    description: Mapped[str] = mapped_column(Text, default="")
    schema_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    max_rows: Mapped[int] = mapped_column(Integer, default=1000)
    connection_config: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_approval: Mapped[dict] = mapped_column(JSON, default=dict)


class Dataset(Entity, Base):
    __tablename__ = "data_datasets"
    name: Mapped[str] = mapped_column(String(160))
    datasource_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(100))
    row_count: Mapped[int] = mapped_column(Integer)
    columns: Mapped[list] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(30), default="csv_import")


class QueryRecord(Entity, Base):
    __tablename__ = "data_queries"
    name: Mapped[str] = mapped_column(String(160), default="数据查询")
    datasource_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    sql: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="succeeded")


class ProcessingJob(Entity, Base):
    __tablename__ = "data_processing_jobs"
    name: Mapped[str] = mapped_column(String(160))
    query_id: Mapped[str] = mapped_column(ForeignKey("data_queries.id"))
    operations: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))


class RowPolicy(Entity, Base):
    """每张源表一个显式策略集；删除规则不等于撤销行限制。"""
    __tablename__ = "data_row_policies"
    datasource_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(16), default="all")
    rules: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("workspace_id", "datasource_id", "table_name", name="uq_data_row_policy_table"),)


class QueryAccessSnapshot(Base):
    """查询结果的不可变授权证据；主体仅审计，协作访问比较有效行列范围。"""
    __tablename__ = "data_query_access_snapshots"
    query_id: Mapped[str] = mapped_column(ForeignKey("data_queries.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_user_id: Mapped[str] = mapped_column(String(36))
    used_tables: Mapped[list] = mapped_column(JSON)
    field_scope: Mapped[dict] = mapped_column(JSON)
    row_scope: Mapped[dict] = mapped_column(JSON)
    policy_versions: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
