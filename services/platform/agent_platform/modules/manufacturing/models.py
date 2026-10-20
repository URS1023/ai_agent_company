"""设备、标准快照、样本证据与处置分别建模，修改业务资源不改写历史。"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def now():
    return datetime.now(timezone.utc)


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    archived: Mapped[bool] = mapped_column(default=False)


class Device(Entity, Base):
    __tablename__ = "manufacturing_devices"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    device_type: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")


class MeasurementPoint(Entity, Base):
    __tablename__ = "manufacturing_points"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    device_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_devices.id"), index=True)
    unit: Mapped[str] = mapped_column(String(40), default="")
    datasource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")


class Product(Entity, Base):
    __tablename__ = "manufacturing_products"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    specification: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")


class ProductBatch(Entity, Base):
    __tablename__ = "manufacturing_batches"
    name: Mapped[str] = mapped_column(String(160))
    product_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_products.id"), index=True)
    external_ref: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InspectionTemplate(Entity, Base):
    __tablename__ = "manufacturing_inspection_templates"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    target_type: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="active")


class TemplateRevision(Base):
    __tablename__ = "manufacturing_template_revisions"
    __table_args__ = (UniqueConstraint("template_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    template_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspection_templates.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Inspection(Entity, Base):
    __tablename__ = "manufacturing_inspections"
    name: Mapped[str] = mapped_column(String(160))
    target_type: Mapped[str] = mapped_column(String(20))
    template_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspection_templates.id"))
    template_revision: Mapped[int] = mapped_column(Integer)
    standard_snapshot: Mapped[dict] = mapped_column(JSON)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("manufacturing_devices.id"), nullable=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("manufacturing_products.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("manufacturing_batches.id"), nullable=True)
    expected_samples: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    outcome: Mapped[str] = mapped_column(String(30), default="incomplete")
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class InspectionSample(Entity, Base):
    __tablename__ = "manufacturing_samples"
    __table_args__ = (UniqueConstraint("inspection_id", "external_ref"),)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspections.id"), index=True)
    external_ref: Mapped[str] = mapped_column(String(160))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SampleEvidence(Base):
    __tablename__ = "manufacturing_sample_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspections.id"), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_samples.id"), index=True)
    observations: Mapped[list] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InspectionReview(Base):
    __tablename__ = "manufacturing_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspections.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="review")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InspectionRun(Base):
    __tablename__ = "manufacturing_inspection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_inspections.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaintenanceAnalysis(Entity, Base):
    __tablename__ = "manufacturing_analyses"
    name: Mapped[str] = mapped_column(String(160))
    device_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_devices.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    request_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)


class AlertRule(Entity, Base):
    __tablename__ = "manufacturing_alert_rules"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    device_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_devices.id"), index=True)
    point_id: Mapped[str | None] = mapped_column(ForeignKey("manufacturing_points.id"), nullable=True)
    operator: Mapped[str] = mapped_column(String(10))
    threshold: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(40))
    dedupe_minutes: Mapped[int] = mapped_column(Integer, default=60)


class Incident(Entity, Base):
    __tablename__ = "manufacturing_incidents"
    rule_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_alert_rules.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_devices.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="open")
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    evidence: Mapped[dict] = mapped_column(JSON)


class IncidentEvent(Base):
    __tablename__ = "manufacturing_incident_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)
    incident_id: Mapped[str] = mapped_column(ForeignKey("manufacturing_incidents.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SimulationActivation(Entity, Base):
    __tablename__ = "manufacturing_simulation_activations"
    generator_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    parameters: Mapped[dict] = mapped_column(JSON)
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    device_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
