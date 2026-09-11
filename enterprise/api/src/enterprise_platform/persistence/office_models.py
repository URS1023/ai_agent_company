"""Isolated Office metadata. Importing this module never creates or migrates tables.

File heads serialize ACL and content writes; historical documents and receipts
are append-only through repository operations. No schema grants immutability to
arbitrary database writers: deployment permissions remain necessary.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OfficeBase(DeclarativeBase):
    pass


class _FileScope:
    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True, sort_order=-2)
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True, sort_order=-1)


class OfficeFileRow(_FileScope, OfficeBase):
    __tablename__ = "enterprise_office_files"
    __table_args__ = (
        CheckConstraint("current_revision > 0", name="ck_office_current_revision"),
        CheckConstraint("acl_revision > 0", name="ck_office_acl_revision"),
    )
    current_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acl_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OfficeGrantRow(_FileScope, OfficeBase):
    __tablename__ = "enterprise_office_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "file_id"],
            ["enterprise_office_files.workspace_id", "enterprise_office_files.file_id"],
            name="fk_office_grant_file",
        ),
        CheckConstraint("NOT can_edit OR can_read", name="ck_office_edit_requires_read"),
    )
    actor_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, nullable=False)


class OfficeRevisionRow(_FileScope, OfficeBase):
    __tablename__ = "enterprise_office_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "file_id"],
            ["enterprise_office_files.workspace_id", "enterprise_office_files.file_id"],
            name="fk_office_revision_file",
        ),
        CheckConstraint("revision > 0", name="ck_office_history_revision"),
        CheckConstraint("length(document_hash) = 64", name="ck_office_document_hash"),
    )
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OfficeEditReceiptRow(_FileScope, OfficeBase):
    __tablename__ = "enterprise_office_edit_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "file_id", "result_revision"],
            [
                "enterprise_office_revisions.workspace_id",
                "enterprise_office_revisions.file_id",
                "enterprise_office_revisions.revision",
            ],
            name="fk_office_receipt_revision",
        ),
        CheckConstraint("result_revision > 1", name="ck_office_receipt_revision"),
        CheckConstraint("length(command_hash) = 64", name="ck_office_command_hash"),
    )
    actor_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
