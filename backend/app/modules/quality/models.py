"""Additive quality records; detector evidence and human reviews are immutable.

职责：质检业务域的持久化模型 —— 指标、模板与模板修订、检测证据、检验记录与人工复核，
以及向导回执和让步（特采）规则/申请。

位置与依赖：与 ``alerts.models.Device`` 共用设备表（设备 ``domains`` 必须含 quality）；
检测数据源来自 ``data_sources``；工作流来源为 ``workflow_executions`` / ``workflow_definitions``。

关键不变式（模型层面保证）：
- **追加式**：复核（``QualityReview``）只新增、不覆盖，检验记录里的 ``results`` 是原始判定，
  "考虑复核后的结论"由服务层计算，不落库覆盖原文；
- 不可变证据：``QualityDetection.results`` 与 ``QualityInspection.snapshot`` 一经写入不修改；
- 唯一约束即业务约束：模板修订 ``(template_id, revision)``、每执行每节点的检测证据
  ``(execution_id, node_id)``、检验提交与让步提交的 ``(workspace_id, owner_user_id, request_key)``；
- 归档用状态（``active`` / ``archived``），原始证据不物理删除；让步规则代码工作区内唯一。
"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class QualityMetric(OwnedRecord, Base):
    """质检指标定义：数值/成分/外观三类，标准用精确文本保存。

    类别与字段的匹配规则见 ``schemas.MetricInput``：外观指标只认缺陷级别与最低置信度，
    数值/成分必须配置标准界限。修改指标不影响已保存的模板修订（模板快照里固化了标准）。
    """
    __tablename__ = "quality_metrics"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="指标名称")
    category: Mapped[str] = mapped_column(String(20), nullable=False, comment="numeric/component/appearance")
    unit: Mapped[str] = mapped_column(String(40), default="", nullable=False, comment="明确计量单位")
    standard_min: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="精确标准下限")
    standard_max: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="精确标准上限")
    component_name: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="成分指标的唯一成分名")
    minimum_confidence: Mapped[str] = mapped_column(String(120), default="0.8", nullable=False, comment="外观自动判定最低置信度")
    blocking_severities: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="明确不合格的缺陷严重度")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="标准及说明")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class QualityTemplate(OwnedRecord, Base):
    """质检模板：有序指标选择的"当前版本"，历史版本在 ``QualityTemplateRevision`` 中。

    ``metric_ids`` 的顺序即报告逐项展示顺序；质检记录固定引用某个模板修订，
    因此模板后续改动不会影响历史记录的标准。
    """
    __tablename__ = "quality_templates"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="模板名称")
    device_type: Mapped[str] = mapped_column(String(100), default="", nullable=False, comment="适用设备类别，空为不限")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="检验标准说明")
    metric_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="明确选择且有序的指标")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class QualityTemplateRevision(OwnedRecord, Base):
    """模板修订：模板 + 完整指标标准的不可变快照，是判定口径的唯一依据。

    ``(template_id, revision)`` 唯一；修订号取自保存时的模板版本，可能跳号（不保证连续）。
    """
    __tablename__ = "quality_template_revisions"
    __table_args__ = (UniqueConstraint("template_id", "revision", name="uq_quality_template_revision"),)
    template_id: Mapped[str] = mapped_column(String(26), ForeignKey("quality_templates.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="不可变修订号")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="模板及完整指标标准快照")


class QualityDetection(OwnedRecord, Base):
    """一次真实检测节点运行的证据（指标 + 设备 + 服务版本 + 规范化结果）。

    ``(execution_id, node_id)`` 唯一：一次执行的一个节点只落一份证据。
    ``snapshot`` 固定指标、设备、数据源版本与查询标识，便于脱离当前配置追溯；
    ``results`` 为空**不等于合格**（表示没有拿到任何可用样本结果），判定层按未检测处理。
    写入只由 ``quality.detection.detect`` 在全部校验通过后发生，永不修改。
    """
    __tablename__ = "quality_detections"
    __table_args__ = (UniqueConstraint("execution_id", "node_id", name="uq_quality_detection_node"),)
    device_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_devices.id"), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(26), ForeignKey("quality_metrics.id"), nullable=False)
    datasource_id: Mapped[str] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_executions.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="检测设备、指标、连接版本及查询标识")
    results: Mapped[list] = mapped_column(JSON, nullable=False, comment="实际规范化结果，空结果不等于检验合格")


class QualityInspection(OwnedRecord, Base):
    """一次质检记录：固定模板修订与来源，``results`` 保存原始逐项判定。

    ``(workspace_id, owner_user_id, request_key)`` 唯一：同一账号的同一提交键只落一条
    （防网络重试重复提交）；``execution_id`` 为空表示人工录入（不冒充设备证据）。
    ``outcome`` 是**不可变原始结论**（passed/failed/review/incomplete），
    人工复核不修改它，只追加 ``QualityReview``；``status`` 用 archivable 的软状态，不物理删除。
    """
    __tablename__ = "quality_inspections"
    __table_args__ = (UniqueConstraint("workspace_id", "owner_user_id", "request_key", name="uq_quality_submission"),)
    template_id: Mapped[str] = mapped_column(String(26), ForeignKey("quality_templates.id"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("workflow_executions.id"), nullable=True)
    node_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    sample_id: Mapped[str] = mapped_column(String(160), nullable=False, comment="样本或批次标识")
    request_key: Mapped[str] = mapped_column(String(160), nullable=False, comment="防重复提交标识")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, comment="passed/failed/review/incomplete")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="当时设备、模板修订、标准与检测来源")
    results: Mapped[list] = mapped_column(JSON, nullable=False, comment="逐项原始结果，不受复核改写")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived，原始证据不物理删除")


class QualityReview(OwnedRecord, Base):
    """人工复核记录（追加式）：一条 = 一次针对某指标的复核结论。

    ``decision`` 为 passed/failed/retest；``note`` 必填（复核依据必须留痕）；
    ``inspection_version`` 记录复核时检验记录的版本，用于审计"这条复核针对哪一版证据"。
    本表只增不改：原始判定永远保留，当前有效结论由服务层按最新复核计算。
    """
    __tablename__ = "quality_reviews"
    inspection_id: Mapped[str] = mapped_column(String(26), ForeignKey("quality_inspections.id"), nullable=False, index=True)
    metric_id: Mapped[str] = mapped_column(String(26), ForeignKey("quality_metrics.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, comment="passed/failed/retest")
    note: Mapped[str] = mapped_column(Text, nullable=False, comment="必填人工复核证据与理由")
    inspection_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="对应的检验记录变更版本")


class QualityWorkflowCreation(OwnedRecord, Base):
    """Idempotency receipt for a normal-page workflow creation request."""

    __tablename__ = "quality_workflow_creations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_user_id", "request_key", name="uq_quality_workflow_request"),
    )
    workflow_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workflow_definitions.id"), nullable=False, unique=True, comment="创建并发布的共享工作流",
    )
    request_key: Mapped[str] = mapped_column(String(160), nullable=False, comment="客户端保留的防重复创建键")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="除防重复键外的完整配置摘要")
    published_revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="首次发布的不可变修订")


class QualityConcessionRule(OwnedRecord, Base):
    """Explicit, versioned business rule for an incoming-quality concession."""

    __tablename__ = "quality_concession_rules"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_quality_concession_rule_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="业务规则编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="规则名称")
    product_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="适用产品编码；空表示明确配置的全产品范围")
    metric_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="必须同时出现且不合格的质检指标")
    weight_unit: Mapped[str] = mapped_column(String(40), nullable=False, comment="原始重量单位")
    metric_unit: Mapped[str] = mapped_column(String(40), nullable=False, comment="单个实测指标单位；多个指标需分别建规则")
    standard_min: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="可选的精确标准下限匹配")
    standard_max: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="可选的精确标准上限匹配")
    min_deviation: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="可选的实测偏差下限")
    max_deviation: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="可选的实测偏差上限")
    deduction_mode: Mapped[str] = mapped_column(String(20), nullable=False, comment="rate 或 weight")
    deduction_value: Mapped[str] = mapped_column(String(120), nullable=False, comment="精确扣量比例或重量")
    rounding_scale: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="可选结算重量小数位；空值保留精确结果")
    rounding_mode: Mapped[str] = mapped_column(String(20), default="exact", nullable=False, comment="exact/half_up/half_even/down/up")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="业务确认说明")


class QualityConcessionRequest(OwnedRecord, Base):
    """Immutable evidence and approval state for one concession proposal."""

    __tablename__ = "quality_concession_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_user_id", "request_key", name="uq_quality_concession_request"),
    )
    request_key: Mapped[str] = mapped_column(String(160), nullable=False, comment="防重复提交键")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="证据与规则参数摘要")
    inspection_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("quality_inspections.id"), nullable=True, index=True)
    rule_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("quality_concession_rules.id"), nullable=True, index=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="申请生成时的规则版本")
    rule_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="申请生成时的完整规则快照")
    sample_id: Mapped[str] = mapped_column(String(160), nullable=False, comment="样本/来料单据标识")
    product_code: Mapped[str] = mapped_column(String(160), default="", nullable=False, comment="产品编码快照")
    weight_unit: Mapped[str] = mapped_column(String(40), nullable=False, comment="原始重量及结算重量单位")
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="已授权来源记录、字段路径和快照标识")
    measurements: Mapped[list] = mapped_column(JSON, nullable=False, comment="实测值和固定标准快照")
    original_weight: Mapped[str | None] = mapped_column(String(600), nullable=True, comment="原始重量精确文本")
    deviation: Mapped[str | None] = mapped_column(String(600), nullable=True, comment="命中的最大标准偏差")
    proposed_deduction: Mapped[str | None] = mapped_column(String(600), nullable=True, comment="审批前候选扣量")
    proposed_settlement_weight: Mapped[str | None] = mapped_column(String(600), nullable=True, comment="审批前候选结算重量")
    settlement_weight: Mapped[str | None] = mapped_column(String(600), nullable=True, comment="仅批准后可用于结算的重量")
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="needs_confirmation/pending/approved/rejected")
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="缺失、匹配或计算说明")
    decision_note: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="审批意见")
    decided_by: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="审批人")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审批时间 UTC")
