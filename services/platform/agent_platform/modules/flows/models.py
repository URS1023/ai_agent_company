"""工作流模型：编排定义与运行记录分开存放，运行留完整步骤证据。"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Workflow(Base):
    """工作流定义：steps 为有序步骤数组，由平台引擎解释执行。"""

    __tablename__ = 'flow_workflows'

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default='')
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowRun(Base):
    """一次运行：保存输入、每步输出与最终状态，失败不掩盖已完成步骤。"""

    __tablename__ = 'flow_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'created_by', 'workflow_id', 'idempotency_key',
        name='uq_flow_run_request'),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default='running')
    input: Mapped[str] = mapped_column(Text, default='')
    steps: Mapped[list[Any]] = mapped_column(JSON, default=list)
    output: Mapped[str] = mapped_column(Text, default='')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # null仅用于识别缺失来源的旧历史；新运行即使无命中也保存完整空间范围。
    knowledge_sources: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 旧运行保持null；新运行在外部请求前提交预约，不能因进程崩溃或刷新再次派发。
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    definition_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
