from unittest.mock import Mock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from enterprise_platform.application.errors import Conflict
from enterprise_platform.persistence.migrate_sources import (
    normalized_check,
    read_sources_sql,
    render_sources_sql,
    require_initial_schema,
)
from enterprise_platform.persistence.models import Base


def inspector():
    result = Mock()
    result.get_schema_names.return_value = ["public", "pg_catalog", "information_schema"]
    result.get_table_names.return_value = list(Base.metadata.tables)
    result.get_view_names.return_value = []
    result.get_materialized_view_names.return_value = []
    result.get_columns.side_effect = lambda name, **kw: [
        {
            "name": c.name,
            "type": c.type,
            "nullable": c.nullable,
            "default": f"nextval('{name}_sequence_seq'::regclass)" if c.name == "sequence" else None,
        }
        for c in Base.metadata.tables[name].c
    ]
    result.get_pk_constraint.side_effect = lambda name, **kw: {
        "constrained_columns": list(Base.metadata.tables[name].primary_key.columns.keys())
    }
    result.get_foreign_keys.side_effect = lambda name, **kw: [
        {
            "name": f.name,
            "constrained_columns": f.column_keys,
            "referred_schema": "public",
            "referred_table": f.referred_table.name,
            "referred_columns": [e.column.name for e in f.elements],
        }
        for f in Base.metadata.tables[name].foreign_key_constraints
    ]
    result.get_unique_constraints.side_effect = lambda name, **kw: [
        {"name": c.name, "column_names": list(c.columns.keys())}
        for c in Base.metadata.tables[name].constraints
        if isinstance(c, UniqueConstraint)
    ]
    result.get_indexes.side_effect = lambda name, **kw: [
        {"name": i.name, "column_names": list(i.columns.keys()), "unique": bool(i.unique)}
        for i in Base.metadata.tables[name].indexes
    ]
    result.get_check_constraints.side_effect = lambda name, **kw: [
        {"name": c.name, "sqltext": str(c.sqltext)}
        for c in Base.metadata.tables[name].constraints
        if isinstance(c, CheckConstraint)
    ]
    return result


def test_additive_artifact_is_stable_and_initial_schema_is_not_rewritten() -> None:
    sql = read_sources_sql()
    assert sql == render_sources_sql() == render_sources_sql()
    assert sql.count("CREATE TABLE public.enterprise_source_") == 2
    assert "DROP " not in sql and "IF NOT EXISTS" not in sql
    require_initial_schema(inspector())


@pytest.mark.parametrize("prefix", ["", "public.", '"public".'])
def test_sequence_reflection_accepts_only_equivalent_public_qualification(prefix: str) -> None:
    fake = inspector()
    fake.get_columns.side_effect = lambda name, **kw: [
        {
            "name": column.name,
            "type": column.type,
            "nullable": column.nullable,
            "default": f"nextval('{prefix}{name}_sequence_seq'::regclass)" if column.name == "sequence" else None,
        }
        for column in Base.metadata.tables[name].c
    ]
    require_initial_schema(fake)


@pytest.mark.parametrize("prefix", ["other.", '"other".', '"public.other".', '"Public".', "public.other_"])
def test_sequence_reflection_rejects_other_schema_or_sequence(prefix: str) -> None:
    fake = inspector()
    fake.get_columns.side_effect = lambda name, **kw: [
        {
            "name": column.name,
            "type": column.type,
            "nullable": column.nullable,
            "default": f"nextval('{prefix}{name}_sequence_seq'::regclass)" if column.name == "sequence" else None,
        }
        for column in Base.metadata.tables[name].c
    ]
    with pytest.raises(Conflict):
        require_initial_schema(fake)


@pytest.mark.parametrize("extra", ["enterprise_source_heads", "apps", "enterprise_source_versions"])
def test_upgrade_rejects_existing_half_or_complete_or_foreign_schema(extra) -> None:
    fake = inspector()
    fake.get_table_names.return_value += [extra]
    with pytest.raises(Conflict):
        require_initial_schema(fake)


