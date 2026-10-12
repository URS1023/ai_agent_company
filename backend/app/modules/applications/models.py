"""Persistent application drafts, immutable releases and launch history."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class BusinessApplication(OwnedRecord, Base):
    """One managed solution with a stable code and an independently published release."""
    # 一个被治理的"应用/解决方案"主体：有稳定 code、可独立发布的正式版本、以及启动历史。
    # 生命周期：draft → published（有正式版本）→ disabled（停用，仍保留版本与历史）→ archived（归档）。
    #

    __tablename__ = "business_applications"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_business_application_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="工作区内稳定应用编码")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="应用名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="业务用途说明")
    kind: Mapped[str] = mapped_column(
        String(24), default="solution", nullable=False,
        comment="assistant/automation/dashboard/solution",
    )
    visibility: Mapped[str] = mapped_column(
        String(24), default="workspace", nullable=False, comment="workspace/restricted/private",
    )
    status: Mapped[str] = mapped_column(
        String(24), default="draft", nullable=False, comment="draft/published/disabled/archived",
    )
    published_version_id: Mapped[str | None] = mapped_column(
        String(26), nullable=True, index=True, comment="当前正式发布的不可变版本",
    )
    launch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_launched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApplicationVersion(OwnedRecord, Base):
    """Immutable application composition; editing always creates a new version."""
    # 应用的不可变发布版本：spec_json 是发布时冻结的规格（含资源绑定与工作流修订号）。
    # 不变式：同一应用内 version_number 递增且唯一；同一时刻只有一个 status=published，旧版本被置 retired。
    #

    __tablename__ = "business_application_versions"
    __table_args__ = (UniqueConstraint("application_id", "version_number", name="uq_business_application_version"),)
    application_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("business_applications.id"), nullable=False, index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("business_application_versions.id"), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="draft", nullable=False, comment="draft/published/retired",
    )
    spec_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="入口、依赖、说明、输入输出与受众的完整快照",
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApplicationLaunch(OwnedRecord, Base):
    """Auditable application entry resolution linked to the unified run center."""
    # 一次应用启动的历史记录：谁、以什么参数、在哪个版本上启动过，以及资源快照。
    # 用途：审计与追溯；幂等键也存放在这里的清单字段中。
    #

    __tablename__ = "business_application_launches"
    application_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("business_applications.id"), nullable=False, index=True,
    )
    application_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("business_application_versions.id"), nullable=False, index=True,
    )
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("runs.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="ui", nullable=False, comment="ui/ai/api")
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="本次实际启动的版本、资源和站内入口快照",
    )
    launch_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)

