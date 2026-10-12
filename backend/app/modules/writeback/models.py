"""Versioned destination allowlists and durable external-write receipts.

中文补充（写回数据模型）：
- WriteTarget：用户显式授权的一份"数据源 + 目标表 + 允许字段 + 允许模式"白名单，
  带 source_version 记录确认时的连接配置版本（配置变了就要求重新确认），
  它**不是**任意 SQL 通道，因此没有存放 SQL 文本的字段。
- WriteOperation：一次节点写入的意图与提交结果回执。(execution_id, node_id) 唯一，
  这是幂等与"不自动重发"的落地方式；affected_rows 允许为 NULL 表示提交结果未知
  （不确定时绝不能写成 0 冒充"没影响"）。
- 两表都继承 OwnedRecord（工作区 + 归属人），历史回执保留来源数据源 ID 以便回溯鉴权。
"""
from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class WriteTarget(OwnedRecord, Base):
    """An explicit source-version/table/column/mode grant, not an arbitrary SQL tool.

    中文补充：写入授权记录。status=active 才允许新写入，disabled/deleted 只保留历史；
    columns/modes 是执行期的白名单来源（执行层还会用实时表结构二次校验）。
    """
    __tablename__ = "workflow_write_targets"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="写入目标名称")
    datasource_id: Mapped[str] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=False, index=True, comment="授权目标源")
    source_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="已确认的连接配置版本")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="真实业务表名")
    columns: Mapped[list] = mapped_column(JSON, nullable=False, comment="明确允许写入的字段")
    modes: Mapped[list] = mapped_column(JSON, nullable=False, comment="允许 insert/update，不开放任意 SQL")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/disabled/deleted")


class WriteOperation(OwnedRecord, Base):
    """Durable intent and observed commit outcome for one execution node.

    中文补充：写入凭据（回执）。唯一约束 uq_workflow_write_once 保证同一运行的同名节点
    只会有一条记录，因此重复执行走"读回执"而不是"再写一次"。
    status 语义：prepared（已登记意图，尚未提交）/ committing（已发出提交请求）/
    succeeded / failed（回滚完成，目标库无变化）/ uncertain（发出提交但结果未知）。
    """
    __tablename__ = "workflow_write_operations"
    __table_args__ = (UniqueConstraint("execution_id", "node_id", name="uq_workflow_write_once"),)
    target_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_write_targets.id"), nullable=False, comment="写入目标")
    datasource_id: Mapped[str] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=False, comment="实际数据源，供历史鉴权")
    execution_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_executions.id"), nullable=False, index=True, comment="所属运行")
    node_id: Mapped[str] = mapped_column(String(80), nullable=False, comment="流程节点")
    status: Mapped[str] = mapped_column(String(20), default="prepared", nullable=False, comment="prepared/committing/succeeded/failed/uncertain")
    requested_rows: Mapped[int] = mapped_column(Integer, nullable=False, comment="预定写入行数")
    affected_rows: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="确认提交行数，未确认不冒充零")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="目标、版本、映射及数据摘要")
    error_message: Mapped[str] = mapped_column(String(2000), default="", nullable=False, comment="脱敏错误或提交不确定说明")
