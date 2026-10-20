"""节能运行保存固定输入、来源与结果；名称/软删除不改写计算事实。"""
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base
from ..models import Entity


class EnergyAnalysis(Entity, Base):
    __tablename__ = "manufacturing_energy_analyses"
    name: Mapped[str] = mapped_column(String(160))
    device_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_devices.id"), index=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("data_queries.id"), index=True)
    request_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    created_by: Mapped[str] = mapped_column(String(36))
