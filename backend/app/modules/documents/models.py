"""Persistent document generation requests and append-only stage events."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class DocumentGeneration(OwnedRecord, Base):
    """One editable draft which becomes immutable after a real artifact is produced."""
    # 一次文档/PPT/表格/PDF 生成请求。
    # 生命周期：draft（可编辑草稿）→ running（执行中）→ completed（已产出不可变产物）/ failed（可修正后重跑）；
    # completed 记录再归档为 archived。生成成功后关键字段被冻结：后续编辑只能通过 copy_generation 复制为新草稿，
    # 保证产物与查询条件的对应关系不被改写。
    #

    __tablename__ = "document_generations"
    name: Mapped[str] = mapped_column(String(240), nullable=False, comment="生成任务及输出文件名称")
    output_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="document/presentation/spreadsheet/pdf")
    mode: Mapped[str] = mapped_column(String(16), default="manual", nullable=False, comment="manual/ai")
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="AI 编写要求；人工模式可为空")
    spec_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="实际渲染的结构化内容规格")
    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="PPT 明确选择的内置或客户模板；无默认值")
    requested_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="PPT 指定总页数，含封面与结束页")
    actual_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="生成后从真实文件读取的页或工作表数量")
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="用户明确选择的目录资源和字段")
    source_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="执行时可见来源的脱敏快照")
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/running/completed/failed/archived")
    stage: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="当前真实处理阶段")
    artifact_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("artifacts.id"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("runs.id"), nullable=True, index=True)
    model_usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="AI 模式的模型名称、用量和调用信息")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="脱敏失败说明")


class DocumentGenerationEvent(OwnedRecord, Base):
    """Append-only, user-visible stage history for one generation."""
    # 生成过程的只追加阶段事件（用户可见的执行历史）。
    # 不变式：(generation_id, sequence) 唯一且递增；details 中不得出现正文、凭据或模型密钥。
    #

    __tablename__ = "document_generation_events"
    __table_args__ = (UniqueConstraint("generation_id", "sequence", name="uq_document_generation_event"),)
    generation_id: Mapped[str] = mapped_column(String(26), ForeignKey("document_generations.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, comment="running/completed/failed")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="不含正文、凭据或模型密钥的阶段摘要")

