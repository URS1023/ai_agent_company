"""Read-only native scheduling ownership snapshot for enterprise integration.

The caller authenticates the native account and scopes app access before invoking
this function. A pinned historical publication without timer nodes is insufficient:
any app-level schedule plan retains native ownership, including a paused plan or
one installed from another publication. This is a snapshot, not an ownership lease
or protection against later native publication. No native behavior is modified.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.trigger.constants import TRIGGER_SCHEDULE_NODE_TYPE
from models.trigger import WorkflowSchedulePlan
from models.workflow import Workflow


class ScheduleInspectionError(ValueError):
    """The requested exact publication could not be positively inspected."""


@dataclass(frozen=True, slots=True)
class ScheduleInspection:
    workspace_id: str
    app_id: str
    workflow_id: str
    graph_hash: str
    native_owner_present: bool


def _has_timer(graph: object) -> bool:
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not graph["nodes"]:
        raise ScheduleInspectionError("native_schedule_graph_invalid")
    ids: set[str] = set()
    found = False
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            raise ScheduleInspectionError("native_schedule_graph_invalid")
        node_id, data = node.get("id"), node.get("data")
        if not isinstance(node_id, str) or not node_id or node_id in ids or not isinstance(data, dict):
            raise ScheduleInspectionError("native_schedule_graph_invalid")
        ids.add(node_id)
        node_type = data.get("type")
        if not isinstance(node_type, str) or not node_type:
            raise ScheduleInspectionError("native_schedule_graph_invalid")
        found = found or node_type == TRIGGER_SCHEDULE_NODE_TYPE
    return found


def inspect_schedule_owner(
    session: Session, *, workspace_id: str, app_id: str, workflow_id: str, expected_hash: str
) -> ScheduleInspection:
    """Read exact published graph and any app plan; never commit or mutate state.

    IDs/hash are canonicalized at the authenticated HTTP boundary. Query scope
    and returned scope are both checked here. Database failures propagate; missing
    or malformed publications never become a false no-owner answer.
    """
    workflow = session.scalar(
        select(Workflow).where(
            Workflow.tenant_id == workspace_id,
            Workflow.app_id == app_id,
            Workflow.id == workflow_id,
        )
    )
    if (
        workflow is None
        or (workflow.tenant_id, workflow.app_id, workflow.id) != (workspace_id, app_id, workflow_id)
        or not isinstance(workflow.version, str)
        or not workflow.version
        or workflow.version == Workflow.VERSION_DRAFT
        or workflow.unique_hash != expected_hash
    ):
        raise ScheduleInspectionError("native_schedule_publication_mismatch")
    try:
        has_timer = _has_timer(workflow.graph_dict)
    except (TypeError, ValueError):
        raise ScheduleInspectionError("native_schedule_graph_invalid") from None
    plan_id = session.scalar(
        select(WorkflowSchedulePlan.id)
        .where(
            WorkflowSchedulePlan.tenant_id == workspace_id,
            WorkflowSchedulePlan.app_id == app_id,
        )
        .limit(1)
    )
    return ScheduleInspection(workspace_id, app_id, workflow_id, expected_hash, has_timer or plan_id is not None)
