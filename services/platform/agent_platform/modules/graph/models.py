"""实体和关系独立建模，图定义与本体采用版本快照。"""

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.database import Base
from ...core.models import new_id, now_iso


class GraphEntity(Base):
    __tablename__ = 'graph_entities'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_id: Mapped[str] = mapped_column(ForeignKey('resource_records.id'), index=True)
    name: Mapped[str] = mapped_column(String(240))
    type: Mapped[str] = mapped_column(String(120))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class GraphRelation(Base):
    __tablename__ = 'graph_relations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_id: Mapped[str] = mapped_column(ForeignKey('resource_records.id'), index=True)
    source: Mapped[str] = mapped_column(ForeignKey('graph_entities.id'))
    target: Mapped[str] = mapped_column(ForeignKey('graph_entities.id'))
    type: Mapped[str] = mapped_column(String(120))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
