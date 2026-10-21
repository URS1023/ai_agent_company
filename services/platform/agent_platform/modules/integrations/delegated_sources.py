"""冻结工具实际返回的来源，并在历史读取时重核；不重跑企业数据库查询。"""

import re

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.task_evidence import query_references
from ..data.models import QueryAccessSnapshot
from ..data.service import DataService
from ..data.sql_safety import SqlFieldValidationError, ValidationError
from ..manufacturing import models as manufacturing
from ..manufacturing.service import ManufacturingService
from ..manufacturing.workflow import authorize_run
from ..reports.inspection_source import InspectionSource
from ..reports.service import ReportService

RESOURCES = {
    "device": ("m10.read", manufacturing.Device),
    "maintenance_analysis": ("m10.read", manufacturing.MaintenanceAnalysis),
    "incident": ("m10.read", manufacturing.Incident),
    "product": ("m11.read", manufacturing.Product),
    "product_batch": ("m11.read", manufacturing.ProductBatch),
    "inspection": ("m11.read", manufacturing.Inspection),
    "inspection_run": ("m11.read", manufacturing.InspectionRun),
    "report": ("m07.read", None),
    'saved_query': ('m06.read', None),
    'knowledge_space': ('m08.read', None),
    'knowledge_search': ('m08.read', None),
}


def _invalid():
    return DomainError(403, "delegation_source_denied", "当前账号无法访问该对话的业务来源")


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)


def _resource(session, actor, resource):
    if (not isinstance(resource, dict) or resource.get("kind") not in RESOURCES
            or not _id(resource.get("id"))):
        raise _invalid()
    kind = resource["kind"]
    allowed = ({"kind", "id", "inspection_id"} if kind == "inspection_run" else
               {'kind', 'id', 'evidence'} if kind == 'knowledge_search' else {"kind", "id"})
    if set(resource) != allowed:
        raise _invalid()
    permission, model = RESOURCES[kind]
    actor.require(permission)
    if kind == 'saved_query':
        return DataService(session, actor).get_query(resource['id'])
    if kind in {'knowledge_space', 'knowledge_search'}:
        from ..knowledge.service import KnowledgeService
        from ..knowledge.evidence import authorize_evidence
        space = KnowledgeService(session, actor).space(resource['id'])
        if kind == 'knowledge_search':
            evidence = resource['evidence']
            if (not isinstance(evidence, dict) or type(evidence.get('schema_version')) is not int
                    or evidence.get('space_ids') != [resource['id']]):
                raise _invalid()
            authorize_evidence(session, actor, evidence)
        return space
    if kind == "report":
        return ReportService(session, actor).get(resource["id"])
    service = ManufacturingService(session, actor)
    if kind == "inspection_run":
        if not _id(resource.get("inspection_id")):
            raise _invalid()
        inspection = service.get(manufacturing.Inspection, resource["inspection_id"])
        return authorize_run(session, actor, inspection, resource["id"])
    return service.get(model, resource["id"])


def _query_scope(session, actor, query_id):
    DataService(session, actor).get_query(query_id)
    snapshot = session.scalar(select(QueryAccessSnapshot).where(QueryAccessSnapshot.query_id == query_id,
        QueryAccessSnapshot.workspace_id == actor.workspace_id))
    return snapshot.scope_hash if snapshot is not None else None


def capture_source(session, actor, kind, row_id, payload, *, inspection_id=None):
    """只记录已输出记录的精确依赖；不带正文、JWT或整个工作区授权印记。"""
    resource = {"kind": kind, "id": row_id}
    if kind == "inspection_run":
        resource["inspection_id"] = inspection_id
    _resource(session, actor, resource)
    ids = sorted(set(query_references(payload)))
    if len(ids) > 200 or any(not _id(value) for value in ids):
        raise DomainError(422, "delegation_source_budget_exceeded", "本条结果引用的数据来源过多")
    queries = [{"id": value, "scope_hash": _query_scope(session, actor, value)} for value in ids]
    files = []
    if kind == "report":
        for file_id in sorted(ReportService._image_file_ids(payload.get("layout", {}))):
            file = FileService(session, actor, None).get(file_id)
            files.append({"id": file_id, "revision": file["revision"], "sha256": file["sha256"]})
    return {"version": 1, "workspace_id": actor.workspace_id, "resource": resource, "queries": queries, "files": files}


def authorize_delegated_source(session, actor, source):
    """当前资源与冻结的旧查询必须同时有权；改换最新数据源不能洗掉旧聊天的依赖。"""
    try:
        if (not isinstance(source, dict) or set(source) != {"version", "workspace_id", "resource", "queries", "files"}
                or type(source["version"]) is not int or source["version"] != 1
                or source["workspace_id"] != actor.workspace_id
                or not isinstance(source["queries"], list) or len(source["queries"]) > 200
                or not isinstance(source["files"], list) or len(source["files"]) > 200):
            raise _invalid()
        _resource(session, actor, source["resource"])
        if source['resource']['kind'] == 'saved_query' and (
                len(source['queries']) != 1 or not isinstance(source['queries'][0], dict)
                or source['queries'][0].get('id') != source['resource']['id']):
            raise _invalid()
        if source['resource']['kind'] in {'knowledge_space', 'knowledge_search'} and (source['queries'] or source['files']):
            # 知识原件走m08文档关联授权；不得混入普通m17文件凭据或替换为另份查询。
            raise _invalid()
        for query in source["queries"]:
            if (not isinstance(query, dict) or set(query) != {"id", "scope_hash"} or not _id(query["id"])
                    or (query["scope_hash"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(query["scope_hash"])))):
                raise _invalid()
            if _query_scope(session, actor, query["id"]) != query["scope_hash"]:
                raise _invalid()
        for source_file in source["files"]:
            if (not isinstance(source_file, dict) or set(source_file) != {"id", "revision", "sha256"}
                    or not _id(source_file["id"]) or type(source_file["revision"]) is not int
                    or source_file["revision"] < 1 or not isinstance(source_file["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", source_file["sha256"])):
                raise _invalid()
            current = FileService(session, actor, None).get(source_file["id"])
            if current["revision"] != source_file["revision"] or current["sha256"] != source_file["sha256"]:
                raise _invalid()
    except ValidationError as error:
        if isinstance(error, SqlFieldValidationError) or error.fields.get("table"):
            raise _invalid() from None
        raise


def report_run_source(session, actor, payload):
    """正式报告的旧检验运行单独留证，报告后来换布局也不能移除原有检验权限依赖。"""
    snapshot = payload.get("layout", {}).get("inspection_source")
    if not snapshot:
        return []
    run = InspectionSource(session, actor).authorize(snapshot)
    return [capture_source(session, actor, "inspection_run", run.id, run.result,
                           inspection_id=snapshot["inspection_id"])]
