"""制造处置共用的人员授权、日期和原始运行核验，不执行外部查询。"""
from datetime import datetime, timezone

from sqlalchemy import or_, select

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Role, User, Workspace
from agent_platform.core.tasks import TaskService

from ..data.service import DataService
from ..data.sql_safety import SqlFieldValidationError, ValidationError
from ..identity.models import ServiceIdentity
from . import models as m


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def input_time(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise DomainError(422, "invalid_time", "时间须完整且包含时区") from exc


def workspace_user(session, actor, user_id, permission):
    """责任人须为当前工作区有效人员并有相应执行权限，不能分派给停用或无权账号。"""
    workspace, user = session.get(Workspace, actor.workspace_id), session.get(User, user_id)
    if workspace is None or user is None or user.disabled or user.organization_id != workspace.organization_id:
        raise DomainError(422, "assignee_unavailable", "请选择当前工作区可执行此任务的人员")
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.user_id == user_id).limit(1)):
        raise DomainError(422, "assignee_unavailable", "请选择当前工作区可执行此任务的人员")
    if user.is_admin:
        return user
    member = session.scalar(select(Membership).where(Membership.workspace_id == actor.workspace_id, Membership.user_id == user_id, Membership.disabled.is_(False)))
    roles = list(session.scalars(select(Role).where(Role.workspace_id == actor.workspace_id, Role.id.in_(member.role_ids)))) if member else []
    permissions = frozenset(item for role in roles for item in role.permissions)
    if member is None or not Actor(user.id, actor.workspace_id, permissions).can(permission):
        raise DomainError(422, "assignee_unavailable", "请选择当前工作区可执行此任务的人员")
    return user


def assignees(session, actor, permission):
    workspace = session.get(Workspace, actor.workspace_id)
    if workspace is None:
        return {"items": [], "total": 0}
    member_ids = select(Membership.user_id).where(Membership.workspace_id == actor.workspace_id, Membership.disabled.is_(False))
    service_users = select(ServiceIdentity.user_id)
    users = session.scalars(select(User).where(User.organization_id == workspace.organization_id, User.disabled.is_(False), User.id.not_in(service_users), or_(User.is_admin.is_(True), User.id.in_(member_ids))).order_by(User.display_name, User.id)).all()
    items = []
    for user in users:
        try:
            workspace_user(session, actor, user.id, permission)
        except DomainError:
            continue
        items.append({"id": user.id, "name": user.display_name or user.username})
    return {"items": items, "total": len(items)}


def people_names(session, actor, ids):
    workspace = session.get(Workspace, actor.workspace_id)
    if workspace is None:
        return {}
    users = session.scalars(select(User).where(User.id.in_({value for value in ids if value}), User.organization_id == workspace.organization_id)).all()
    return {user.id: user.display_name or user.username for user in users}


def authorize_run(session, actor, inspection, run_id):
    """历史处置始终检查选中运行的查询证据，后续任务换源不能替旧数据授权。"""
    actor.require("m11.read")
    run = session.scalar(select(m.InspectionRun).where(m.InspectionRun.id == run_id, m.InspectionRun.workspace_id == actor.workspace_id, m.InspectionRun.inspection_id == inspection.id))
    if run is None:
        raise DomainError(404, "inspection_run_not_found", "检验运行不存在或不属于该任务")
    if run.result.get("run_id") != run.id or run.result.get("standard_revision") != inspection.template_revision:
        raise DomainError(422, "inspection_run_evidence_invalid", "检验运行或标准版本证据不完整")
    task = TaskService(session, actor).get(run.task_id)
    if task["inputs"].get("inspection_id") != inspection.id or task["inputs"].get("template_id") != inspection.template_id or task["inputs"].get("template_revision") != inspection.template_revision or inspection.standard_snapshot.get("revision") != inspection.template_revision:
        raise DomainError(422, "inspection_run_evidence_invalid", "运行任务与固定标准版本不一致")
    queries = {metric.get("provenance", {}).get("query_id") for sample in run.result.get("samples", []) for metric in sample.get("metrics", [])}
    for query_id in queries - {None, ""}:
        try:
            DataService(session, actor).get_query(query_id)
        except ValidationError as exc:
            if isinstance(exc, SqlFieldValidationError) or exc.fields.get("table"):
                raise DomainError(403, "inspection_evidence_access_denied", "当前账号无法访问原始检验证据") from exc
            raise
    return run
