"""报表设计、绑定、版本、模板与独立导出API。"""
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings

from . import models as m, schemas as s
from .service import ReportService, STATIC_COMPONENTS, SUPPORTED_COMPONENTS, component_catalog, view

router = APIRouter(prefix="/api/v1", tags=["报表与模板"])


@router.get("/reports")
def reports(q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return ReportService(session, actor).list(q=q, limit=limit, offset=offset)


@router.post("/reports", status_code=201)
def create_report(data: s.ReportInput, session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    return ReportService(session, actor).create(data.model_dump())


@router.get("/reports/{id}")
def report(id: str, session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return view(ReportService(session, actor).get(id))


@router.patch("/reports/{id}")
def patch_report(id: str, data: s.ReportPatch, session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    return ReportService(session, actor).patch(id, data.model_dump(exclude_unset=True))


@router.delete("/reports/{id}")
def delete_report(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m07.delete"))):
    return ReportService(session, actor).patch(id, {"revision": revision, "archived": True})


@router.get("/reports/{id}/revisions")
def revisions(id: str, session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return ReportService(session, actor).revisions(id)


@router.post("/reports/{id}/restorations")
def restore(id: str, data: s.RestoreInput, session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    return ReportService(session, actor).restore(id, data.revision, data.target_revision)


@router.post("/reports/{id}/publications")
def publish(id: str, data: s.RevisionInput, session=Depends(get_session), actor=Depends(require_permission("m07.publish")), settings=Depends(get_settings)):
    return ReportService(session, actor, settings).publish(id, data.revision)


@router.post("/reports/{id}/data-refreshes")
def refresh(id: str, data: s.RevisionInput, session=Depends(get_session), actor=Depends(require_permission("m07.write")), settings=Depends(get_settings)):
    return ReportService(session, actor, settings).refresh(id, data.revision)


@router.post("/reports/{id}/drilldowns")
def drilldown(id: str, data: s.DrilldownInput, session=Depends(get_session), actor=Depends(require_permission("m07.read")), settings=Depends(get_settings)):
    return ReportService(session, actor, settings).drilldown(id, data.widget_id, data.filters)


@router.post("/reports/{id}/exports")
def export(id: str, data: s.ExportInput, session=Depends(get_session), actor=Depends(require_permission("m07.read")), settings=Depends(get_settings)):
    content, media, suffix = ReportService(session, actor, settings).export(id, data.format, data.revision)
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="report-{id}.{suffix}"'})


@router.get("/reports/{id}/image-assets")
def image_assets(id: str, session=Depends(get_session), actor=Depends(require_permission("m07.read")), settings=Depends(get_settings)):
    return ReportService(session, actor, settings).image_assets(id)


@router.post("/report-generations", status_code=201)
def generate(data: s.GenerationInput, session=Depends(get_session), actor=Depends(require_permission("m07.write")), settings=Depends(get_settings)):
    return ReportService(session, actor, settings).generate(data.model_dump())


@router.post("/inspection-report-generations", status_code=201)
def generate_inspection_report(data: s.InspectionReportGenerationInput, session=Depends(get_session), actor=Depends(require_permission("m07.write")), settings=Depends(get_settings)):
    actor.require("m11.read")
    return ReportService(session, actor, settings).generate_inspection(data.model_dump())


@router.get("/report-templates")
def templates(q: str = "", session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return ReportService(session, actor).templates(q=q)


@router.post("/report-templates", status_code=201)
def create_template(data: s.ReportInput, session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    return ReportService(session, actor).create(data.model_dump(), model=m.ReportTemplate)


@router.get("/report-templates/{id}")
def template(id: str, session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return ReportService(session, actor).template(id)


@router.patch("/report-templates/{id}")
def patch_template(id: str, data: s.ReportPatch, session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    if id.startswith("hifi-template-"):
        raise DomainError(409, "builtin_template_read_only", "内置模板请另存为自定义模板")
    return ReportService(session, actor).patch(id, data.model_dump(exclude_unset=True), model=m.ReportTemplate)


@router.delete("/report-templates/{id}")
def delete_template(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m07.delete"))):
    return ReportService(session, actor).patch(id, {"revision": revision, "archived": True}, model=m.ReportTemplate)


@router.post("/report-templates/{id}/publications")
def publish_template(id: str, data: s.RevisionInput, session=Depends(get_session), actor=Depends(require_permission("m07.publish"))):
    return ReportService(session, actor).publish(id, data.revision, model=m.ReportTemplate)


@router.get("/report-components")
def components(actor=Depends(require_permission("m07.read"))):
    rows = [{"id": key, "name": name, "editable": True, "supports_binding": key not in STATIC_COMPONENTS} for key, name in SUPPORTED_COMPONENTS.items()]
    return {"items": rows, "total": len(rows)}


@router.get("/report-components/catalog")
def source_component_catalog(actor=Depends(require_permission("m07.read"))):
    return component_catalog()


@router.post("/report-component-instances")
def create_component_instance(data: s.ComponentInstanceInput, actor=Depends(require_permission("m07.write"))):
    from .factory import component_instance
    return component_instance(data, SUPPORTED_COMPONENTS)


@router.get("/prompt-templates")
def prompts(q: str = "", session=Depends(get_session), actor=Depends(require_permission("m07.read"))):
    return RecordStore(session, actor).list("report_prompt", q=q)


@router.post("/prompt-templates", status_code=201)
def create_prompt(data: dict = Body(...), session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    if not data.get("name") or not data.get("content") or len(str(data["content"])) > 20000:
        raise DomainError(422, "prompt_invalid", "请填写模板名称及不超过20000字的内容")
    return RecordStore(session, actor).create("report_prompt", data)


@router.patch("/prompt-templates/{id}")
def patch_prompt(id: str, data: dict = Body(...), session=Depends(get_session), actor=Depends(require_permission("m07.write"))):
    revision = data.pop("revision", None)
    if revision is None:
        raise DomainError(422, "revision_required", "请提交当前版本号")
    return RecordStore(session, actor).update("report_prompt", id, data, revision)


@router.delete("/prompt-templates/{id}")
def delete_prompt(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m07.delete"))):
    return RecordStore(session, actor).delete("report_prompt", id, revision)
