"""Office source authorization metadata, separate from published migration 0014.

Snapshots are append-only, captured by trusted source adapters, never accepted from
model output. Source grant mutations must lock their source head first; file work
locks file head then source heads in sorted order. Native/source revocation must
join this protocol before the policy can be enabled in production. No startup DDL.
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKeyConstraint, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OfficeSourceBase(DeclarativeBase):
    pass


class OfficeSourceRow(OfficeSourceBase):
    __tablename__ = "enterprise_office_sources"
    __table_args__ = (CheckConstraint("acl_revision > 0", name="ck_office_source_acl_revision"),)
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acl_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)


class OfficeSourceGrantRow(OfficeSourceBase):
    __tablename__ = "enterprise_office_source_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["enterprise_office_sources.workspace_id", "enterprise_office_sources.source_id"],
            name="fk_office_source_grant",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False)


class OfficeSnapshotRow(OfficeSourceBase):
    __tablename__ = "enterprise_office_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["enterprise_office_sources.workspace_id", "enterprise_office_sources.source_id"],
            name="fk_office_snapshot_source",
        ),
        CheckConstraint("length(payload_hash) = 64", name="ck_office_snapshot_hash"),
    )
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
