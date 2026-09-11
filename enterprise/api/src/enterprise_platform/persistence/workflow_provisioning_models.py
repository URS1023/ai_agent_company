"""Additive provisioning journal metadata, independent of draft-import tables."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ProvisioningBase(DeclarativeBase):
    pass


class WorkflowProvisioningRow(ProvisioningBase):
    __tablename__ = "enterprise_workflow_provisioning"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_workflow_provisioning_request"),
        CheckConstraint(
            "revision > 0 AND setup_revision > 0 AND config_revision > 0 AND expected_source_revision > 0",
            name="ck_workflow_provisioning_revisions",
        ),
        CheckConstraint(
            "expected_binding_revision IS NULL OR expected_binding_revision > 0",
            name="ck_workflow_provisioning_binding_revision",
        ),
        CheckConstraint(
            "state IN ('in_progress', 'rejected', 'uncertain', 'published_pending_enrollment')",
            name="ck_workflow_provisioning_state",
        ),
        CheckConstraint("phase_count BETWEEN 0 AND 4", name="ck_workflow_provisioning_phase_count"),
        CheckConstraint(
            "(phase_count = 0 AND active_phase IS NULL AND phase_state IS NULL) "
            "OR (phase_count > 0 AND active_phase IS NOT NULL AND phase_state IS NOT NULL)",
            name="ck_workflow_provisioning_phase_presence",
        ),
        CheckConstraint(
            "active_phase IS NULL OR active_phase IN ('read_draft', 'prepare_credential', "
            "'bind_credential', 'publish')",
            name="ck_workflow_provisioning_phase",
        ),
        CheckConstraint(
            "phase_state IS NULL OR phase_state IN ('queued', 'claimed', 'succeeded', 'rejected', 'uncertain')",
            name="ck_workflow_provisioning_phase_state",
        ),
        CheckConstraint(
            "(phase_state IS NOT NULL AND phase_state = 'claimed' AND claim_nonce IS NOT NULL) "
            "OR ((phase_state IS NULL OR phase_state <> 'claimed') AND claim_nonce IS NULL)",
            name="ck_workflow_provisioning_nonce",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_workflow_provisioning_chronology"),
        Index("ix_workflow_provisioning_setup", "workspace_id", "setup_id", "created_at", "provisioning_id"),
    )
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provisioning_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    setup_id: Mapped[str] = mapped_column(String(128), nullable=False)
    setup_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    read_id: Mapped[str] = mapped_column(String(128), nullable=False)
    read_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_binding_revision: Mapped[int | None] = mapped_column(Integer)
    config_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    active_phase: Mapped[str | None] = mapped_column(String(32))
    phase_state: Mapped[str | None] = mapped_column(String(16))
    phase_count: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_nonce: Mapped[str | None] = mapped_column(String(36))
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