@pytest.mark.parametrize(
    "method",
    [
        "get_columns",
        "get_pk_constraint",
        "get_foreign_keys",
        "get_unique_constraints",
        "get_indexes",
        "get_check_constraints",
    ],
)
def test_initial_table_names_are_not_enough_to_accept_wrong_shape(method) -> None:
    fake = inspector()
    getattr(fake, method).side_effect = None
    getattr(fake, method).return_value = {} if method == "get_pk_constraint" else []
    with pytest.raises(Conflict):
        require_initial_schema(fake)


def test_extra_views_and_user_schemas_are_rejected() -> None:
    fake = inspector()
    fake.get_view_names.return_value = ["hidden"]
    with pytest.raises(Conflict):
        require_initial_schema(fake)
    fake = inspector()
    fake.get_schema_names.return_value += ["other"]
    with pytest.raises(Conflict):
        require_initial_schema(fake)


def test_normalizes_postgres_reflection_casts_without_ignoring_changed_policy() -> None:
    expected = "state IN ('queued', 'claimed', 'dispatched', 'uncertain', 'succeeded', 'failed', 'cancelled')"
    reflected = (
        "((state)::text = ANY ((ARRAY['queued'::character varying, 'claimed'::character varying, "
        "'dispatched'::character varying, 'uncertain'::character varying, 'succeeded'::character varying, "
        "'failed'::character varying, 'cancelled'::character varying])::text[]))"
    )
    assert normalized_check(expected) == normalized_check(reflected)
    assert normalized_check("revision > 0") == normalized_check("(revision > 0)")
    assert normalized_check(expected) != normalized_check(expected.replace("failed", "bad"))


@pytest.mark.parametrize("changed", ["CAST(revision AS SMALLINT) > 0", "CAST(revision AS TEXT)::INTEGER > 0"])
def test_check_normalization_preserves_numeric_casts(changed: str) -> None:
    assert normalized_check("revision > 0") != normalized_check(changed)


def test_normalizes_postgres_negated_membership_without_changing_grouping() -> None:
    model = "state NOT IN ('queued', 'importing') OR (app_id IS NULL AND import_id IS NULL)"
    reflected = (
        "(state::text <> ALL (ARRAY['queued'::character varying, 'importing'::character varying]::text[])) "
        "OR app_id IS NULL AND import_id IS NULL"
    )
    assert normalized_check(model) == normalized_check(reflected)
    assert normalized_check(model) != normalized_check(reflected.replace("OR app_id", "AND app_id"))


@pytest.mark.parametrize("value", ["'queued'", "NULL", "'queued', NULL"])
def test_negated_membership_preserves_null_elements(value: str) -> None:
    assert normalized_check(f"state NOT IN ({value})") == normalized_check(f"state <> ALL (ARRAY[{value}])")


@pytest.mark.parametrize("operator", ["= ALL", "<> ANY", "> ALL", "< ALL"])
def test_negated_membership_does_not_accept_other_array_comparisons(operator: str) -> None:
    assert normalized_check("state NOT IN ('queued', 'importing')") != normalized_check(
        f"state {operator} (ARRAY['queued', 'importing'])"
    )


@pytest.mark.parametrize(
    "operand",
    [
        "\"ALL\"(ARRAY['queued'])",
        "other.ALL(ARRAY['queued'])",
        "ALL(ARRAY['queued'], ARRAY['importing'])",
        "ALL(SELECT state FROM other)",
    ],
)
def test_negated_membership_preserves_function_and_query_semantics(operand: str) -> None:
    assert normalized_check("state NOT IN ('queued')") != normalized_check(f"state <> {operand}")


def test_check_normalization_preserves_lossy_string_casts() -> None:
    assert normalized_check("state IN ('queued')") != normalized_check("CAST(state AS VARCHAR(1)) IN ('queued')")


