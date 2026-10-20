"""仅新tmp SQLite/CSV与Mock验证行列隔离和业务历史，最多小量串行业务查询。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Organization, Role, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.modules.data import models as m
from agent_platform.modules.data.policy_sql import PARAMETER_PREFIX
from agent_platform.modules.data.row_policies import RowPolicyService
from agent_platform.modules.data.schemas import RowPolicyInput, RowPolicyPatch
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.sql_safety import authorize_sql, driver_query
from agent_platform.modules.manufacturing import models as manufacturing_models
from agent_platform.modules.reports import models as report_models


@pytest.fixture
def context(tmp_path):
    """四行两产线固定样本；只连接本测试新建文件，既有配置和网络均不参与。"""
    engine = create_engine("sqlite:///" + (tmp_path / "row-permissions.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Organization(id="org", name="新测试组织"))
        session.add(Workspace(id="ws", organization_id="org", name="新测试工作区"))
        session.add_all([Role(id="role-a", workspace_id="ws", name="产线甲", permissions=["m06.read"]), Role(id="role-b", workspace_id="ws", name="产线乙", permissions=["m06.read"])])
        for user_id, role in [("a1", "role-a"), ("a2", "role-a"), ("b1", "role-b"), ("none", None), ("admin", None)]:
            session.add(User(id=user_id, organization_id="org", username=user_id, display_name=user_id, password_hash="unused-test-placeholder", is_admin=user_id == "admin"))
            session.add(Membership(workspace_id="ws", user_id=user_id, role_ids=[role] if role else []))
        session.flush()
        settings = Settings(data_dir=tmp_path / "new-imports")
        admin = Actor("admin", "ws", frozenset({"*"}), ("admin",), "org", True)
        permissions = frozenset({"m06.read", "m06.write", "m10.read", "m10.write", "m11.read", "m11.write", "m07.read", "m07.write", "m07.publish"})
        actors = {user: Actor(user, "ws", permissions, ("产线甲" if user.startswith("a") else "产线乙",), "org") for user in ("a1", "a2", "b1", "none")}
        actors["admin"] = admin
        data = DataService(session, admin, settings)
        dataset = data.import_csv({"name": "新建两产线样本", "csv_text": "sample_id,line,value,unit,timestamp,period,load\nA1,A,10,g,2026-10-01T00:00:00Z,baseline,50\nA2,A,11,g,2026-10-02T00:00:00Z,recent,50\nB1,B,100,g,2026-10-01T00:00:00Z,baseline,50\nB2,B,110,g,2026-10-02T00:00:00Z,recent,50\n"})
        source = data.get_source(dataset["datasource_id"])
        data.patch_source(source.id, {"revision": source.revision, "field_roles": {dataset["table_name"]: {"line": ["admin"]}}})
        yield SimpleNamespace(session=session, settings=settings, actors=actors, data=data, source=source, table=dataset["table_name"], dataset=dataset,
                              policies=RowPolicyService(session, admin), engine=engine)
        session.rollback()
    engine.dispose()


def rule(role, line):
    return {"subject_type": "role", "subject_ids": [role], "conditions": [{"field": "line", "operator": "eq", "value": line}]}


def policy(context, rules=None, mode="rules"):
    payload = RowPolicyInput(table_name=context.table, mode=mode, rules=rules if rules is not None else [rule("role-a", "A"), rule("role-b", "B")]).model_dump()
    return context.policies.create(context.source.id, payload)


def data_for(context, user):
    return DataService(context.session, context.actors[user], context.settings)


def query(context, user="a1", sql=None, parameters=None):
    return data_for(context, user).execute_query({"datasource_id": context.source.id, "sql": sql or f'SELECT sample_id,value FROM "{context.table}" ORDER BY sample_id', "parameters": parameters or {}, "max_rows": 10})


def test_two_lines_unmatched_subject_and_private_filter_field(context):
    policy(context)
    one, two, nobody = query(context), query(context, "b1"), query(context, "none")
    assert [r["sample_id"] for r in one["rows"]] == ["A1", "A2"]
    assert [r["sample_id"] for r in two["rows"]] == ["B1", "B2"]
    assert nobody["rows"] == []
    assert one["parameters"] == {} and PARAMETER_PREFIX in one["executed_sql"]
    assert "line" not in [c["columnName"] for c in data_for(context, "a1").metadata(context.source.id)["tables"][0]["columns"]]
    with pytest.raises(DomainError):
        query(context, sql=f'SELECT line FROM "{context.table}"')


@pytest.mark.parametrize("kind", ["left", "self", "cte", "union", "aggregate", "or", "window"])
def test_physical_scope_filter_preserves_query_meaning(context, kind):
    policy(context)
    table = '"' + context.table + '"'
    statements = {
        "left": f"SELECT l.sample_id,r.value AS matched FROM {table} l LEFT JOIN {table} r ON r.sample_id='B1' ORDER BY l.sample_id",
        "self": f"SELECT l.sample_id FROM {table} l JOIN {table} r ON l.sample_id=r.sample_id ORDER BY l.sample_id",
        "cte": f"WITH x AS (SELECT * FROM {table}) SELECT sample_id FROM x ORDER BY sample_id",
        "union": f"SELECT sample_id FROM {table} WHERE sample_id='A1' UNION ALL SELECT sample_id FROM {table} WHERE sample_id='B1'",
        "aggregate": f"SELECT SUM(CAST(value AS INTEGER)) AS total FROM {table}",
        "or": f"SELECT sample_id FROM {table} WHERE 1=1 OR sample_id='B1' ORDER BY sample_id",
        "window": f"SELECT sample_id,COUNT(*) OVER () AS visible_count FROM {table} ORDER BY sample_id",
    }
    result = query(context, sql=statements[kind])
    if kind == "aggregate":
        assert result["rows"] == [{"total": 21}]
    else:
        assert [r["sample_id"] for r in result["rows"]] == (["A1"] if kind == "union" else ["A1", "A2"])
        if kind == "left":
            assert [r["matched"] for r in result["rows"]] == [None, None]
        if kind == "window":
            assert [r["visible_count"] for r in result["rows"]] == [2, 2]
    assert result["plan"]


def test_same_scope_collaboration_and_revocation_never_requery(context, monkeypatch):
    configured = policy(context)
    saved = query(context)
    peer = data_for(context, "a2")
    monkeypatch.setattr(DataService, "_path", lambda *args: pytest.fail("历史授权不得重新打开业务源"))
    assert peer.get_query(saved["id"]).id == saved["id"]
    with pytest.raises(DomainError) as denied:
        data_for(context, "b1").get_query(saved["id"])
    assert denied.value.code == "query_scope_denied"
    # 只有版本变化但有效条件相同，既有不可变事实仍可由同范围人员复核。
    context.policies.patch(configured.id, RowPolicyPatch(revision=configured.revision, mode="rules", rules=[rule("role-b", "B"), rule("role-a", "A")]).model_dump())
    assert peer.get_query(saved["id"]).id == saved["id"]
    context.session.scalar(select(Membership).where(Membership.user_id == "a2")).role_ids = []
    context.session.flush()
    with pytest.raises(DomainError) as denied:
        peer.get_query(saved["id"])
    assert denied.value.status == 403


def test_dynamic_identity_value_is_server_bound_and_scope_specific(context):
    configured = policy(context, [{"subject_type": "all", "conditions": [{"field": "sample_id", "operator": "eq", "value_source": "current_user_id"}]}])
    safe = data_for(context, "a1")._authorize_query(context.source, f'SELECT value FROM "{context.table}"', {}, 10)
    assert list(safe.parameters.values()) == ["a1"]
    saved = query(context)
    with pytest.raises(DomainError) as denied:
        data_for(context, "a2").get_query(saved["id"])
    assert denied.value.code == "query_scope_denied"
    assert configured.rules[0]["conditions"][0]["value"] is None


def test_legacy_results_work_only_before_any_effective_row_limit(context):
    saved = query(context)
    context.session.execute(delete(m.QueryAccessSnapshot).where(m.QueryAccessSnapshot.query_id == saved["id"]))
    assert data_for(context, "a2").get_query(saved["id"]).id == saved["id"]
    policy(context)
    with pytest.raises(DomainError) as denied:
        data_for(context, "a1").get_query(saved["id"])
    assert denied.value.code == "query_scope_denied"


def test_or_rules_and_conditions_intersect_before_aggregation(context):
    first = rule("role-a", "A")
    first["conditions"].append({"field": "sample_id", "operator": "eq", "value": "A1"})
    second = rule("role-a", "B")
    second["conditions"].append({"field": "sample_id", "operator": "eq", "value": "B2"})
    policy(context, [first, second])
    assert [r["sample_id"] for r in query(context)["rows"]] == ["A1", "B2"]


@pytest.mark.parametrize("change,path", [
    ({"field": "missing"}, "field"), ({"value": None}, "value"),
    ({"operator": "in", "value": []}, "value"), ({"operator": "in", "value": ["A", 1]}, "value"),
    ({"value_source": "current_user_id", "value": "forged"}, "value"),
    ({"operator": "is_null", "value": "A"}, "value"),
])
def test_invalid_conditions_are_field_errors_before_any_source_connection(context, change, path):
    rules = [rule("role-a", "A")]
    rules[0]["conditions"][0].update(change)
    with pytest.raises(DomainError) as invalid:
        policy(context, rules)
    assert invalid.value.code == "row_policy_invalid"
    assert invalid.value.fields["path"] == "rules.0.conditions.0." + path


def test_policy_edit_conflicts_foreign_subject_and_explicit_all(context):
    configured = policy(context)
    with pytest.raises(DomainError) as duplicate:
        policy(context)
    assert duplicate.value.code == "row_policy_exists"
    with pytest.raises(DomainError) as foreign:
        policy(context, [rule("foreign-role", "A")])
    assert foreign.value.fields["path"] == "rules.0.subject_ids"
    patched = context.policies.patch(configured.id, {"revision": 1, "mode": "all", "rules": []})
    assert patched.revision == 2 and len(query(context)["rows"]) == 4
    with pytest.raises(DomainError) as stale:
        context.policies.patch(configured.id, {"revision": 1, "mode": "rules", "rules": []})
    assert stale.value.code == "revision_conflict"


def test_visible_pagination_and_dataset_counts_use_current_scope(context):
    policy(context)
    first = query(context)
    query(context, "b1")
    second = query(context)
    reader = data_for(context, "a2")
    assert reader.visible_queries(limit=1)["total"] == 2
    assert reader.visible_queries(limit=1, offset=1)["items"][0]["id"] == first["id"]
    assert reader.visible_queries(limit=1)["items"][0]["id"] == second["id"]
    assert reader.dataset_view(context.dataset["id"])["row_count"] is None
    assert "line" not in reader.dataset_view(context.dataset["id"])["columns"]
    assert "rowCount" not in reader.metadata(context.source.id)["tables"][0]
    assert data_for(context, "none").visible_datasets()["total"] == 0


def test_reserved_parameters_and_controlled_es_are_rejected_before_connecting(context, monkeypatch):
    policy(context)
    reader = data_for(context, "a1")
    monkeypatch.setattr(DataService, "_path", lambda *args: pytest.fail("拒绝场景不得打开业务源"))
    with pytest.raises(DomainError) as reserved:
        query(context, sql=f'SELECT value FROM "{context.table}" WHERE sample_id=:{PARAMETER_PREFIX}x', parameters={PARAMETER_PREFIX + "x": "A1"})
    assert reserved.value.code == "reserved_query_parameter"
    original = context.source.provider
    context.source.provider = "elasticsearch"
    try:
        with pytest.raises(DomainError) as unsupported:
            reader._authorize_query(context.source, f'SELECT value FROM "{context.table}"', {}, 10)
        assert unsupported.value.code == "row_policy_provider_unsupported"
    finally:
        context.source.provider = original


@pytest.mark.parametrize("provider", ["postgres", "mysql", "http_json"])
def test_connectors_receive_the_same_policy_ast_with_bound_values(context, provider):
    """只检查驱动协议和SQL，不连接所声明的目标，也不调用HTTP。"""
    policy(context)
    original, original_config = context.source.provider, context.source.connection_config
    context.source.provider = provider
    if provider in ("postgres", "mysql"):
        context.source.connection_config = {"schema_name": "factory_a", "database": "factory_data"}
    try:
        safe = data_for(context, "a1")._authorize_query(context.source, f'SELECT value FROM "{context.table}" WHERE sample_id=:sample', {"sample": "A1"}, 10)
        sql, parameters = driver_query(safe, "sqlite" if provider == "http_json" else provider)
        assert parameters["sample"] == "A1" and "A" in parameters.values()
        assert "WHERE" in sql and "FROM (SELECT" in sql
        assert "'A'" not in sql
        if provider == "postgres":
            assert '"factory_a".' in sql
        elif provider == "mysql":
            assert '`factory_data`.' in sql
        assert safe.access_scope["row_scope"][context.table]["mode"] == "rules"
    finally:
        context.source.provider = original
        context.source.connection_config = original_config


def test_cte_name_cannot_hide_a_physical_table_in_another_scope():
    schema = [{"tableName": "allowed", "columns": [{"columnName": "value"}]}]
    with pytest.raises(DomainError):
        authorize_sql("SELECT * FROM (WITH blocked AS (SELECT value FROM allowed) SELECT value FROM blocked) x UNION ALL SELECT value FROM blocked", provider="sqlite", tables=schema, max_rows=5)
    with pytest.raises(DomainError):
        authorize_sql("WITH allowed AS (SELECT 1 AS value) SELECT value FROM other.allowed", provider="sqlite", tables=schema, max_rows=5)


def test_dataset_write_responses_do_not_reveal_restricted_totals(context):
    from agent_platform.modules.data.router import patch_dataset, patch_source, delete_dataset
    from agent_platform.modules.data.schemas import DatasetPatch, SourcePatch
    policy(context)
    reader = context.actors["a1"]
    updated = patch_dataset(context.dataset["id"], DatasetPatch(revision=1, name="改名后的样本"), context.session, reader)
    assert updated["row_count"] is None and "line" not in updated["columns"]
    source = patch_source(context.source.id, SourcePatch(revision=context.source.revision, name="改名后的来源"), context.session, reader)
    assert "rowCount" not in source["schema_snapshot"][0]
    assert "line" not in [column["columnName"] for column in source["schema_snapshot"][0]["columns"]]
    deleted = delete_dataset(context.dataset["id"], updated["revision"], context.session, reader)
    assert deleted["archived"] is True and deleted["row_count"] is None


def test_policy_expansion_budget_rejects_before_source_connection(context, monkeypatch):
    rules = [{"subject_type": "all", "conditions": [{"field": "sample_id", "operator": "in", "value": [f"{i}-{j}" for j in range(100)]} for i in range(10)]}]
    policy(context, rules)
    monkeypatch.setattr(DataService, "_path", lambda *args: pytest.fail("权限条件超限不得打开源"))
    with pytest.raises(DomainError) as rejected:
        query(context)
    assert rejected.value.code == "row_policy_budget_exceeded"


def test_quality_report_and_maintenance_share_current_row_evidence(context, monkeypatch):
    """实际取数/样本映射/判定/另一人复核/正式报告；撤权后业务历史无隐式重查。"""
    from agent_platform.modules.manufacturing import models as manufacturing
    from agent_platform.modules.manufacturing.schemas import AnalysisInput, InspectionImport
    from agent_platform.modules.manufacturing.service import ManufacturingService
    from agent_platform.modules.reports.service import ReportService

    policy(context)
    data = data_for(context, "a1")
    factory = ManufacturingService(context.session, context.actors["a1"])
    peer = ManufacturingService(context.session, context.actors["a2"])
    product = factory.create(manufacturing.Product, {"name": "产线甲产品"})
    standard = factory.create(manufacturing.InspectionTemplate, {"name": "质量标准", "target_type": "product", "metrics": [{"id": "mass", "name": "质量", "unit": "g", "standard_max": "20"}]})
    inspection = factory.create_inspection({"name": "产线甲首检", "target_type": "product", "product_id": product["id"], "template_id": standard["id"], "template_revision": 1, "expected_samples": 2})
    saved = query(context, sql=f'SELECT * FROM "{context.table}"')
    imported = factory.import_inspection(inspection["id"], InspectionImport(revision=1, query_id=saved["id"], mappings=[{"metric_id": "mass", "value_field": "value", "unit_field": "unit"}]), context.settings)
    checked = factory.run_inspection(inspection["id"], imported["revision"])
    assert checked["outcome"] == "passed" and len(checked["result"]["samples"]) == 2
    reviewed = peer.review(inspection["id"], {"revision": checked["revision"], "outcome": "passed", "note": "同产线独立复核"})["inspection"]
    reports = ReportService(context.session, context.actors["a2"], context.settings)
    report = reports.generate_inspection({"name": "产线甲检验报告", "inspection_id": inspection["id"], "run_id": reviewed["result"]["run_id"], "inspection_revision": reviewed["revision"]})
    assert "A1" in reports.export(report["id"], "html")[0]
    device = factory.create(manufacturing.Device, {"name": "产线甲设备"})
    analysis = factory.create_analysis(AnalysisInput(device_id=device["id"], datasource_id=context.source.id, sql=f'SELECT * FROM "{context.table}"', min_samples=2), context.settings)
    assert data.get_query(analysis["provenance"]["query_id"]).result["row_count"] == 2
    original_facts = deepcopy(reviewed["result"])
    member = context.session.scalar(select(Membership).where(Membership.user_id == "a2"))
    member.role_ids = ["role-b"]
    context.session.flush()
    monkeypatch.setattr(DataService, "_path", lambda *args: pytest.fail("撤权后业务读取不得重查源"))
    for read in (lambda: peer.inspection_results(inspection["id"]), lambda: peer.get(manufacturing.MaintenanceAnalysis, analysis["id"]), lambda: reports.export(report["id"], "html"), lambda: reports.export(report["id"], "html", revision=1)):
        with pytest.raises(DomainError) as denied:
            read()
        assert denied.value.status == 403
    assert context.session.get(manufacturing.Inspection, inspection["id"]).result == original_facts
