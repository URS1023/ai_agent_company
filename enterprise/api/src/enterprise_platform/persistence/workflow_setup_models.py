"""Additive 0003 enterprise metadata, separate from published earlier migrations."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SetupBase(DeclarativeBase):
    pass


class WorkflowSetupRow(SetupBase):
    __tablename__ = "enterprise_workflow_setups"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_workflow_setup_request"),
        CheckConstraint("revision > 0", name="ck_workflow_setup_revision"),
        CheckConstraint("expected_source_revision > 0", name="ck_workflow_setup_source_revision"),
        CheckConstraint(
            "expected_binding_revision IS NULL OR expected_binding_revision > 0",
            name="ck_workflow_setup_binding_revision",
        ),
        CheckConstraint(
            "state IN ('queued', 'importing', 'draft_ready', 'confirmation_required', 'uncertain', 'failed')",
            name="ck_workflow_setup_state",
        ),
        CheckConstraint(
            "(state = 'importing' AND import_nonce IS NOT NULL) OR (state <> 'importing' AND import_nonce IS NULL)",
            name="ck_workflow_setup_nonce",
        ),
        CheckConstraint(
            "state NOT IN ('queued', 'importing') OR (app_id IS NULL AND import_id IS NULL)",
            name="ck_workflow_setup_pending_ids",
        ),
        CheckConstraint(
            "state <> 'draft_ready' OR (app_id IS NOT NULL AND import_id IS NOT NULL)",
            name="ck_workflow_setup_draft_ids",
        ),
        CheckConstraint(
            "state <> 'confirmation_required' OR import_id IS NOT NULL", name="ck_workflow_setup_confirmation_id"
        ),
        Index("ix_workflow_setup_device_scenario", "workspace_id", "device_id", "scenario", "created_at", "setup_id"),
    )
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    setup_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    read_id: Mapped[str] = mapped_column(String(128), nullable=False)
    read_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_binding_revision: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    app_id: Mapped[str | None] = mapped_column(String(128))
    import_id: Mapped[str | None] = mapped_column(String(128))
    reason_code: Mapped[str | None] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    import_nonce: Mapped[str | None] = mapped_column(String(128))
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
