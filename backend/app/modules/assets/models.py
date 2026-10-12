"""Workspace asset annotations, terms, metrics and cross-resource relations."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class AssetAnnotation(OwnedRecord, Base):
    """Business metadata layered over a real resource without copying that resource."""
    # 资产标注：叠加在真实资源之上的业务语义（别名、业务域、描述、标签、负责人、敏感级、字段说明）。
    # 与资源本身解耦：资源被删除或换实现后，标注仍在，靠 (asset_type, asset_id) 关联；归档保留历史。
    #

    __tablename__ = "asset_annotations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "resource_type", "resource_id", name="uq_asset_annotation_resource"),
    )
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True, comment="目录登记的稳定资源类型")
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True, comment="原业务资源标识")
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="用于检索的业务别名")
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="用户维护的目录分类")
    business_domain: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="所属业务域")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="业务说明；不覆盖原资源内容")
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="可检索标签")
    steward_user_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=True, comment="数据责任人；必须是当前工作区成员",
    )
    sensitivity: Mapped[str] = mapped_column(
        String(24), default="internal", nullable=False, comment="public/internal/confidential/restricted",
    )
    freshness_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="允许的最大数据年龄；空值表示未配置",
    )
    field_descriptions: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False, comment="字段路径到业务说明的映射，如 orders.amount",
    )
    search_text: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="规范化检索文本，不含凭据")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived")


class BusinessTerm(OwnedRecord, Base):
    """A governed vocabulary item used by people, metrics and AI discovery."""
    # 业务术语（词表项）：统一各业务方对同一概念的说法，含定义与同义词。
    #

    __tablename__ = "asset_business_terms"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_asset_term_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定业务术语编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="术语名称")
    definition: Mapped[str] = mapped_column(Text, nullable=False, comment="业务口径定义")
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="同义词和常用叫法")
    business_domain: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="所属业务域")
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived")


class MetricDefinition(OwnedRecord, Base):
    """A documented metric formula bound to explicitly authorized source assets."""
    # 指标定义：口径、单位、计算说明与来源引用的治理记录，避免同名指标口径不一致。
    #

    __tablename__ = "asset_metric_definitions"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_asset_metric_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定指标编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="指标名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="指标口径说明")
    formula: Mapped[str] = mapped_column(Text, nullable=False, comment="展示和生成查询使用的公式；目录接口不直接执行")
    unit: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    aggregation: Mapped[str] = mapped_column(String(32), default="custom", nullable=False)
    granularity: Mapped[str] = mapped_column(String(80), default="", nullable=False, comment="日/月/设备/组织等统计粒度")
    dimensions: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="允许的分析维度")
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="显式资源类型、资源标识与可选字段")
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived")


class AssetRelation(OwnedRecord, Base):
    """A governed lineage or semantic edge between two real catalog resources."""
    # 资产之间的关系（如"该报表使用此指标"），用于资产目录的关系视图与影响面分析。
    # 软删除：状态置为归档而不是删行，保证历史关系可追溯。
    #

    __tablename__ = "asset_relations"
    __table_args__ = (UniqueConstraint(
        "workspace_id", "source_type", "source_id", "target_type", "target_id", "relation_kind",
        name="uq_asset_relation_edge",
    ),)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    relation_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="depends_on/derived_from/contains/related_to",
    )
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/archived")

