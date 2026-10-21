"""用人类当前权限派发十五个只读业务工具，复用原领域服务而非另写数据库接口。"""

import json
import re

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from ..manufacturing import models as m
from ..manufacturing.service import ManufacturingService, view
from ..manufacturing.workflow import authorize_run
from ..reports.service import ReportService, view as report_view
from .delegated_contract import FILTERS, OPERATIONS, PAGED_OPERATIONS
from .delegated_sources import authorize_delegated_source, capture_source, report_run_source


def parameters(operation, values):
    """模型只可填写白名单业务筛选；拒绝重复参数、任意身份和数据库执行参数。"""
    if operation not in OPERATIONS:
        raise DomainError(404, "delegated_tool_not_found", "业务工具不存在")
    pairs = list(values.multi_items()) if hasattr(values, "multi_items") else list(values.items())
    allowed = set(FILTERS.get(operation, {"id"}))
    paged = operation in PAGED_OPERATIONS
    if paged:
        allowed.update({"page", "limit"})
    if len({key for key, _ in pairs}) != len(pairs) or any(key not in allowed for key, _ in pairs):
        raise DomainError(422, "delegated_parameters_invalid", "查询参数不正确")
    result = dict(pairs)
    for name, value in result.items():
        if name in {"page", "limit", 'top_k'}:
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", value):
                raise DomainError(422, "delegated_parameters_invalid", "分页参数不正确")
            number = int(value)
            if number > {'limit': 20, 'page': 1000, 'top_k': 5}[name]:
                raise DomainError(422, "delegated_parameters_invalid", "分页参数超出范围")
            result[name] = number
        elif not isinstance(value, str) or len(value) > (1000 if name == 'query' else 160 if name == "q" else 80) or any(c in value for c in "\r\n\x00"):
            raise DomainError(422, "delegated_parameters_invalid", "查询字段不正确")
        elif name not in {'q', 'query'} and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise DomainError(422, "delegated_parameters_invalid", "查询字段不正确")
    if any(name in allowed and not result.get(name, '').strip() for name in ('id', 'space_id', 'query')):
        raise DomainError(422, "delegated_resource_required", "请选择业务记录")
    if paged:
        result.setdefault("page", 1)
        result.setdefault("limit", 20)
    return result


def _run_list(session, actor, service, row_id, limit, offset):
    inspection = service.get(m.Inspection, row_id)
    rows = list(session.scalars(select(m.InspectionRun).where(m.InspectionRun.workspace_id == actor.workspace_id,
        m.InspectionRun.inspection_id == row_id).order_by(m.InspectionRun.created_at.desc(), m.InspectionRun.id).limit(1001)))
    if len(rows) > 1000:
        raise DomainError(422, "delegated_history_budget_exceeded", "历史运行过多，请在检验页面缩小范围")
    visible = []
    for row in rows:
        try:
            authorize_run(session, actor, inspection, row.id)
        except DomainError as error:
            if error.status in {403, 404}:
                continue
            raise
        visible.append(view(row))
    return visible[offset:offset + limit]


def execute_tool(session, actor, operation, values, *, context=None, settings=None):
    """仅读取平台保存实体；调用者须先完成委托认证，结果和来源必须同事务交付。"""
    args = parameters(operation, values)
    actor.require(OPERATIONS[operation][1])
    from .delegated_data import OPERATIONS as data_operations, execute_data
    from .delegated_knowledge import OPERATIONS as knowledge_operations, execute_knowledge
    if operation in data_operations or operation in knowledge_operations:
        payload, sources = (execute_data(session, actor, operation, args) if operation in data_operations else
            execute_knowledge(session, actor, operation, args, context=context, settings=settings))
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > 512 * 1024:
            raise DomainError(422, 'delegated_result_budget_exceeded', '结果较大，请缩小查询范围')
        return payload, sources
    service = ManufacturingService(session, actor)
    listing = operation.startswith("list_")
    limit, offset = args.get("limit", 20), (args.get("page", 1) - 1) * args.get("limit", 20)
    filters = {key: value for key, value in args.items() if key not in {"id", "page", "limit"}}
    resource_id = args.get("id")
    kinds = {"devices": ("device", m.Device), "maintenance_analyses": ("maintenance_analysis", m.MaintenanceAnalysis),
             "incidents": ("incident", m.Incident), "products": ("product", m.Product),
             "product_batches": ("product_batch", m.ProductBatch), "inspections": ("inspection", m.Inspection)}
    suffix = operation.removeprefix("list_").removeprefix("get_")
    singular = {"device": "devices", "maintenance_analysis": "maintenance_analyses", "incident": "incidents",
                "product_batch": "product_batches", "inspection": "inspections"}
    suffix = singular.get(suffix, suffix)
    if operation == "list_reports":
        kind, items = "report", ReportService(session, actor).list(limit=limit, offset=offset, **filters)["items"]
    elif operation == "get_report":
        kind, items = "report", [report_view(ReportService(session, actor).get(resource_id))]
    elif operation == "list_inspection_runs":
        kind, items = "inspection_run", _run_list(session, actor, service, resource_id, limit, offset)
    elif operation == "get_inspection_result":
        kind, items = "inspection", [service.inspection_results(resource_id)]
    elif suffix in kinds:
        kind, model = kinds[suffix]
        if listing:
            items = service.list(model, limit=limit, offset=offset, **filters)["items"]
        else:
            row = service.get(model, resource_id)
            item = (service.inspection_views([row])[0] if kind == "inspection" else
                    service.maintenance_views([row])[0] if kind in {"incident", "maintenance_analysis"} else view(row))
            if kind == "inspection":
                item["reviews"] = service.inspection_results(resource_id)["reviews"]
            if kind == "incident":
                # 已先核同工作区的父预警，事件按外键归属读取，不接受客户端另报事件ID。
                events = session.scalars(select(m.IncidentEvent).where(m.IncidentEvent.incident_id == row.id)
                    .order_by(m.IncidentEvent.created_at)).all()
                item["events"] = [view(event) for event in events]
            items = [item]
    else:
        raise DomainError(404, "delegated_tool_not_found", "业务工具不存在")
    payload = {"items": items, "returned_count": len(items)} if listing else items[0]
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > 512 * 1024:
        raise DomainError(422, "delegated_result_budget_exceeded", "结果较大，请缩小查询范围")
    sources = []
    for item in items:
        sources.append(capture_source(session, actor, kind, item["id"], item,
            inspection_id=resource_id if kind == "inspection_run" else None))
        if kind == "report":
            sources.extend(report_run_source(session, actor, item))
    return payload, sources
