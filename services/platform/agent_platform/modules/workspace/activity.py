"""把有用、当前仍获授权的审计事件投影为工作台动态，不向页面输出内部事件作为文案。"""

import re

from sqlalchemy import select

from ...core.models import AuditEvent, ResourceGrant, ResourceRecord
from ..identity.access import RESOURCE_MODULES
from ..operations.support_models import OperationSupportResource

MAX_CANDIDATES = 64
MAX_ACTIVITIES = 8
SUPPORT_NAMES = {"dictionary": "字典", "parameter": "系统参数", "announcement": "公告", "form": "表单"}
RESOURCE_NAMES = {
    "application": "应用", "automation": "自动化", "agent_app_binding": "智能体",
    "knowledge_space": "知识库", "graph": "知识图谱", "ontology": "本体",
    "business_metric": "业务指标", "query_template": "查询模板", "artifact": "成果",
    "skill-binding": "技能", "tool_connection": "工具", "report_prompt": "报告提示模板",
}
ACTIONS = {"create": "新增", "update": "更新", "delete": "删除", "publish": "发布", "restore": "恢复", "archive": "归档", "export": "导出"}
IDENTIFIER = re.compile(r"^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$", re.I)


def event_kind(event):
    """仅识别既有明确契约；表单校验、凭据等内部活动不会占用业务动态。"""
    if event.action.startswith("operation_support."):
        action = event.action.removeprefix("operation_support.")
        kind = event.resource_type.removeprefix("operation_")
        if action in ACTIONS and kind in SUPPORT_NAMES and event.resource_type == "operation_" + kind:
            return "support", kind, action
    elif event.action in ACTIONS and event.resource_type in RESOURCE_NAMES:
        return "resource", event.resource_type, event.action
    return None


def display_name(event, row):
    """旧审计可以用当前可见对象补名；不使用ID、编码或任意details.message凑出动态。"""
    details = event.details if isinstance(event.details, dict) else {}
    candidate = details.get("name") or row.name
    if not isinstance(candidate, str):
        return ""
    name = " ".join(candidate.split()).strip()
    if not name or IDENTIFIER.fullmatch(name):
        return ""
    return name if len(name) <= 160 else name[:159] + "…"


def recent_activities(session, actor):
    """最多扫64条、返8条；对象和授权批量读取，事件数量不会产生逐条资源查询。"""
    rules = [AuditEvent.workspace_id == actor.workspace_id]
    if not actor.is_admin:
        rules.append(AuditEvent.user_id == actor.user_id)
    events = list(session.scalars(select(AuditEvent).where(*rules)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(MAX_CANDIDATES)))
    candidates = [(event, parsed) for event in events if (parsed := event_kind(event))]
    support_ids = {event.resource_id for event, (source, _, _) in candidates if source == "support"}
    resource_ids = {event.resource_id for event, (source, _, _) in candidates if source == "resource"}
    supports = {}
    if support_ids and actor.can("m16.read"):
        supports = {row.id: row for row in session.scalars(select(OperationSupportResource).where(
            OperationSupportResource.workspace_id == actor.workspace_id,
            OperationSupportResource.id.in_(support_ids), OperationSupportResource.deleted.is_(False)))}
    resources = {}
    grants = set()
    if resource_ids:
        resources = {row.id: row for row in session.scalars(select(ResourceRecord).where(
            ResourceRecord.workspace_id == actor.workspace_id, ResourceRecord.id.in_(resource_ids),
            ResourceRecord.deleted.is_(False)))}
        if not actor.is_admin:
            grants = {(row.resource_type, row.resource_id) for row in session.scalars(select(ResourceGrant).where(
                ResourceGrant.workspace_id == actor.workspace_id, ResourceGrant.user_id == actor.user_id,
                ResourceGrant.resource_id.in_(resource_ids))) if "read" in row.actions or "*" in row.actions}
    result = []
    seen = set()
    for event, (source, kind, action) in candidates:
        if source == "support":
            row = supports.get(event.resource_id)
            if row is None or row.kind != kind:
                continue
            if not actor.is_admin and row.scope != "workspace" and row.owner_id != actor.user_id:
                continue
            label = SUPPORT_NAMES[kind]
        else:
            row = resources.get(event.resource_id)
            module = RESOURCE_MODULES[kind]
            if row is None or row.kind != kind or not actor.can(module + ".read"):
                continue
            if not actor.is_admin and not (row.visibility == "workspace" or row.owner_id == actor.user_id or (kind, row.id) in grants):
                continue
            label = RESOURCE_NAMES[kind]
        name = display_name(event, row)
        if not name:
            continue
        key = (event.resource_type, event.resource_id, action)
        if key in seen:
            continue
        seen.add(key)
        result.append({"id": event.id, "action": event.action, "resource_type": event.resource_type,
            "resource_id": event.resource_id, "created_at": event.created_at,
            "message": f"{ACTIONS[action]}{label}「{name}」"})
        if len(result) == MAX_ACTIVITIES:
            break
    return result
