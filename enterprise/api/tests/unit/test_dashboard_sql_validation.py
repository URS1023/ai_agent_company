import json

import pytest
from test_dashboard_sql_generation import setup

from enterprise_platform.application.dashboard_sql_generation import (
    SqlSchemaColumn,
    SqlSchemaTable,
    validate_proposal_sql,
)
from enterprise_platform.application.dashboard_sql_proposals import SqlProposals
from enterprise_platform.application.errors import InvalidInput


def validate(sql, field="count", dialect="postgresql", tables=None):
    _, _, schemas, _, _ = setup()
    schema = schemas.resolve.return_value.model_copy(update={"dialect": dialect})
    if tables is not None:
        schema = schema.model_copy(
            update={
                "tables": tuple(
                    SqlSchemaTable(
                        name=name, columns=tuple(SqlSchemaColumn(name=column, sql_type="integer") for column in columns)
                    )
                    for name, columns in tables.items()
                )
            }
        )
    proposals = SqlProposals.model_validate_json(
        json.dumps(
            {
                "slots": [
                    {
                        "slot_id": "a",
                        "sql": sql,
                        "field_map": {"value": field},
                        "metric_definition": "Inspection count",
                        "time_definition": "All time",
                    }
                ]
            }
        )
    )
    validate_proposal_sql(proposals, schema)


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
@pytest.mark.parametrize(
    "sql,field",
    [
        ("SELECT count FROM measurements", "count"),
        ("SELECT SUM(count) AS total FROM measurements", "total"),
        ("SELECT COUNT(*) AS total FROM measurements", "total"),
        ("WITH totals AS (SELECT count AS n FROM measurements) SELECT n FROM totals", "n"),
        (
            "SELECT a.count FROM measurements a WHERE EXISTS (SELECT 1 FROM measurements b WHERE b.count = a.count)",
            "count",
        ),
    ],
)
def test_accepts_authorized_columns_aggregates_and_cte_output(sql, field, dialect):
    validate(sql, field, dialect)


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count FROM measurements",
        "SELECT count FROM public.measurements",
        "SELECT a.count FROM measurements a JOIN public.measurements b ON a.count = b.count",
        "SELECT public.measurements.count FROM public.measurements",
        "WITH c AS (SELECT count FROM measurements) "
        "SELECT c.count FROM c JOIN public.measurements b ON c.count = b.count",
    ],
)
def test_mixed_qualification_depth_keeps_all_explicit_granted_tables_usable(sql, dialect):
    validate(sql, dialect=dialect, tables={"measurements": ("count",), "public.measurements": ("count",)})


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT secret AS count FROM measurements",
        "SELECT a.secret AS count FROM measurements a JOIN public.measurements b ON a.count = b.secret",
    ],
)
def test_same_basename_tables_do_not_share_column_grants(sql):
    with pytest.raises(InvalidInput):
        validate(sql, tables={"measurements": ("count",), "public.measurements": ("secret",)})


def test_qualified_table_retains_its_distinct_permitted_columns():
    validate(
        "SELECT secret AS count FROM public.measurements",
        tables={"measurements": ("count",), "public.measurements": ("secret",)},
    )


def test_quoted_column_case_is_preserved_not_widened():
    validate('SELECT "Count" AS count FROM measurements', tables={"measurements": ("Count",)})
    with pytest.raises(InvalidInput):
        validate("SELECT Count AS count FROM measurements", tables={"measurements": ("Count",)})


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM measurements",
        "SELECT count FROM measurements; SELECT count FROM measurements",
        "SELECT secret FROM measurements",
        "SELECT count FROM measurements WHERE secret = 1",
        "SELECT count FROM measurements ORDER BY secret",
        "SELECT SUM(secret) AS count FROM measurements",
        "SELECT * FROM measurements",
        "SELECT m.* FROM measurements m",
        "SELECT COUNT(m.*) AS count FROM measurements m",
        "SELECT count FROM forbidden",
        "SELECT count FROM measurements a JOIN measurements b ON a.count = b.count",
        "SELECT a.count FROM measurements a NATURAL JOIN measurements b",
        "WITH hidden AS (SELECT secret AS count FROM measurements) SELECT count FROM hidden",
        "SELECT pg_sleep(10) AS count FROM measurements",
        "SELECT a.count FROM measurements a WHERE EXISTS (SELECT 1 FROM measurements b WHERE b.secret = a.count)",
        "SELECT count INTO other FROM measurements",
        "SELECT count FROM measurements WHERE count = :unbound",
    ],
)
def test_rejects_writes_hidden_columns_ambiguous_sources_and_undeclared_parameters(sql):
    with pytest.raises(InvalidInput, match="dashboard_sql_validation_failed"):
        validate(sql)


@pytest.mark.parametrize(
    "sql,field",
    [
        ("SELECT count FROM measurements", "missing"),
        ("SELECT count AS value, count AS value FROM measurements", "value"),
        ("SELECT count + 1 FROM measurements", "_col_0"),
    ],
)
def test_field_mapping_requires_unique_real_projection_alias(sql, field):
    with pytest.raises(InvalidInput, match="dashboard_sql_validation_failed"):
        validate(sql, field)