@pytest.mark.parametrize("case", ["serial", "cascade", "partial_index", "computed"])
def test_migration_rejects_storage_semantic_drift(case: str) -> None:
    fake = inspector()
    if case in {"serial", "computed"}:
        original = fake.get_columns.side_effect

        def changed(name, **kwargs):
            columns = original(name, **kwargs)
            for column in columns:
                if case == "serial" and column["name"] == "sequence":
                    column["default"] = None
                if case == "computed":
                    column["computed"] = {"sqltext": "1"}
            return columns

        fake.get_columns.side_effect = changed
    elif case == "cascade":
        original = fake.get_foreign_keys.side_effect

        def changed(name, **kwargs):
            return [{**fk, "options": {"ondelete": "CASCADE"}} for fk in original(name, **kwargs)]

        fake.get_foreign_keys.side_effect = changed
    else:
        original = fake.get_indexes.side_effect

        def changed(name, **kwargs):
            return [
                {**index, "dialect_options": {"postgresql_where": "revision > 5"}} for index in original(name, **kwargs)
            ]

        fake.get_indexes.side_effect = changed
    with pytest.raises(Conflict):
        require_initial_schema(fake)


def test_schema_only_cli_does_not_read_environment_or_create_engine(monkeypatch, capsys) -> None:
    from enterprise_platform.persistence import migrate_sources

    monkeypatch.setattr(migrate_sources, "create_engine", Mock(side_effect=AssertionError("no engine")))
    monkeypatch.setattr(
        migrate_sources, "os", Mock(environ=Mock(get=Mock(side_effect=AssertionError("no environment"))))
    )
    assert migrate_sources.main(["--print-sql"]) == 0
    assert "CREATE TABLE public.enterprise_source_heads" in capsys.readouterr().out


def test_migration_cli_rejects_unknown_arguments_without_echoing_them(capsys) -> None:
    from enterprise_platform.persistence.migrate_sources import main

    assert main(["--wrong=fixture-private-token"]) == 1
    output = capsys.readouterr()
    assert output.err == "invalid_input\n" and not output.out


def test_check_normalization_preserves_boolean_grouping() -> None:
    original = "(input_json IS NULL AND input_digest IS NULL) OR (input_json IS NOT NULL AND input_digest IS NOT NULL)"
    changed = "input_json IS NULL AND (input_digest IS NULL OR input_json IS NOT NULL) AND input_digest IS NOT NULL"
    assert normalized_check(original) != normalized_check(changed)


@pytest.mark.parametrize(
    "model,reflected",
    [
        ("length(nonce) = 16", "(length((nonce)::text) = 16)"),
        ("length(key_id) BETWEEN 1 AND 64", "((length((key_id)::text) >= 1) AND (length((key_id)::text) <= 64))"),
        ("length(ciphertext) BETWEEN 24 AND 5484", "((length(ciphertext) >= 24) AND (length(ciphertext) <= 5484))"),
    ],
)
def test_postgres_credential_length_reflection_is_equivalent(model: str, reflected: str) -> None:
    assert normalized_check(model) == normalized_check(reflected)


@pytest.mark.parametrize(
    "changed",
    [
        "length((nonce)::text) = 15",
        "length((key_id)::text) = 16",
        "octet_length((nonce)::text) = 16",
        "length(CAST(nonce AS VARCHAR(8))) = 16",
        "length(CAST(nonce AS CHAR(36))) = 16",
        "length(CAST(revision AS TEXT)) = 16",
        "length(CAST(CAST(nonce AS VARCHAR(8)) AS TEXT)) = 16",
    ],
)
def test_credential_length_reflection_retains_changed_semantics(changed: str) -> None:
    assert normalized_check("length(nonce) = 16") != normalized_check(changed)


@pytest.mark.parametrize(
    "changed",
    [
        "length(key_id) >= 0 AND length(key_id) <= 64",
        "length(key_id) > 1 AND length(key_id) <= 64",
        "length(key_id) >= 1 OR length(key_id) <= 64",
        "length(key_id) >= 1 AND length(nonce) <= 64",
        "length(key_id) BETWEEN SYMMETRIC 1 AND 64",
    ],
)
def test_credential_between_normalization_retains_changed_policy(changed: str) -> None:
    assert normalized_check("length(key_id) BETWEEN 1 AND 64") != normalized_check(changed)
