"""财务口径执行前校验：保存元数据、纯 AST 和隔离 SQLite，不连接真实业务库或模型。"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.errors import ValidationError
from app.modules.analytics import queries, semantic
from app.modules.analytics.finance_semantics import finance_categories, validate_finance_query
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sql_safety import authorize_sql
from app.modules.business_ai.context import auth_context_from_tool
from test_analytics import source


def financial_tables():
    """应收、应付、客户各有明确字段含义，并声明客户和组织复合外键。"""
    return [
        {"tableName": "ar", "tableComment": "应收账款", "columns": [
            {"columnName": "id", "dataType": "INTEGER", "primaryKey": True},
            {"columnName": "customer", "dataType": "INTEGER", "comment": "客户标识"},
            {"columnName": "org", "dataType": "INTEGER", "comment": "组织标识"},
            {"columnName": "balance", "dataType": "DECIMAL", "comment": "应收余额"},
            {"columnName": "received", "dataType": "DECIMAL", "comment": "已收金额"}],
         "foreignKeys": [{"columns": ["customer", "org"], "referredTable": "customers", "referredColumns": ["id", "org"]}]},
        {"tableName": "ap", "tableComment": "资金支付明细", "columns": [
            {"columnName": "id", "dataType": "INTEGER", "primaryKey": True},
            {"columnName": "customer", "dataType": "INTEGER", "comment": "客户标识"},
            {"columnName": "org", "dataType": "INTEGER", "comment": "组织标识"},
            {"columnName": "payee", "dataType": "TEXT", "comment": "收款单位"},
            {"columnName": "unpaid", "dataType": "DECIMAL", "comment": "未付金额"}],
         "foreignKeys": [{"columns": ["customer", "org"], "referredTable": "customers", "referredColumns": ["id", "org"]}]},
        {"tableName": "customers", "tableComment": "客户信息", "columns": [
            {"columnName": "id", "dataType": "INTEGER", "primaryKey": True},
            {"columnName": "org", "dataType": "INTEGER"},
            {"columnName": "name", "dataType": "TEXT", "comment": "客户名称"}]},
        {"tableName": "contracts", "tableComment": "销售合同", "columns": [
            {"columnName": "amount", "dataType": "DECIMAL", "comment": "合同金额"}]},
    ]


def validate(sql, *, task="", current="", tables=None, previous=()):
    """先走真实 AST 授权，再走无副作用业务语义校验。"""
    tables = tables or financial_tables()
    safe = authorize_sql(sql, provider="sqlite", tables=tables, max_rows=100)
    intent = {"currentRequest": current, "plannerTask": task,
              "previousUserTurns": [{"request": request} for request in previous]} if current or previous else None
    return validate_finance_query(safe, tables, task, user_intent=intent)


@pytest.mark.parametrize("sql", ["SELECT unpaid FROM ap", "WITH p AS (SELECT unpaid AS amount FROM ap) SELECT SUM(amount) AS total FROM p",
                                 "SELECT SUM(unpaid) AS total FROM ap UNION ALL SELECT SUM(balance) AS total FROM ar"])
def test_payable_cannot_satisfy_receivable_even_through_cte_or_union(sql):
    with pytest.raises(ValidationError) as caught:
        validate(sql, task="查询应收余额", current="上个月应收做成大屏")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_direction_mismatch"
    assert caught.value.extra["executionBlocked"] is True and caught.value.extra["businessDataQueried"] is False
    assert caught.value.extra["canProveDataAbsence"] is False


@pytest.mark.parametrize("sql", ["SELECT unpaid AS 应收金额 FROM ap", "SELECT amount AS 应收金额 FROM contracts",
                                 "SELECT received AS 应收余额 FROM ar"])
def test_result_alias_cannot_relabel_money(sql):
    with pytest.raises(ValidationError) as caught:
        validate(sql)
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_measure_mismatch"


@pytest.mark.parametrize("sql", ["SELECT amount FROM contracts", "SELECT received FROM ar"])
def test_unproven_contract_or_received_money_is_not_receivable(sql):
    with pytest.raises(ValidationError) as caught:
        validate(sql, task="应收金额")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_measure_unverified"


@pytest.mark.parametrize("sql", ["SELECT 0 AS 应收金额", "SELECT COUNT(*) AS 应收笔数 FROM ap"])
def test_constants_or_wrong_direction_counts_do_not_invent_receivables(sql):
    with pytest.raises(ValidationError) as caught:
        validate(sql)
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_measure_unverified"


def test_finance_date_label_keeps_table_context_but_identifier_cannot_be_labeled_money():
    tables = financial_tables()
    tables[1]["columns"].append({"columnName": "planned_date", "dataType": "DATE", "comment": "计划日期"})
    assert validate("SELECT planned_date AS 付款计划日期 FROM ap", task="付款计划日期", tables=tables)["status"] == "context_only"
    with pytest.raises(ValidationError):
        validate("SELECT id AS 应收金额 FROM ar")


def test_mixed_total_request_can_execute_one_payable_subtask_without_replanning():
    evidence = validate("SELECT SUM(unpaid) AS 未付金额 FROM ap", task="本次查询应付未付金额", current="查询应收和应付并做大屏")
    assert evidence["status"] == "validated" and evidence["actualCategories"] == ["payable"]
    with pytest.raises(ValidationError) as caught:
        validate("SELECT unpaid FROM ap", task="改为查应付", current="只查询应收")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_task_scope_mismatch"
    assert validate("SELECT unpaid FROM ap", task="查询应付", current="改成应付", previous=["查询应收"])["status"] == "validated"


def test_dimension_and_fixed_diagnostic_queries_do_not_claim_money():
    assert validate("SELECT id,name FROM customers", task="获取客户标识和名称", current="查询应收并做大屏")["status"] == "context_only"
    assert validate("SELECT COUNT(*) AS record_count FROM ap")["status"] == "context_only"
    assert validate("SELECT SUM(balance) AS 应收金额 FROM ar", task="按客户统计", current="继续做大屏", previous=["查询应收"])["status"] == "validated"
    assert validate("SELECT unpaid AS 未付金额 FROM ap", task="查询付款情况")["status"] == "validated"
    with pytest.raises(ValidationError):
        validate("SELECT unpaid AS 未付金额 FROM ap", task="查询实际付款金额")


@pytest.mark.parametrize("sql", [
    "SELECT ap.unpaid,c.name FROM ap JOIN customers c ON ap.payee=c.name",
    "SELECT ar.balance,c.name FROM ar JOIN customers c ON ar.customer=c.id",
    "SELECT ar.balance,c.name FROM ar JOIN customers c ON ar.customer=c.id OR ar.org=c.org",
    "SELECT ar.balance,c.name FROM ar JOIN customers c ON CAST(ar.customer AS TEXT)=CAST(c.id AS TEXT) AND ar.org=c.org",
    "SELECT ar.balance,c.name FROM ar CROSS JOIN customers c",
])
def test_names_partial_keys_or_disjunctions_are_not_declared_relationships(sql):
    with pytest.raises(ValidationError) as caught:
        validate(sql)
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_join_unverified"


@pytest.mark.parametrize("sql", [
    "SELECT ar.balance,c.name FROM ar JOIN customers c ON ar.customer=c.id AND ar.org=c.org",
    "WITH a AS (SELECT customer,org,balance FROM ar) SELECT a.balance,c.name FROM a JOIN customers c ON a.customer=c.id AND a.org=c.org",
    "SELECT ar.balance,c.name FROM ar, customers c WHERE ar.customer=c.id AND ar.org=c.org",
])
def test_complete_declared_keys_work_through_aliases_and_ctes(sql):
    evidence = validate(sql, task="查询应收余额")
    assert evidence["status"] == "validated" and evidence["verifiedJoinConditions"] == 2


def test_cross_finance_amount_comparison_is_allowed_after_declared_joins():
    evidence = validate("SELECT ar.balance,ap.unpaid FROM ar JOIN customers c ON ar.customer=c.id AND ar.org=c.org "
        "JOIN ap ON ap.customer=c.id AND ap.org=c.org WHERE ar.balance>ap.unpaid", task="比较应收和应付金额")
    assert evidence["status"] == "validated" and evidence["verifiedJoinConditions"] == 4
    with pytest.raises(ValidationError) as caught:
        validate("SELECT a.receivable+p.payable AS 应收金额 FROM (SELECT SUM(balance) receivable FROM ar) a "
            "CROSS JOIN (SELECT SUM(unpaid) payable FROM ap) p", task="比较应收和应付金额")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_measure_mismatch"
    evidence = validate("SELECT SUM(CASE WHEN ap.unpaid>0 THEN ar.balance ELSE 0 END) AS 应收金额 "
        "FROM ar JOIN customers c ON ar.customer=c.id AND ar.org=c.org "
        "JOIN ap ON ap.customer=c.id AND ap.org=c.org", task="核对有付款记录的应收金额", current="核对应收和付款")
    assert evidence["actualCategories"] == ["receivable"]


def test_counting_amounts_does_not_turn_row_counts_into_money():
    for sql in ("SELECT COUNT(balance) AS 应收金额 FROM ar",
                "WITH a AS (SELECT COUNT(balance) AS n FROM ar) SELECT n AS 应收金额 FROM a"):
        with pytest.raises(ValidationError):
            validate(sql)
    assert validate("WITH a AS (SELECT COUNT(balance) AS n FROM ar) SELECT n AS 应收笔数 FROM a")["status"] == "context_only"


def test_independent_scalar_finance_totals_can_be_combined():
    evidence = validate("SELECT a.receivable,p.payable FROM (SELECT SUM(balance) receivable FROM ar) a "
        "CROSS JOIN (SELECT SUM(unpaid) payable FROM ap) p", task="查询应收和应付金额")
    assert evidence["status"] == "validated" and set(evidence["actualCategories"]) == {"receivable", "payable"}


def test_saved_wrong_finance_sql_fails_before_reading_any_business_data():
    data = json.loads((Path(__file__).parents[1] / "fixtures" / "finance_payment_direction.json").read_text(encoding="utf-8"))
    safe = authorize_sql(data["rejectedSql"], provider="mysql", tables=data["tables"], max_rows=100)
    with pytest.raises(ValidationError) as caught:
        validate_finance_query(safe, data["tables"], task="查询应收明细")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_join_unverified"
    assert "收款单位" in caught.value.extra["correction"]


def test_finance_recall_expansion_does_not_cross_funds_direction():
    assert not {"付款", "应付", "payment", "payable"}.intersection(semantic._expanded_terms("上个月应收"))
    assert not {"收款", "应收", "receivable"}.intersection(semantic._expanded_terms("应付金额"))
    assert {"应收", "应付"}.issubset(semantic._expanded_terms("应收与应付"))
    assert finance_categories("查询收款单位名称") == set()
    assert finance_categories("不要查应付，只看应收") == {"receivable"}
    assert finance_categories("应收款、未收款") == {"receivable"}
    assert finance_categories("应付款、未付款") == {"payable"}
    assert finance_categories("unpaid_amount") == {"payable"}
    for instruction in ("不要改为付款", "不能换成应付", "不得改成付款", "不能擅自替换为付款"):
        assert finance_categories("查询应收，" + instruction) == {"receivable"}


def test_explicit_receipt_metadata_overrides_ambiguous_english_payment_table_name():
    tables = [{"tableName": "customer_payment", "tableComment": "客户回款", "columns": [
        {"columnName": "amount", "dataType": "DECIMAL", "comment": "金额"}]}]
    assert validate("SELECT SUM(amount) AS 收款金额 FROM customer_payment", task="收款金额", tables=tables)["status"] == "validated"


def test_union_counts_cannot_hide_opposite_business_direction():
    with pytest.raises(ValidationError) as caught:
        validate("SELECT COUNT(*) AS n FROM ap UNION ALL SELECT COUNT(*) AS n FROM ar", task="查询应收记录数")
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_direction_mismatch"


@pytest.mark.parametrize("mode", ["sql", "natural_language"])
def test_public_execution_blocks_bad_finance_before_provider_in_both_modes(client, administrator, db, source, monkeypatch, mode):
    from app.models import AuthIdentity
    from sqlalchemy import select
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    ctx = auth_context_from_tool({"db": db, "workspace_id": administrator["X-Workspace-Id"], "user_id": identity.user_id})
    monkeypatch.setattr(queries, "source_metadata", lambda *args: SimpleNamespace(tables_json=financial_tables()))
    monkeypatch.setattr(queries, "recall_tables", lambda *args, **kwargs: (financial_tables(), []))
    generation = []
    def generate(*args, **kwargs):
        generation.append(True)
        return "SELECT unpaid FROM ap", {}
    monkeypatch.setattr(queries, "natural_language_sql", generate)
    monkeypatch.setattr(queries, "execute_read", lambda *args: pytest.fail("校验失败后不能执行任何业务 SQL"))
    request = QueryInput(datasourceId=source["id"], mode=mode, query="SELECT unpaid FROM ap" if mode == "sql" else "查询应收")
    with pytest.raises(ValidationError) as caught:
        queries.execute_query(ctx, request, user_intent={"currentRequest": "查询应收", "plannerTask": "查询应收",
            "previousUserTurns": [], "runId": "isolated-test", "conversationId": "", "executionOwner": "dsh"})
    assert caught.value.extra["semanticValidation"]["reasonCode"] == "finance_direction_mismatch"
    assert generation == ([] if mode == "sql" else [True])
