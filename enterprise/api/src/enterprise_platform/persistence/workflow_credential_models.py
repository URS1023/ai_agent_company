"""Additive credential metadata, independent of earlier schema versions."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CredentialBase(DeclarativeBase):
    pass


class WorkflowCredentialRow(CredentialBase):
    __tablename__ = "enterprise_workflow_credentials"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_workflow_credential_revision"),
        CheckConstraint("length(key_id) BETWEEN 1 AND 64", name="ck_workflow_credential_key"),
        CheckConstraint("length(nonce) = 16", name="ck_workflow_credential_nonce"),
        CheckConstraint("length(ciphertext) BETWEEN 24 AND 5484", name="ck_workflow_credential_payload"),
        Index("ix_workflow_credential_active", "workspace_id", "app_id", "active"),
    )
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    secret_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(16), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    public_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
