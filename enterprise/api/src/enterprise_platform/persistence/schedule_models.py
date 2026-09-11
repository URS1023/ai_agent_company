"""Independent schedule metadata; importing never connects or applies a schema."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ScheduleBase(DeclarativeBase):
    pass


class ScheduleRow(ScheduleBase):
    __tablename__ = "enterprise_schedules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "binding_id", name="uq_schedule_binding_owner"),
        UniqueConstraint("workspace_id", "device_id", "scenario", name="uq_schedule_lane_owner"),
        CheckConstraint("revision > 0 AND binding_revision > 0", name="ck_schedule_revisions"),
        CheckConstraint("updated_at >= created_at", name="ck_schedule_chronology"),
        CheckConstraint("scenario IN ('alert', 'quality')", name="ck_schedule_scenario"),
        Index("ix_schedule_due", "enabled", "next_due_at"),
    )
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(256), nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    service_actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
