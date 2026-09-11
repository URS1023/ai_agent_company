from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from sqlalchemy.orm import Session

from services.enterprise_schedule_inspection import ScheduleInspectionError, inspect_schedule_owner


def fixture():
    session = create_autospec(Session, instance=True)
    workflow = SimpleNamespace(
        id="wf",
        app_id="app",
        tenant_id="workspace",
        version="published",
        unique_hash="a" * 64,
        graph_dict={"nodes": [{"id": "start", "data": {"type": "start"}}]},
    )
    session.scalar.side_effect = [workflow, None]
    return session, workflow


def inspect(session):
    return inspect_schedule_owner(
        session, workspace_id="workspace", app_id="app", workflow_id="wf", expected_hash="a" * 64
    )


def test_exact_publication_and_application_plan_are_both_checked_without_writes() -> None:
    session, _ = fixture()
    result = inspect(session)
    assert not result.native_owner_present
    assert (result.workspace_id, result.app_id, result.workflow_id, result.graph_hash) == (
        "workspace",
        "app",
        "wf",
        "a" * 64,
    )
    workflow_query, plan_query = [call.args[0] for call in session.scalar.call_args_list]
    assert set(workflow_query.compile().params.values()) == {"workspace", "app", "wf"}
    assert "wf" not in plan_query.compile().params.values()
    assert {"workspace", "app"} <= set(plan_query.compile().params.values())
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.flush.assert_not_called()


def test_any_app_plan_including_paused_or_other_revision_blocks_enterprise_owner() -> None:
    session, workflow = fixture()
    session.scalar.side_effect = [workflow, "plan-from-another-publication"]
    assert inspect(session).native_owner_present


def test_schedule_node_blocks_even_without_an_installed_plan() -> None:
    session, workflow = fixture()
    workflow.graph_dict["nodes"].append({"id": "timer", "data": {"type": "trigger-schedule"}})
    assert inspect(session).native_owner_present


@pytest.mark.parametrize(
    ("field", "value"),
    [("tenant_id", "other"), ("app_id", "other"), ("id", "other"), ("version", "draft"), ("unique_hash", "b" * 64)],
)
def test_stale_or_foreign_publication_is_not_reported_as_unowned(field, value) -> None:
    session, workflow = fixture()
    setattr(workflow, field, value)
    with pytest.raises(ScheduleInspectionError):
        inspect(session)
    assert session.scalar.call_count == 1


@pytest.mark.parametrize("graph", [{}, {"nodes": []}, {"nodes": [None]}, {"nodes": [{"id": "x", "data": {}}]}])
def test_malformed_graph_fails_closed(graph) -> None:
    session, workflow = fixture()
    workflow.graph_dict = graph
    with pytest.raises(ScheduleInspectionError):
        inspect(session)


def test_missing_publication_is_not_reported_as_unowned() -> None:
    session, _ = fixture()
    session.scalar.side_effect = [None]
    with pytest.raises(ScheduleInspectionError):
        inspect(session)
