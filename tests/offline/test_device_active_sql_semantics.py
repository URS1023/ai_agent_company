"""设备删除标记的执行前语义回归：真实 AST、模型伪响应，禁止执行数据库查询。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.modules.analytics import queries
from app.modules.analytics.inspection_semantics import validate_device_active_records
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sql_safety import SqlFieldValidationError, authorize_sql


TABLES = [{"tableName": "t_devicemanagement", "columns": [
    {"columnName": "deviceManagementId"}, {"columnName": "c_deviceName"}, {"columnName": "isDeleted"},
]}]
QUESTION = "查询未删除设备的名称"


def validate(sql, question=QUESTION, tables=None):
    """使用实际授权解析链；不实例化平台数据库或业务连接。"""
    tables = TABLES if tables is None else tables
    query = authorize_sql(sql, provider="mysql", tables=tables, max_rows=10, database="fixture")
    validate_device_active_records(query, question, tables)


@pytest.mark.parametrize("condition", [
    "d.isDeleted=0 OR d.isDeleted IS NULL",
    "COALESCE(d.isDeleted,0)=0",
    "IFNULL(d.isDeleted,0)=0",
    "d.isDeleted=0 OR d.c_deviceName='任意设备'",
    "d.isDeleted<>1",
])
def test_unknown_delete_status_cannot_be_treated_as_active(condition):
    with pytest.raises(SqlFieldValidationError, match="isDeleted = 0"):
        validate("SELECT d.c_deviceName FROM t_devicemanagement d WHERE " + condition)


def test_missing_active_filter_is_rejected():
    with pytest.raises(SqlFieldValidationError):
        validate("SELECT c_deviceName FROM t_devicemanagement")


@pytest.mark.parametrize("condition", [
    "d.isDeleted=0", "0=d.isDeleted", "d.isDeleted='0'",
    "d.isDeleted=0 AND (d.c_deviceName='甲' OR d.c_deviceName='乙')",
    "(d.isDeleted=0 AND d.c_deviceName='甲') OR (d.isDeleted=0 AND d.c_deviceName='乙')",
    "d.isDeleted=0 AND COALESCE(d.c_deviceName,'')<>''",
])
def test_strict_zero_and_unrelated_null_display_are_valid(condition):
    validate("SELECT d.c_deviceName FROM t_devicemanagement d WHERE " + condition)


@pytest.mark.parametrize("question,condition", [
    ("查看删除标记为 NULL 的设备", "d.isDeleted IS NULL"),
    ("查询未删除设备，也包含删除标记为 NULL 的设备", "d.isDeleted=0 OR d.isDeleted IS NULL"),
    ("未删除设备统计，删除标记 NULL 也算未删除", "COALESCE(d.isDeleted,0)=0"),
    ("查询已删除设备", "d.isDeleted=1"),
    ("分别统计未删除和已删除设备", "d.isDeleted IN (0,1)"),
    ("查看设备，不限定删除状态", "d.isDeleted IS NULL"),
])
def test_explicit_null_deleted_or_unspecified_scope_is_not_overridden(question, condition):
    validate("SELECT d.c_deviceName FROM t_devicemanagement d WHERE " + condition, question)


@pytest.mark.parametrize("question", [
    "查询未删除设备，名称空值保持空值",
    "查询未删除设备，排除删除标记 NULL",
    "查询有效设备记录，不包含已删除设备",
])
def test_unrelated_null_or_exclusion_does_not_disable_rule(question):
    with pytest.raises(SqlFieldValidationError):
        validate("SELECT d.c_deviceName FROM t_devicemanagement d WHERE d.isDeleted=0 OR d.isDeleted IS NULL", question)


def test_external_table_and_unknown_signature_are_not_given_device_rules():
    external = [{"tableName": "external_devices", "columns": TABLES[0]["columns"]}]
    validate("SELECT c_deviceName FROM external_devices WHERE isDeleted=0 OR isDeleted IS NULL", tables=external)
    unknown = [{"tableName": "t_devicemanagement", "columns": [{"columnName": "name"}, {"columnName": "isDeleted"}]}]
    validate("SELECT name FROM t_devicemanagement WHERE isDeleted=0 OR isDeleted IS NULL", tables=unknown)


def test_scope_filter_cannot_be_borrowed_from_another_union_branch():
    with pytest.raises(SqlFieldValidationError):
        validate("SELECT c_deviceName FROM t_devicemanagement WHERE isDeleted=0 UNION ALL "
                 "SELECT c_deviceName FROM t_devicemanagement WHERE COALESCE(isDeleted,0)=0")


@pytest.mark.parametrize("join", ["LEFT JOIN", "LEFT OUTER JOIN"])
def test_left_join_right_active_filter_is_valid_with_preserved_side_where(join):
    tables = TABLES + [{"tableName": "t_dinspectionmaintenanceplan", "columns": [
        {"columnName": "dInspectionMaintenancePlanId"}, {"columnName": "c_planName"}, {"columnName": "isDeleted"},
    ]}]
    validate("SELECT d.c_deviceName,p.c_planName FROM t_devicemanagement d " + join +
             " t_dinspectionmaintenanceplan p ON p.dInspectionMaintenancePlanId=d.deviceManagementId "
             "AND p.isDeleted=0 WHERE d.isDeleted=0", tables=tables)


@pytest.mark.parametrize("condition", [
    "d.isDeleted=0 AND p.isDeleted=0",
    "p.isDeleted=0",
])
def test_left_join_on_cannot_filter_the_preserved_device_rows(condition):
    tables = TABLES + [{"tableName": "t_dinspectionmaintenanceplan", "columns": [
        {"columnName": "dInspectionMaintenancePlanId"}, {"columnName": "isDeleted"},
    ]}]
    with pytest.raises(SqlFieldValidationError, match="t_devicemanagement"):
        validate("SELECT d.c_deviceName FROM t_devicemanagement d LEFT JOIN t_dinspectionmaintenanceplan p "
                 "ON p.dInspectionMaintenancePlanId=d.deviceManagementId AND " + condition, tables=tables)


def test_left_join_right_unknown_delete_flag_still_fails():
    tables = TABLES + [{"tableName": "t_dinspectionmaintenanceplan", "columns": [
        {"columnName": "dInspectionMaintenancePlanId"}, {"columnName": "isDeleted"},
    ]}]
    with pytest.raises(SqlFieldValidationError, match="t_dinspectionmaintenanceplan"):
        validate("SELECT d.c_deviceName FROM t_devicemanagement d LEFT JOIN t_dinspectionmaintenanceplan p "
                 "ON p.dInspectionMaintenancePlanId=d.deviceManagementId "
                 "AND (p.isDeleted=0 OR p.isDeleted IS NULL) WHERE d.isDeleted=0", tables=tables)


@pytest.mark.parametrize("repaired", [True, False])
def test_natural_language_has_one_model_repair_and_never_executes_bad_sql(monkeypatch, repaired):
    invalid = "SELECT c_deviceName FROM t_devicemanagement WHERE isDeleted=0 OR isDeleted IS NULL"
    valid = "SELECT c_deviceName FROM t_devicemanagement WHERE isDeleted=0"
    responses = [{"content": json.dumps({"sql": sql}), "usage": {"total_tokens": 10}}
                 for sql in [invalid, valid if repaired else invalid]]
    model = Mock(side_effect=responses)
    execute = Mock(side_effect=AssertionError("生成和修正阶段禁止连接数据库"))
    monkeypatch.setattr(queries.model, "chat_for_run", model)
    monkeypatch.setattr(queries, "execute_read", execute)
    payload = QueryInput(datasourceId="source", query=QUESTION, mode="natural_language", maxRows=10)
    source = SimpleNamespace(id="source", provider="mysql", connection_config={"databaseName": "fixture"})
    call = lambda: queries.natural_language_sql(SimpleNamespace(workspace_id="fixture"), source, payload, TABLES)
    if repaired:
        sql, usage = call()
        assert sql == valid and usage["sqlRepairs"] == 1 and usage["modelCalls"] == 2
    else:
        with pytest.raises(SqlFieldValidationError):
            call()
    assert model.call_count == 2 and execute.call_count == 0
    correction = json.loads(model.call_args.args[1][-1]["content"])
    assert "isDeleted = 0" in correction["validationError"]
    assert "数据库尚未执行" in correction["instruction"]
