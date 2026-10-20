"""制造领域路由：台账、标准、任务证据、维护分析与预警处置。"""
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import get_settings

from . import models as m, schemas as s
from .service import ManufacturingService, csv_content, view

router = APIRouter(prefix="/api/v1", tags=["制造质检与维护"])


def _validate(schema, data):
    try:
        return schema.model_validate(data).model_dump()
    except ValidationError as exc:
        fields = {".".join(str(p) for p in e["loc"]): e["msg"] for e in exc.errors()}
        raise DomainError(422, "validation_failed", "请检查填写内容", fields) from exc


def _resource_routes(path, model, schema, permission):
    """统一台账的HTTP样板；资源约束和版本逻辑均留在领域服务。"""
    def listing(q: str = "", limit: int = 50, offset: int = 0, device_id: str | None = None, session=Depends(get_session), actor=Depends(require_permission(permission + ".read"))):
        filters = {"device_id": device_id} if hasattr(model, "device_id") and device_id else {}
        return ManufacturingService(session, actor).list(model, q=q, limit=limit, offset=offset, **filters)
    def creating(data: dict = Body(...), session=Depends(get_session), actor=Depends(require_permission(permission + ".write"))):
        return ManufacturingService(session, actor).create(model, _validate(schema, data))
    def getting(id: str, session=Depends(get_session), actor=Depends(require_permission(permission + ".read"))):
        return view(ManufacturingService(session, actor).get(model, id))
    def updating(id: str, data: dict = Body(...), session=Depends(get_session), actor=Depends(require_permission(permission + ".write"))):
        service = ManufacturingService(session, actor)
        original = view(service.get(model, id))
        merged = {**{k: v for k, v in original.items() if k in schema.model_fields}, **data}
        parsed = _validate(schema, merged)
        return service.change(model, id, parsed, data.get("revision"))
    def deleting(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission(permission + ".delete"))):
        return ManufacturingService(session, actor).archive(model, id, revision)
    for method, suffix, handler in [("GET", "", listing), ("POST", "", creating), ("GET", "/{id}", getting), ("PATCH", "/{id}", updating), ("DELETE", "/{id}", deleting)]:
        router.add_api_route(path + suffix, handler, methods=[method], name=model.__name__ + "_" + method.lower())


_resource_routes("/devices", m.Device, s.DeviceInput, "m10")
_resource_routes("/measurement-points", m.MeasurementPoint, s.PointInput, "m10")
_resource_routes("/alert-rules", m.AlertRule, s.AlertRuleInput, "m10")
_resource_routes("/products", m.Product, s.ProductInput, "m11")
_resource_routes("/inspection-templates", m.InspectionTemplate, s.TemplateInput, "m11")


