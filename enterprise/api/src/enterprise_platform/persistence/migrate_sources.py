"""Explicit additive migration 0002; run after 0001, never during application startup.

Existing 0001 columns and constraints are verified before new DDL. Re-application,
partial upgrades, extra user objects and divergent schemas require operator review;
this command does not repair or drop them. All DDL shares the original migration lock.
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import Never, cast

import sqlglot
from sqlalchemy import CheckConstraint, MetaData, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import AddConstraint, CreateTable, PrimaryKeyConstraint
from sqlglot import exp

from enterprise_platform.application.errors import Conflict, EnterpriseError, InvalidInput, PersistenceError

from .migrate import read_initial_sql, validate_target
from .models import Base
from .source_models import SourceBase


def source_statements() -> tuple[str, ...]:
    metadata = MetaData()
    for table in SourceBase.metadata.sorted_tables:
        table.to_metadata(metadata, schema="public")
    dialect = cast(Callable[[], Dialect], postgresql.dialect)()
    creations: list[str] = []
    alterations: list[str] = []
    for table in list(metadata.sorted_tables):
        constraints = sorted(
            (c for c in table.constraints if not isinstance(c, PrimaryKeyConstraint)), key=lambda c: str(c.name or "")
        )
        table.constraints.difference_update(constraints)
        creations.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        alterations.extend(str(AddConstraint(c).compile(dialect=dialect)).strip() for c in constraints)
    return tuple(creations + alterations)


def render_sources_sql() -> str:
    return (
        "-- Enterprise migration 0002: encrypted source heads and immutable read revisions.\n"
        "-- Apply only after verified 0001 in the dedicated enterprise_* database.\n\n"
        "BEGIN;\nSET LOCAL search_path TO public;\n\n" + ";\n\n".join(source_statements()) + ";\n\nCOMMIT;\n"
    )


def read_sources_sql() -> str:
    artifact = (
        files("enterprise_platform.persistence").joinpath("migrations/0002_sources.sql").read_text(encoding="utf-8")
    )
    if artifact != render_sources_sql():
        raise InvalidInput("migration_artifact_mismatch")
    return artifact


def normalized_check(sql: str) -> str:
    """Fingerprint the AST, preserving boolean grouping after reflection normalization.

    Rendering a tree after removing redundant Paren nodes can lose AND/OR precedence;
    the structural fingerprint retains parent/child edges and excludes source positions.
    """
    expression = sqlglot.parse_one(sql, read="postgres")

    def structure(node: exp.Expression) -> str:
        dump = cast(Callable[[], list[dict[str, object]]], node.dump)
        return json.dumps(
            [{key: value for key, value in item.items() if key != "m"} for item in dump()], sort_keys=True
        )

    def unwrap(node: exp.Expression) -> exp.Expression:
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, exp.Cast):
            value = node.this
            while isinstance(value, exp.Paren):
                value = value.this
            target = node.to
            text_type = target.this in {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR} and not target.expressions
            text_array = (
                target.this == exp.DataType.Type.ARRAY
                and len(target.expressions) == 1
                and (
                    isinstance(target.expressions[0], exp.DataType)
                    and target.expressions[0].this == exp.DataType.Type.TEXT
                    and not target.expressions[0].expressions
                )
            )
            if (
                text_type
                and (
                    isinstance(value, exp.Column)
                    and value.name == "state"
                    or isinstance(value, exp.Literal)
                    and value.is_string
                )
                or text_array
                and isinstance(value, exp.Array)
            ):
                return value
        return node

    for _ in range(64):
        before = structure(expression)
        expression = expression.transform(unwrap)
        if structure(expression) == before:
            break

    # PostgreSQL resolves VARCHAR length arguments to TEXT and expands BETWEEN.
    # Limit this equivalence to the credential table's known text-length checks;
    # bounded/lossy casts, other functions and different operands stay structural.
    credential_text_columns = {"key_id", "nonce", "ciphertext"}

    def credential_length(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Length) and type(node.this) is exp.Cast:
            operand = node.this
            if (
                operand.to.this == exp.DataType.Type.TEXT
                and not operand.to.expressions
                and isinstance(operand.this, exp.Column)
                and operand.this.name in credential_text_columns
            ):
                result = node.copy()
                result.set("this", operand.this.copy())
                return result
        return node

    expression = expression.transform(credential_length)

    def credential_range(node: exp.Expression) -> exp.Expression:
        if (
            isinstance(node, exp.Between)
            and not node.args.get("symmetric")
            and isinstance(node.this, exp.Length)
            and isinstance(node.this.this, exp.Column)
            and node.this.this.name in credential_text_columns
        ):
            return exp.And(
                this=exp.GTE(this=node.this.copy(), expression=node.args["low"].copy()),
                expression=exp.LTE(this=node.this.copy(), expression=node.args["high"].copy()),
            )
        return node

    expression = expression.transform(credential_range)

    def membership(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.EQ) and isinstance(node.expression, exp.Any):
            array = unwrap(node.expression.this)
            if isinstance(array, exp.Array):
                return exp.In(this=node.this.copy(), expressions=[item.copy() for item in array.expressions])
        if (
            isinstance(node, exp.NEQ)
            and isinstance(node.expression, exp.Anonymous)
            and isinstance(node.expression.this, str)
            and node.expression.this.upper() == "ALL"
            and len(node.expression.expressions) == 1
        ):
            array = unwrap(node.expression.expressions[0])
            if isinstance(array, exp.Array):
                return exp.Not(
                    this=exp.In(this=node.this.copy(), expressions=[item.copy() for item in array.expressions])
                )
        return node

    return structure(expression.transform(membership))


def require_schema(inspector: Inspector, expected_metadata: MetaData, *, schema: str = "public") -> None:
    """Pure inspector boundary; tests may supply reflection without opening a DB."""
    schemas = inspector.get_schema_names()
    if schema not in schemas:
        raise Conflict("source_migration_initial_schema_required")
    existing: set[str] = set()
    for item in schemas:
        if item == "information_schema" or item.startswith("pg_"):
            continue
        names = (
            inspector.get_table_names(schema=item)
            + inspector.get_view_names(schema=item)
            + inspector.get_materialized_view_names(schema=item)
        )
        existing.update(f"{item}.{name}" for name in names)
        if item != schema and names:
            raise Conflict("source_migration_unexpected_objects")
    expected = {f"{schema}.{name}" for name in expected_metadata.tables}
    if (
        existing != expected
        or inspector.get_view_names(schema=schema)
        or inspector.get_materialized_view_names(schema=schema)
    ):
        raise Conflict("source_migration_initial_schema_required")
    dialect = cast(Callable[[], Dialect], postgresql.dialect)()
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    for table in expected_metadata.sorted_tables:
        name = table.name
        columns = inspector.get_columns(name, schema=schema)
        actual = {(c["name"], str(c["type"].compile(dialect=dialect)), c["nullable"]) for c in columns}
        expected_columns = {(c.name, str(c.type.compile(dialect=dialect)), c.nullable) for c in table.c}
        valid = actual == expected_columns
        for column in columns:
            default = column.get("default")
            if column.get("computed") or column.get("identity"):
                valid = False
            if column["name"] == "sequence":
                valid &= bool(
                    re.fullmatch(
                        rf"nextval\('(?:(?:{re.escape(schema)}|{re.escape(quoted_schema)})\.)?"
                        rf"{re.escape(name)}_sequence_seq'::regclass\)",
                        str(default),
                    )
                )
            elif default is not None:
                valid = False
        valid &= inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") == list(
            table.primary_key.columns.keys()
        )
        actual_fks = {
            (
                f.get("name"),
                tuple(f["constrained_columns"]),
                f["referred_schema"] or schema,
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in inspector.get_foreign_keys(name, schema=schema)
        }
        expected_fks = {
            (f.name, tuple(f.column_keys), schema, f.referred_table.name, tuple(e.column.name for e in f.elements))
            for f in table.foreign_key_constraints
        }
        valid &= actual_fks == expected_fks
        valid &= all(not any(f.get("options", {}).values()) for f in inspector.get_foreign_keys(name, schema=schema))
        valid &= all(
            not any(c.get("dialect_options", {}).values())
            for c in inspector.get_unique_constraints(name, schema=schema)
        )
        valid &= {
            (c.get("name"), tuple(c["column_names"])) for c in inspector.get_unique_constraints(name, schema=schema)
        } == {(c.name, tuple(c.columns.keys())) for c in table.constraints if isinstance(c, UniqueConstraint)}
        valid &= {
            (i["name"], tuple(i["column_names"]), bool(i["unique"]))
            for i in inspector.get_indexes(name, schema=schema)
            if not i.get("duplicates_constraint")
        } == {(i.name, tuple(i.columns.keys()), bool(i.unique)) for i in table.indexes}
        for index in inspector.get_indexes(name, schema=schema):
            valid &= not index.get("include_columns") and not index.get("column_sorting")
            valid &= all(
                not value or (key == "postgresql_using" and value == "btree")
                for key, value in index.get("dialect_options", {}).items()
            )
        try:
            valid &= all(
                not any(c.get("dialect_options", {}).values())
                for c in inspector.get_check_constraints(name, schema=schema)
            )
            valid &= {
                (c.get("name"), normalized_check(c["sqltext"]))
                for c in inspector.get_check_constraints(name, schema=schema)
            } == {
                (c.name, normalized_check(str(c.sqltext))) for c in table.constraints if isinstance(c, CheckConstraint)
            }
        except (ValueError, sqlglot.errors.ParseError):
            valid = False
        if not valid:
            raise Conflict("source_migration_initial_shape_mismatch")


def require_initial_schema(inspector: Inspector, *, schema: str = "public") -> None:
    require_schema(inspector, Base.metadata, schema=schema)


def apply_sources(database_url: str, expected_database: str) -> None:
    url = validate_target(database_url, expected_database)
    read_initial_sql()
    read_sources_sql()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_database:
                raise InvalidInput("migration_actual_database_mismatch")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            connection.execute(text("SELECT pg_advisory_xact_lock(1162761296)"))
            require_initial_schema(inspect(connection))
            for statement in source_statements():
                connection.exec_driver_sql(statement)
    except SQLAlchemyError:
        raise PersistenceError("enterprise_source_migration_failed") from None
    finally:
        engine.dispose()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise InvalidInput("invalid_migration_arguments")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description="Explicit additive enterprise migration 0002, after 0001")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-sql", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--url-env", default="ENTERPRISE_DATABASE_URL")
    parser.add_argument("--expected-database")
    try:
        args = parser.parse_args(argv)
        if args.print_sql:
            sys.stdout.write(read_sources_sql())
            return 0
        if not re.fullmatch(r"ENTERPRISE_[A-Z0-9_]+", args.url_env) or not args.expected_database:
            raise InvalidInput("explicit_enterprise_target_required")
        url = os.environ.get(args.url_env)
        if not url:
            raise InvalidInput("migration_url_environment_missing")
        apply_sources(url, args.expected_database)
    except EnterpriseError as error:
        sys.stderr.write(error.code + "\n")
        return 1
    sys.stdout.write("Applied enterprise migration 0002.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
