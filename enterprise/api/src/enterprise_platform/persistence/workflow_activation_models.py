"""Independent activation metadata; no engine creation or automatic migration."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ActivationBase(DeclarativeBase):
    pass


class WorkflowActivationRow(ActivationBase):
    __tablename__ = "enterprise_workflow_activations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "enrollment_id", name="uq_workflow_activation_enrollment"),
        UniqueConstraint("key_id", "workflow_id", name="uq_workflow_activation_key_version"),
        CheckConstraint(
            "(active AND revision = 1) OR (NOT active AND revision = 2)", name="ck_workflow_activation_state"
        ),
        CheckConstraint("binding_revision > 0", name="ck_workflow_activation_binding_revision"),
        CheckConstraint("updated_at >= created_at", name="ck_workflow_activation_chronology"),
    )
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    activation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enrollment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    key_id: Mapped[str] = mapped_column(String(67), nullable=False)
    binding_id: Mapped[str] = mapped_column(String(256), nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
