"""Validate JSON functions through the real SQL AST guard, without executing SQL.

中文说明：通过真实的 SQL AST 守卫校验 JSON 函数用法，但全程不执行 SQL。
隔离方式：sqlglot/AST 解析 + 纯函数断言，不连数据库。
边界：函数放行与否取决于白名单策略，不能因为函数合法就放宽语句整体校验。
"""
import pytest

from app.errors import ValidationError
from app.modules.analytics.sql_safety import authorize_sql

TABLES = [{"tableName": "devices", "columns": [
    {"columnName": "id"}, {"columnName": "name"}, {"columnName": "status"}]}]


@pytest.mark.parametrize("expression", [
    "JSON_ARRAYAGG(name)",
    "JSON_ARRAYAGG(JSON_OBJECT('id', id, 'name', name))",
    "JSON_OBJECTAGG(id, name)",
    "JSON_ARRAY(id, name)",
])
def test_read_only_json_functions_are_authorized(expression):
    result = authorize_sql(f"SELECT {expression} AS result FROM devices",
                           provider="mysql", tables=TABLES, max_rows=20)
    assert result.tables == ("devices",)
    assert "LIMIT 21" in result.sql
    assert "JSON_" in result.sql


@pytest.mark.parametrize("sql", [
    "SELECT JSON_ARRAYAGG(SLEEP(10)) FROM devices",
    "SELECT JSON_OBJECT('x', LOAD_FILE('/etc/passwd')) FROM devices",
    "SELECT JSON_ARRAYAGG(secret) FROM devices",
    "SELECT JSON_ARRAYAGG(name) FROM other.devices",
    "SELECT JSON_ARRAYAGG(table_name) FROM information_schema.tables",
    "SELECT JSON_ARRAYAGG(name) FROM devices; DELETE FROM devices",
    "SELECT JSON_ARRAYAGG(name) INTO OUTFILE '/tmp/data' FROM devices",
    "SELECT JSON_ARRAYAGG(name) FROM devices FOR UPDATE",
])
def test_json_does_not_bypass_sql_authorization(sql):
    with pytest.raises(ValidationError):
        authorize_sql(sql, provider="mysql", tables=TABLES, max_rows=20)
