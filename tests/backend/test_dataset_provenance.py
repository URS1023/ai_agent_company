"""验证数据集来源解析（物理表识别与表注释取用）不猜测、不越权。

验证行为：SQL 中的物理表名可从 CTE/文本里正确还原；表描述尊重数据源授权范围并只取真实注释。
隔离方式：纯函数 + SimpleNamespace 桩，不建数据库、不连库、不调用模型。
边界：CTE 名称与字符串字面量不得被当成物理表；超出授权范围的表不返回描述。
"""
from types import SimpleNamespace

import pytest

from app.modules.workflows.dataset_provenance import describe_tables, referenced_tables


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM sales s JOIN devices d ON s.id=d.id", ["devices", "sales"]),
    ("WITH records AS (SELECT * FROM sales) SELECT * FROM records", ["sales"]),
    ("SELECT * FROM (SELECT * FROM sales) t UNION ALL SELECT * FROM sales", ["sales"]),
    ("WITH sales AS (SELECT * FROM sales) SELECT * FROM sales", ["sales"]),
    ("SELECT 'FROM fake_table' AS note FROM `real_table` -- JOIN another_fake", ["real_table"]),
    ("SELECT * FROM", []), ("SELECT 1; SELECT 2", []),
])
def test_describe_physical_tables_without_guessing_ctes_or_text(sql, expected):
    assert referenced_tables(sql, "mysql") == expected


def test_source_table_description_respects_scope_and_uses_real_comments():
    source = SimpleNamespace(provider="mysql", connection_config={"allowedTables": ["sales"]})
    labels = {"sales": {"comment": "销售记录"}}
    assert describe_tables("SELECT * FROM sales JOIN private_table ON 1=1", source, labels) == [
        {"name": "sales", "label": "销售记录"},
    ]
