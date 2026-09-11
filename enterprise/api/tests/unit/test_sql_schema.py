import pytest
from sqlglot import exp

from enterprise_platform.adapters.sql_schema import ExactSqlSchema


def test_schema_is_detached_and_returns_exact_table_metadata():
    columns = {"measurements": {"count": "INTEGER"}, "public.measurements": {"secret": "TEXT"}}
    schema = ExactSqlSchema(columns, dialect="postgres")
    columns["measurements"]["secret"] = "TEXT"
    assert schema.column_names("measurements") == ("count",)
    assert schema.column_names(exp.to_table("public.measurements")) == ("secret",)
    assert schema.get_column_type("measurements", "count").is_type(exp.DataType.Type.INT)
    assert not schema.empty
    assert schema.supported_table_args == ("this", "db", "catalog")


def test_unknown_tables_and_inferred_schema_changes_are_rejected():
    schema = ExactSqlSchema({"public.measurements": {"count": "INTEGER"}}, dialect="postgres")
    with pytest.raises(ValueError):
        schema.column_names("measurements")
    with pytest.raises(ValueError):
        schema.add_table("other", {"count": "INTEGER"})