@router.get("/inspection-templates/{id}/revisions")
def standard_revisions(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    ManufacturingService(session, actor).get(m.InspectionTemplate, id)
    revisions = session.scalars(select(m.TemplateRevision).where(m.TemplateRevision.template_id == id).order_by(m.TemplateRevision.number.desc())).all()
    return {"items": [view(r) for r in revisions], "total": len(revisions)}


@router.get("/devices/{id}/telemetry")
def telemetry(id: str, limit: int = 500, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    actor.require("m06.read")
    return ManufacturingService(session, actor).telemetry(id, settings, limit)


@router.get("/devices/{id}/maintenance-cases")
def maintenance_cases(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    service = ManufacturingService(session, actor)
    service.get(m.Device, id)
    return service.list(m.Incident, device_id=id)


@router.post("/maintenance-analyses", status_code=201)
def create_analysis(data: s.AnalysisInput, session=Depends(get_session), actor=Depends(require_permission("m10.write")), settings=Depends(get_settings)):
    if data.datasource_id:
        actor.require("m06.read")
    return ManufacturingService(session, actor).create_analysis(data, settings)


@router.get("/maintenance-analyses")
def analyses(q: str = "", device_id: str | None = None, limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    return ManufacturingService(session, actor).list(m.MaintenanceAnalysis, q=q, device_id=device_id, limit=limit, offset=offset)


@router.get("/maintenance-analyses/{id}")
def analysis(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    return view(ManufacturingService(session, actor).get(m.MaintenanceAnalysis, id))


@router.post("/maintenance-analyses/{id}/exports")
def export_analysis(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    service = ManufacturingService(session, actor)
    row = service.get(m.MaintenanceAnalysis, id)
    service.audit("export", "maintenance_analysis", row.id)
    return Response(csv_content(row.result["bins"]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="analysis-{id}.csv"'})


@router.post("/alert-rules/{id}/dry-runs")
def dry_run(id: str, data: s.AlertEvaluation, session=Depends(get_session), actor=Depends(require_permission("m10.write"))):
    values = data.model_dump()
    values["create_incident"] = False
    return ManufacturingService(session, actor).evaluate_alert(id, values)


@router.post("/alert-rules/{id}/evaluations")
def evaluate_alert(id: str, data: s.AlertEvaluation, session=Depends(get_session), actor=Depends(require_permission("m10.write"))):
    return ManufacturingService(session, actor).evaluate_alert(id, data.model_dump())


@router.post("/alert-rules/{id}/source-evaluations")
def evaluate_source_alert(id: str, data: s.SourceAlertEvaluation, session=Depends(get_session), actor=Depends(require_permission("m10.write")), settings=Depends(get_settings)):
    actor.require("m06.read")
    return ManufacturingService(session, actor).evaluate_source_alert(id, data, settings)


@router.get("/incidents")
def incidents(q: str = "", status: str | None = None, limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    return ManufacturingService(session, actor).list(m.Incident, q=q, status=status, limit=limit, offset=offset)


@router.get("/incidents/{id}")
def incident(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read"))):
    service = ManufacturingService(session, actor)
    row = service.get(m.Incident, id)
    events = session.scalars(select(m.IncidentEvent).where(m.IncidentEvent.incident_id == row.id).order_by(m.IncidentEvent.created_at)).all()
    return {**view(row), "events": [view(e) for e in events]}


@router.post("/incidents/{id}/actions")
def incident_action(id: str, data: s.IncidentAction, session=Depends(get_session), actor=Depends(require_permission("m10.write"))):
    return ManufacturingService(session, actor).incident_action(id, data.model_dump())


@router.get("/product-batches")
def batches(product_id: str | None = None, q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    return ManufacturingService(session, actor).list(m.ProductBatch, product_id=product_id, q=q, limit=limit, offset=offset)


@router.post("/product-batches", status_code=201)
def create_batch(data: s.BatchInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).create_batch(data.model_dump())


@router.get("/product-batches/{id}")
def batch(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    return view(ManufacturingService(session, actor).get(m.ProductBatch, id))


@router.patch("/product-batches/{id}")
def patch_batch(id: str, data: dict = Body(...), session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    revision = data.pop("revision", None)
    service = ManufacturingService(session, actor)
    original = service.get(m.ProductBatch, id)
    values = _validate(s.BatchInput, {**{k: v for k, v in view(original).items() if k in s.BatchInput.model_fields}, **data})
    if values["product_id"] != original.product_id:
        raise DomainError(422, "batch_product_immutable", "批次归属产品不能修改")
    return service.change(m.ProductBatch, id, values, revision)


@router.delete("/product-batches/{id}")
def delete_batch(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m11.delete"))):
    return ManufacturingService(session, actor).archive(m.ProductBatch, id, revision)


@router.get("/inspections")
def inspections(q: str = "", target_type: str | None = None, limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    return ManufacturingService(session, actor).list(m.Inspection, q=q, target_type=target_type, limit=limit, offset=offset)


@router.post("/inspections", status_code=201)
def create_inspection(data: s.InspectionInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).create_inspection(data.model_dump())


@router.get("/inspections/{id}")
def inspection(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    service = ManufacturingService(session, actor)
    row = service.get(m.Inspection, id)
    return {**service.inspection_views([row])[0], "reviews": service.inspection_results(id)["reviews"]}


@router.delete("/inspections/{id}")
def delete_inspection(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m11.delete"))):
    return ManufacturingService(session, actor).archive(m.Inspection, id, revision)


@router.patch("/inspections/{id}")
def patch_inspection(id: str, data: s.InspectionPatch, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).edit_inspection(id, data.model_dump())


@router.get("/inspections/{id}/samples")
def samples(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    service = ManufacturingService(session, actor)
    service.get(m.Inspection, id)
    return service.list(m.InspectionSample, inspection_id=id, limit=200)


@router.post("/inspections/{id}/samples", status_code=201)
def create_sample(id: str, data: s.SampleInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).add_sample(id, data.model_dump())


@router.get("/inspections/{id}/samples/{sample_id}")
def sample(id: str, sample_id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    service = ManufacturingService(session, actor)
    service.get(m.Inspection, id)
    row = service.get(m.InspectionSample, sample_id)
    if row.inspection_id != id:
        raise DomainError(404, "sample_not_found", "当前任务没有该样本")
    return view(row)


@router.patch("/inspections/{id}/samples/{sample_id}")
def patch_sample(id: str, sample_id: str, data: s.SamplePatch, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).edit_sample(id, sample_id, data.model_dump())


@router.delete("/inspections/{id}/samples/{sample_id}")
def delete_sample(id: str, sample_id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m11.delete"))):
    return ManufacturingService(session, actor).edit_sample(id, sample_id, {"revision": revision}, archive=True)


@router.get("/inspections/{id}/results")
def results(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    return ManufacturingService(session, actor).inspection_results(id)


@router.post("/inspections/{id}/results", status_code=201)
def create_results(id: str, data: s.ResultInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).add_evidence(id, data.model_dump())


@router.post("/inspections/{id}/imports")
def import_results(id: str, data: s.InspectionImport, session=Depends(get_session), actor=Depends(require_permission("m11.write")), settings=Depends(get_settings)):
    actor.require("m06.read")
    return ManufacturingService(session, actor).import_inspection(id, data, settings)


@router.post("/inspections/{id}/runs")
def run(id: str, data: s.RevisionInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).run_inspection(id, data.revision)


@router.get("/inspections/{id}/runs")
def inspection_runs(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    service = ManufacturingService(session, actor)
    service.get(m.Inspection, id)
    runs = session.scalars(select(m.InspectionRun).where(m.InspectionRun.inspection_id == id, m.InspectionRun.workspace_id == actor.workspace_id).order_by(m.InspectionRun.created_at.desc())).all()
    from ..data.service import DataService
    for run in runs:
        for sample in run.result.get("samples", []):
            for metric in sample.get("metrics", []):
                if metric.get("provenance", {}).get("query_id"):
                    DataService(session, actor).get_query(metric["provenance"]["query_id"])
    return {"items": [view(run) for run in runs], "total": len(runs)}


@router.post("/inspections/{id}/reviews")
def review(id: str, data: s.ReviewInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).review(id, data.model_dump())


@router.post("/inspections/{id}/deviations")
def deviation(id: str, data: s.ReviewInput, session=Depends(get_session), actor=Depends(require_permission("m11.write"))):
    return ManufacturingService(session, actor).review(id, data.model_dump(), kind="deviation")


@router.post("/inspections/{id}/exports")
def export_inspection(id: str, session=Depends(get_session), actor=Depends(require_permission("m11.read"))):
    service = ManufacturingService(session, actor)
    result = service.inspection_results(id)
    rows = [{"sample_id": sample["sample_id"], "sample": sample["external_ref"], **metric} for sample in result["result"].get("samples", []) for metric in sample["metrics"]]
    service.audit("export", "inspection", id)
    return Response(csv_content(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="inspection-{id}.csv"'})


@router.get("/data-generators")
def generators(actor=Depends(require_permission("m10.read"))):
    return {"items": [{"id": "food-equipment-v1", "name": "食品设备合成数据", "status": "available", "source_kind": "synthetic", "devices": 8, "fields": 76, "max_steps": 120, "default_running": False}], "total": 1}


@router.post("/data-generators/{id}/activations")
def activate_generator(id: str, data: s.SimulationInput, session=Depends(get_session), actor=Depends(require_permission("m10.admin")), settings=Depends(get_settings)):
    from .simulation import GENERATOR_ID, SimulationService
    if id != GENERATOR_ID:
        raise DomainError(404, "generator_not_found", "生成器不存在")
    actor.require("m06.write")
    return SimulationService(session, actor, settings).create(data)


@router.get("/data-generators/{id}/status")
def generator_status(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    from .simulation import GENERATOR_ID, SimulationService
    if id != GENERATOR_ID:
        raise DomainError(404, "generator_not_found", "生成器不存在")
    return SimulationService(session, actor, settings).latest()


@router.get("/data-generator-activations/{id}")
def generator_activation(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    from .simulation import SimulationService
    return SimulationService(session, actor, settings).get(id)


@router.post("/data-generator-activations/{id}/advances")
def advance_generator(id: str, data: s.SimulationAdvance, session=Depends(get_session), actor=Depends(require_permission("m10.admin")), settings=Depends(get_settings)):
    from .simulation import SimulationService
    actor.require("m06.write")
    return SimulationService(session, actor, settings).advance(id, data)


@router.post("/data-generator-activations/{id}/cancellations")
def cancel_generator(id: str, data: s.RevisionInput, session=Depends(get_session), actor=Depends(require_permission("m10.admin")), settings=Depends(get_settings)):
    from .simulation import SimulationService
    return SimulationService(session, actor, settings).cancel(id, data.revision)
