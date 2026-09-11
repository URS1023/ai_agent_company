"""Independent enrollment metadata; importing this module performs no migration."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EnrollmentBase(DeclarativeBase):
    pass


class WorkflowEnrollmentRow(EnrollmentBase):
    __tablename__ = "enterprise_workflow_enrollments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provisioning_id", name="uq_workflow_enrollment_provisioning"),
        UniqueConstraint("workspace_id", "app_id", "secret_ref", name="uq_workflow_enrollment_secret"),
        CheckConstraint("revision > 0", name="ck_workflow_enrollment_revision"),
        CheckConstraint(
            "state IN ('pending_verification', 'verification_claimed', 'verified', "
            "'token_claimed', 'token_stored', 'rejected', 'uncertain')",
            name="ck_workflow_enrollment_state",
        ),
        CheckConstraint(
            "(state IN ('verification_claimed', 'token_claimed') AND claim_nonce IS NOT NULL) OR "
            "(state NOT IN ('verification_claimed', 'token_claimed') AND claim_nonce IS NULL)",
            name="ck_workflow_enrollment_nonce",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_workflow_enrollment_chronology"),
    )

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enrollment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provisioning_id: Mapped[str] = mapped_column(String(36), nullable=False)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_nonce: Mapped[str | None] = mapped_column(String(36))
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
