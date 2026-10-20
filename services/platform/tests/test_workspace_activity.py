"""工作台动态使用新临时SQLite验证当前授权、中文投影和有界批量读取。"""

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.models import AuditEvent, ResourceGrant, ResourceRecord
from agent_platform.modules.operations.support_models import OperationSupportResource
from agent_platform.modules.operations.support_service import SupportService
from agent_platform.modules.workspace.activity import recent_activities
from agent_platform.modules.workspace.router import workbench


@pytest.fixture
def context(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "activity.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, Actor("operator", "owned-workspace", frozenset({"m01.read", "m16.read", "m13.read"})), engine
        session.rollback()
    engine.dispose()


def support(session, actor, *, identifier="dictionary", name="设备状态", scope="workspace", owner=None, deleted=False, workspace=None):
    row = OperationSupportResource(id=identifier, workspace_id=workspace or actor.workspace_id,
        kind="dictionary", name=name, code="device.status." + identifier, scope=scope,
        owner_id=owner or actor.user_id, scope_owner="workspace" if scope == "workspace" else owner or actor.user_id,
        definition={"entries": []}, deleted=deleted)
    session.add(row)
    session.flush()
    return row


def audit(session, actor, identifier, *, action="operation_support.update", resource="operation_dictionary",
          name=None, user=None, workspace=None, stamp="2026-10-21T01:30:00+00:00"):
    row = AuditEvent(workspace_id=workspace or actor.workspace_id, user_id=user or actor.user_id,
        resource_id=identifier, action=action, resource_type=resource,
        details={} if name is None else {"name": name}, created_at=stamp)
    session.add(row)
    session.flush()
    return row


def test_legacy_support_event_gets_authorized_name_and_workbench_keeps_compatible_fields(context):
    session, actor, _ = context
    row = support(session, actor)
    entry = audit(session, actor, row.id)
    result = workbench(actor, session)["activities"]
    assert result == [{"id": entry.id, "action": "operation_support.update", "resource_type": "operation_dictionary",
        "resource_id": row.id, "created_at": "2026-10-21T01:30:00+00:00", "message": "更新字典「设备状态」"}]
    assert "details" not in result[0]


def test_new_support_snapshot_adds_only_human_name_and_kind_without_definition_values(context):
    session, actor, _ = context
    row = support(session, actor)
    SupportService(session, actor).snapshot(row, "update", ["name"])
    entry = session.scalar(select(AuditEvent))
    assert entry.details["name"] == "设备状态" and entry.details["kind"] == "dictionary"
    assert set(entry.details) == {"name", "kind", "code", "revision", "fields"}
    assert recent_activities(session, actor)[0]["message"] == "更新字典「设备状态」"


def test_own_audit_does_not_bypass_current_module_personal_or_workspace_scope(context):
    session, actor, _ = context
    visible = support(session, actor)
    private = support(session, actor, identifier="private", scope="personal", owner="another-person")
    foreign = support(session, actor, identifier="foreign", workspace="another-workspace")
    for row in [visible, private, foreign]:
        audit(session, actor, row.id, name="审计中有名称但不代表可见")
    result = recent_activities(session, actor)
    assert [row["resource_id"] for row in result] == [visible.id]
    revoked = replace(actor, permissions=frozenset({"m01.read"}))
    assert recent_activities(session, revoked) == []


def test_non_admin_keeps_own_event_scope_admin_stays_in_current_workspace(context):
    session, actor, _ = context
    row = support(session, actor)
    own = audit(session, actor, row.id, action="operation_support.create")
    other = audit(session, actor, row.id, user="another-person")
    audit(session, actor, row.id, workspace="another-workspace", action="operation_support.publish")
    assert [item["id"] for item in recent_activities(session, actor)] == [own.id]
    admin = replace(actor, is_admin=True, permissions=frozenset({"*"}))
    assert {item["id"] for item in recent_activities(session, admin)} == {own.id, other.id}


def test_generic_private_resource_requires_live_read_grant_and_module_permission(context):
    session, actor, _ = context
    row = ResourceRecord(id="private-app", workspace_id=actor.workspace_id, kind="application", name="巡检助手",
        data={}, visibility="private", owner_id="another-person")
    session.add(row)
    session.flush()
    audit(session, actor, row.id, action="update", resource="application")
    assert recent_activities(session, actor) == []
    grant = ResourceGrant(workspace_id=actor.workspace_id, resource_type="application", resource_id=row.id,
        user_id=actor.user_id, actions=["read"])
    session.add(grant)
    session.flush()
    assert recent_activities(session, actor)[0]["message"] == "更新应用「巡检助手」"
    assert recent_activities(session, replace(actor, permissions=frozenset({"m01.read"}))) == []
    grant.actions = ["write"]
    session.flush()
    assert recent_activities(session, actor) == []


def test_internal_missing_deleted_or_kind_mismatched_events_are_not_ui_filler(context):
    session, actor, _ = context
    support(session, actor, identifier="deleted", deleted=True)
    support(session, actor, identifier="empty", name="")
    support(session, actor, identifier="uuid", name="12345678-abcd-abcd-abcd-1234567890ab")
    support(session, actor, identifier="wrong")
    audit(session, actor, "deleted")
    audit(session, actor, "missing", name="无权对象名称")
    audit(session, actor, "empty")
    audit(session, actor, "uuid")
    audit(session, actor, "wrong", resource="operation_form")
    audit(session, actor, "wrong", action="operation_support.validate")
    audit(session, actor, "wrong", action="credential.resolve", resource="credential")
    assert recent_activities(session, actor) == []


def test_repeated_updates_collapse_and_known_events_are_found_beyond_first_eight(context):
    session, actor, _ = context
    row = support(session, actor)
    known = audit(session, actor, row.id)
    for index in range(10):
        audit(session, actor, row.id, action="operation_support.validate", stamp=f"2026-10-21T02:00:{index:02}+00:00")
    newer = audit(session, actor, row.id, stamp="2026-10-21T03:00:00+00:00")
    assert [entry["id"] for entry in recent_activities(session, actor)] == [newer.id]
    assert known.id != newer.id


def test_candidate_budget_limits_scan_and_batch_queries_do_not_grow_per_event(context):
    session, actor, engine = context
    for index in range(12):
        config = support(session, actor, identifier=f"config-{index}", name=f"产线状态{index}")
        audit(session, actor, config.id, stamp=f"2026-10-21T03:00:{index:02}+00:00")
        application = ResourceRecord(workspace_id=actor.workspace_id, kind="application", name=f"巡检{index}",
            data={}, owner_id=actor.user_id, visibility="workspace")
        session.add(application)
        session.flush()
        audit(session, actor, application.id, action="create", resource="application")
    statements = []
    def count(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", count)
    try:
        result = recent_activities(session, actor)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(result) == 8
    assert len(statements) == 4
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_unknown_events_cannot_trigger_unbounded_history_or_object_reads(context, monkeypatch):
    from agent_platform.modules.workspace import activity
    session, actor, engine = context
    config = support(session, actor)
    audit(session, actor, config.id)
    for index in range(3):
        audit(session, actor, config.id, action="operation_support.validate", stamp=f"2026-10-21T04:00:{index:02}+00:00")
    monkeypatch.setattr(activity, "MAX_CANDIDATES", 2)
    statements = []
    def count(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))
    event.listen(engine, "before_cursor_execute", count)
    try:
        result = recent_activities(session, actor)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert result == []
    assert len(statements) == 1
    assert "LIMIT" in statements[0][0]
