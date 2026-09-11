"""Enterprise-only SQLAlchemy metadata for tenant-scoped transactional repositories.

JSON is canonical text so persisted hashes do not depend on database JSON rewriting.
Bindings own a compare-and-swap dispatch slot; uncertain runs keep that slot occupied.
Input/spec and completed result documents are append-only through repository operations.
No engine, session, database connection or migration runs when this module is imported.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Dedicated business metadata; never registered with Dify's native database."""


class DeviceRow(Base):
    __tablename__ = "enterprise_devices"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_device_revision"),
        UniqueConstraint("workspace_id", "device_code", name="uq_device_code"),
    )

    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    device_code: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    device_json: Mapped[str] = mapped_column(Text, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BindingRow(Base):
    __tablename__ = "enterprise_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "device_id", "scenario", name="uq_binding_lane"),
        ForeignKeyConstraint(
            ["workspace_id", "device_id"],
            ["enterprise_devices.workspace_id", "enterprise_devices.device_id"],
            name="fk_binding_device_tenant",
        ),
        CheckConstraint("revision > 0", name="ck_binding_revision"),
    )

    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_json: Mapped[str] = mapped_column(Text, nullable=False)
    active_run_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunRow(Base):
    __tablename__ = "enterprise_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uq_run_tenant_identity"),
        UniqueConstraint("workspace_id", "request_key", name="uq_run_request"),
        UniqueConstraint("workspace_id", "dispatch_nonce", name="uq_run_dispatch_nonce"),
        ForeignKeyConstraint(
            ["workspace_id", "binding_id"],
            ["enterprise_bindings.workspace_id", "enterprise_bindings.binding_id"],
            name="fk_run_binding_tenant",
        ),
        CheckConstraint(
            "state IN ('queued', 'claimed', 'dispatched', 'uncertain', 'succeeded', 'failed', 'cancelled')",
            name="ck_run_state",
        ),
        CheckConstraint(
            "(result_json IS NULL AND result_digest IS NULL) OR "
            "(result_json IS NOT NULL AND result_digest IS NOT NULL)",
            name="ck_result_evidence_pair",
        ),
        CheckConstraint(
            "(input_json IS NULL AND input_digest IS NULL) OR (input_json IS NOT NULL AND input_digest IS NOT NULL)",
            name="ck_input_evidence_pair",
        ),
        CheckConstraint("revision > 0", name="ck_run_revision"),
        Index("ix_run_fifo", "workspace_id", "binding_id", "state", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(256), nullable=False)
    run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    binding_id: Mapped[str] = mapped_column(String(256), nullable=False)
    device_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[str | None] = mapped_column(Text)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    dispatch_nonce: Mapped[str | None] = mapped_column(String(256))
    dify_run_id: Mapped[str | None] = mapped_column(String(256))
    reason_code: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[str | None] = mapped_column(Text)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "enterprise_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["enterprise_runs.workspace_id", "enterprise_runs.run_id"],
            name="fk_audit_run_tenant",
        ),
        Index("ix_audit_run_sequence", "workspace_id", "run_id", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(256))
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
