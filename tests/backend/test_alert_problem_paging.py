"""Real alert evidence, permission filtering and bounded page query work.

中文说明：验证预警报告/问题的分页查询在权限过滤下仍然有界（查询次数不随问题数或跳过的行数增长），
且拒绝访问的父级报告在分页前就被过滤、过滤条件不会被替换。
隔离方式：client 夹具（内存 SQLite）+ 统计真实执行的 SELECT 次数，不连业务库、不调用模型。
"""
from contextlib import contextmanager

from sqlalchemy import event, select

from app.db import get_engine
from app.modules.alerts.models import AlertProblem
from app.modules.system.models import Role
from test_alerts import API, business_node, equipment, policy
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute, graph, result


def produce_report(client, headers, *, code="PAGING", count=25):
    rule, _ = policy(client, headers, equipment(client, headers, code=code))
    flow = create_flow(client, headers, graph(business_node(rule)))
    execution = result(execute(client, headers, flow, {
        "data": [{"device": f"{code}-{index}", "amount": 200} for index in range(count)],
    }))
    return execution["result"]["outputs"]["end"]["alertReportId"]


@contextmanager
def counted_selects():
    statements = []
    engine = get_engine()
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def page(client, headers, **params):
    with counted_selects() as statements:
        rows = result(client.get(API + "/problems", headers=headers, params=params))
    return rows, len(statements)


def test_shared_report_query_work_does_not_grow_per_problem_or_skipped_row(client, administrator, db):
    report_id = produce_report(client, administrator, count=225)
    expected = list(db.scalars(select(AlertProblem.id).where(AlertProblem.report_id == report_id)
                              .order_by(AlertProblem.created_at.desc(), AlertProblem.id)))
    single, first_count = page(client, administrator, limit=1)
    first, twenty_count = page(client, administrator, limit=20)
    later, later_count = page(client, administrator, limit=20, offset=200)
    filtered, filtered_count = page(client, administrator, limit=20, offset=200, reportId=report_id)
    print({"single": first_count, "twenty": twenty_count, "offset200": later_count,
           "explicitReportOffset200": filtered_count})
    assert [row["id"] for row in single] == expected[:1]
    assert [row["id"] for row in first] == expected[:20]
    assert [row["id"] for row in later] == expected[200:220]
    assert filtered == later
    assert twenty_count <= first_count + 2, "Repeated report authorization for each problem"
    assert later_count <= first_count + 3, "Skipped problems repeat the same evidence queries"
    assert filtered_count <= first_count + 2, "Explicit report still authorizes each problem"


def test_denied_report_is_filtered_before_offset_and_permissions_are_fresh(client, administrator, db):
    allowed_id = produce_report(client, administrator, code="VISIBLE", count=7)
    denied_id = produce_report(client, administrator, code="HIDDEN", count=8)
    broad = create_role(client, administrator, "problem_reader", permissions=[
        "home.view", "alerts.view", "workflows.view",
    ], scope="workspace")
    devices = create_role(client, administrator, "own_devices", permissions=["devices.view"], scope="self")
    create_user(client, administrator, [broad["id"], devices["id"]])
    reader = activate_user(client)
    from app.models import AuthIdentity
    from app.modules.alerts.models import AlertReport, Device
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "test-reader"))
    allowed_device = db.get(Device, db.get(AlertReport, allowed_id).device_id)
    allowed_device.owner_user_id = identity.user_id
    db.commit()
    expected = list(db.scalars(select(AlertProblem.id).where(AlertProblem.report_id == allowed_id)
                              .order_by(AlertProblem.created_at.desc(), AlertProblem.id)))
    rows, _ = page(client, reader, limit=3, offset=2)
    assert [row["id"] for row in rows] == expected[2:5]
    assert client.get(API + "/problems", headers=reader, params={"reportId": denied_id}).status_code == 404
    role = db.get(Role, devices["id"])
    role.permissions = [p for p in role.permissions if p != "devices.view"]
    db.commit()
    rows, _ = page(client, reader, limit=3, offset=0)
    assert rows == []
    assert client.get(API + "/problems", headers=reader, params={"reportId": allowed_id}).status_code == 403


def test_report_authorization_does_not_replace_problem_scope_or_status_filter(client, administrator, db):
    report_id = produce_report(client, administrator, count=6)
    broad = create_role(client, administrator, "parents_reader", permissions=[
        "home.view", "workflows.view", "devices.view",
    ], scope="workspace")
    own = create_role(client, administrator, "own_alerts", permissions=["alerts.view"], scope="self")
    create_user(client, administrator, [broad["id"], own["id"]])
    reader = activate_user(client)
    from app.models import AuthIdentity
    from app.modules.alerts.models import AlertReport, AlertRule, AlertRuleRevision
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "test-reader"))
    assert identity is not None
    report = db.get(AlertReport, report_id)
    rule = db.get(AlertRule, report.rule_id)
    revision = db.scalar(select(AlertRuleRevision).where(AlertRuleRevision.rule_id == rule.id))
    for parent in [report, rule, revision]:
        parent.owner_user_id = identity.user_id
    problems = list(db.scalars(select(AlertProblem).where(AlertProblem.report_id == report_id)
                               .order_by(AlertProblem.created_at.desc(), AlertProblem.id)))
    for index, problem in enumerate(problems):
        if index % 2 == 0:
            problem.owner_user_id = identity.user_id
        problem.status = "resolved" if index == 2 else "open"
    db.commit()
    rows, _ = page(client, reader, limit=10, reportId=report_id, status="open")
    assert [row["id"] for row in rows] == [problems[i].id for i in [0, 4]]
    rows, _ = page(client, reader, limit=1, offset=1, status="open")
    assert [row["id"] for row in rows] == [problems[4].id]
