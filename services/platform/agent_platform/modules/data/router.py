"""数据领域API；受控查询、导入、质量检查均返回真实持久化结果。"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings

from ..manufacturing.service import csv_content
from . import models as m, schemas as s
from .service import DataService, view

router = APIRouter(prefix="/api/v1", tags=["数据接入与分析"])


@router.get("/datasources")
def sources(q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m05.read"))):
    service = DataService(session, actor)
    result = service.list(m.DataSource, q=q, limit=limit, offset=offset)
    for item in result["items"]:
        item["schema_snapshot"] = service.metadata(item["id"])["tables"]
    return result


@router.post("/datasources", status_code=201)
def create_source(data: s.SourceInput, session=Depends(get_session), actor=Depends(require_permission("m05.write")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).create_source(data.model_dump())


@router.get("/datasources/{id}")
def source(id: str, session=Depends(get_session), actor=Depends(require_permission("m05.read"))):
    service = DataService(session, actor)
    data = view(service.get_source(id))
    data["schema_snapshot"] = service.metadata(id)["tables"]
    return data


@router.patch("/datasources/{id}")
def patch_source(id: str, data: s.SourcePatch, session=Depends(get_session), actor=Depends(require_permission("m05.write"))):
    return DataService(session, actor).patch_source(id, data.model_dump(exclude_unset=True))


@router.delete("/datasources/{id}")
def delete_source(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m05.delete"))):
    service = DataService(session, actor)
    # 软删除不移除原始导入文件，审计和历史查询仍保留恢复依据。
    return service.update(m.DataSource, id, {"archived": True}, revision)


@router.post("/datasources/{id}/connection-checks")
def check_source(id: str, session=Depends(get_session), actor=Depends(require_permission("m05.write")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).check_connection(id)


@router.get("/datasources/{id}/metadata")
def metadata(id: str, session=Depends(get_session), actor=Depends(require_permission("m05.read"))):
    return DataService(session, actor).metadata(id)


@router.post("/datasources/{id}/metadata-syncs")
def sync_metadata(id: str, session=Depends(get_session), actor=Depends(require_permission("m05.write")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).sync_metadata(id)


@router.post("/datasources/{id}/target-approvals")
def approve_target(id: str, data: s.TargetApproval, session=Depends(get_session), actor=Depends(require_permission("m05.admin")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).approve_target(id, data.model_dump())


@router.post("/datasources/{id}/recoveries")
def recover_source(id: str, data: s.SourceRecovery, session=Depends(get_session), actor=Depends(require_permission("m05.admin")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).recover_source(id, data.model_dump())


@router.get("/datasets")
def datasets(q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    return DataService(session, actor).list(m.Dataset, q=q, limit=limit, offset=offset)


@router.post("/datasets", status_code=201)
def import_dataset(data: s.DatasetInput, session=Depends(get_session), actor=Depends(require_permission("m06.write")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).import_csv(data.model_dump())


@router.get("/datasets/{id}")
def dataset(id: str, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    return view(DataService(session, actor).get(m.Dataset, id))


@router.patch("/datasets/{id}")
def patch_dataset(id: str, data: s.DatasetPatch, session=Depends(get_session), actor=Depends(require_permission("m06.write"))):
    return DataService(session, actor).update(m.Dataset, id, data.model_dump(), data.revision)


@router.delete("/datasets/{id}")
def delete_dataset(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m06.delete"))):
    return DataService(session, actor).update(m.Dataset, id, {"archived": True}, revision)


@router.post("/queries", status_code=201)
def execute_query(data: s.QueryInput, session=Depends(get_session), actor=Depends(require_permission("m06.write")), settings=Depends(get_settings)):
    return DataService(session, actor, settings).execute_query(data.model_dump())


@router.post("/queries/natural-language")
def natural_query(data: s.NaturalQueryInput, request: Request, session=Depends(get_session), actor=Depends(require_permission("m06.write")), settings=Depends(get_settings)):
    from ..integrations.service import IntegrationService
    completion = IntegrationService(session, actor, request).model_completion
    return DataService(session, actor, settings).natural_query(data.model_dump(), completion)


@router.get("/queries")
def queries(q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    service = DataService(session, actor)
    result = service.list(m.QueryRecord, q=q, limit=limit, offset=offset)
    visible = []
    for item in result["items"]:
        try:
            service.get_query(item["id"])
            visible.append(item)
        except DomainError as exc:
            if exc.status not in (403, 404, 422):
                raise
    return {"items": visible, "total": len(visible)}


@router.get("/queries/{id}")
def query(id: str, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    data = view(DataService(session, actor).get_query(id))
    return {**data, **data["result"]}


@router.post("/queries/{id}/exports")
def export_query(id: str, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    row = DataService(session, actor).get_query(id)
    RecordStore(session, actor).audit("export", "query", row.id)
    return Response(csv_content(row.result["rows"]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="query-{id}.csv"'})


@router.post("/data-quality-checks")
def quality(data: s.QualityInput, session=Depends(get_session), actor=Depends(require_permission("m06.write"))):
    return DataService(session, actor).quality_check(data.model_dump())


@router.post("/data-processing-jobs", status_code=201)
def process(data: s.ProcessingInput, session=Depends(get_session), actor=Depends(require_permission("m06.write"))):
    return DataService(session, actor).process(data.model_dump())


@router.get("/data-processing-jobs/{id}")
def processing_job(id: str, session=Depends(get_session), actor=Depends(require_permission("m06.read"))):
    return view(DataService(session, actor).get_processing(id))


def _metadata_routes(path, kind, permission):
    """元数据目录复用仓储，查询执行和制造实体仍使用各自领域服务。"""
    def listing(q: str = "", limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission(permission + ".read"))):
        return RecordStore(session, actor).list(kind, q=q, limit=limit, offset=offset)
    def creating(data: s.MetadataResource, session=Depends(get_session), actor=Depends(require_permission(permission + ".write"))):
        if data.datasource_id:
            DataService(session, actor).get_source(data.datasource_id)
        return RecordStore(session, actor).create(kind, data.model_dump(exclude={"revision"}))
    def getting(id: str, session=Depends(get_session), actor=Depends(require_permission(permission + ".read"))):
        return RecordStore(session, actor).get(kind, id)
    def updating(id: str, data: s.MetadataResource, session=Depends(get_session), actor=Depends(require_permission(permission + ".write"))):
        if data.revision is None:
            raise DomainError(422, "revision_required", "请提交当前版本号")
        if data.datasource_id:
            DataService(session, actor).get_source(data.datasource_id)
        return RecordStore(session, actor).update(kind, id, data.model_dump(exclude={"revision"}), data.revision)
    def deleting(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission(permission + ".delete"))):
        return RecordStore(session, actor).delete(kind, id, revision)
    for method, suffix, handler in [("GET", "", listing), ("POST", "", creating), ("GET", "/{id}", getting), ("PATCH", "/{id}", updating), ("DELETE", "/{id}", deleting)]:
        router.add_api_route(path + suffix, handler, methods=[method], name=kind + "_" + method.lower())


_metadata_routes("/business-metrics", "business_metric", "m05")
_metadata_routes("/data-relations", "data_relation", "m05")
_metadata_routes("/query-templates", "query_template", "m06")
