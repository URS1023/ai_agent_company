"""核对完整回执唯一索引的真实方言反射形状；不连接业务数据库。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import String

from app import schema


def reflected_index(monkeypatch, extra):
    """只注入目录反射结果，实际启动校验函数保持产品实现。"""
    index = {"name": schema.RECEIPT_INDEX, "column_names": ["receipt_key"], "unique": True, **extra}
    inspector = SimpleNamespace(
        get_columns=lambda table: [{"name": "receipt_key", "type": String(64), "nullable": True}],
        get_indexes=lambda table: [deepcopy(index)],
    )
    monkeypatch.setattr(schema, "inspect", lambda connection: inspector)
    schema.validate_receipt_schema(object())


@pytest.mark.parametrize("extra", [
    {"dialect_options": {"postgresql_where": "receipt_key IS NOT NULL"}},
    {"dialect_options": {"mysql_length": {"receipt_key": 16}}},
    {"expressions": ["lower(receipt_key)"]},
    {"include_columns": ["run_id"], "dialect_options": {"postgresql_include": ["run_id"]}},
    {"dialect_options": {"unrecognized_option": {}}},
    {"dialect_options": {"unrecognized_option": False}},
], ids=["partial", "prefix", "expression", "nonempty-include", "unknown-empty", "unknown-false"])
def test_noncanonical_receipt_indexes_are_rejected(monkeypatch, extra):
    """局部、前缀、表达式与未知选项不能因广泛忽略空值而进入可信回执链。"""
    with pytest.raises(RuntimeError, match="唯一索引不匹配"):
        reflected_index(monkeypatch, extra)


def test_mysql_full_unique_receipt_index_is_accepted(monkeypatch):
    """普通MySQL全长唯一索引的反射形状可通过同一启动校验。"""
    reflected_index(monkeypatch, {"type": "UNIQUE"})


def test_postgres_empty_include_receipt_index_is_accepted(monkeypatch):
    """SQLAlchemy对PG 11+普通索引反射空INCLUDE，它不会改变单列唯一性。"""
    reflected_index(monkeypatch, {"include_columns": [], "dialect_options": {"postgresql_include": []}})
