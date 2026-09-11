"""Separate metadata for enterprise dashboards; imports never apply a schema."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DashboardBase(DeclarativeBase):
    pass


class DashboardRow(DashboardBase):
    __tablename__ = "enterprise_dashboards"
    __table_args__ = (CheckConstraint("revision > 0", name="ck_dashboard_revision"),)

    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    design_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    bindings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
