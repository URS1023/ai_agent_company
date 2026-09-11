"""Additive 0002 metadata, deliberately separate from the published 0001 model set."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SourceBase(DeclarativeBase):
    pass


class SourceHeadRow(SourceBase):
    __tablename__ = "enterprise_source_heads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_source_create_request"),
        UniqueConstraint("workspace_id", "read_id", name="uq_source_read_identity"),
        CheckConstraint("revision > 0", name="ck_source_head_revision"),
    )
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    read_id: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)


class SourceVersionRow(SourceBase):
    __tablename__ = "enterprise_source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["enterprise_source_heads.workspace_id", "enterprise_source_heads.source_id"],
            name="fk_source_version_tenant",
        ),
        UniqueConstraint(
            "workspace_id", "source_id", "source_revision", "read_id", "read_revision", name="uq_source_read_version"
        ),
        CheckConstraint("revision > 0", name="ck_source_version_revision"),
    )
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    read_id: Mapped[str] = mapped_column(String(256), nullable=False)
    read_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_nonce: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
