"""Explicit, one-time enterprise schema migration; never called by application startup.

The operator must choose a dedicated enterprise_* PostgreSQL database by URL environment
variable and independently confirm its name. Only the fixed public schema is written.
Existing user tables/views, including renamed native Dify databases, reject migration.
Migration 0001 is not an upgrade or reset command: an existing enterprise schema rejects
re-application. No rollback here drops tables; a failed transaction rolls back its DDL.
"""

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import cast

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, PrimaryKeyConstraint

from enterprise_platform.application.errors import Conflict, EnterpriseError, InvalidInput, PersistenceError

from .models import Base


def initial_statements() -> tuple[str, ...]:
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata, schema="public")
    dialect = cast(Callable[[], Dialect], postgresql.dialect)()
    creations: list[str] = []
    alterations: list[str] = []
    indexes: list[str] = []
    for table in list(metadata.sorted_tables):
        # SQLAlchemy stores constraints in a set; sort named ALTERs for stable artifacts.
        constraints = sorted(
            (item for item in table.constraints if not isinstance(item, PrimaryKeyConstraint)),
            key=lambda item: str(item.name or ""),
        )
        table.constraints.difference_update(constraints)
        creations.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        alterations.extend(str(AddConstraint(item).compile(dialect=dialect)).strip() for item in constraints)
        indexes.extend(
            str(CreateIndex(index).compile(dialect=dialect)).strip()
            for index in sorted(table.indexes, key=lambda index: index.name or "")
        )
    return tuple(creations + alterations + indexes)


def render_initial_sql() -> str:
    header = (
        "-- Enterprise migration 0001: four independent business tables.\n"
        "-- Dedicated empty enterprise_* database only.\n"
    )
    return (
        header
        + "\nBEGIN;\nSET LOCAL search_path TO public;\n\n"
        + ";\n\n".join(initial_statements())
        + ";\n\nCOMMIT;\n"
    )


def read_initial_sql() -> str:
    artifact = (
        files("enterprise_platform.persistence").joinpath("migrations/0001_initial.sql").read_text(encoding="utf-8")
    )
    if artifact != render_initial_sql():
        raise InvalidInput("migration_artifact_mismatch")
    return artifact


def validate_target(database_url: str, expected_database: str) -> URL:
    if not re.fullmatch(r"enterprise_[a-z0-9_]{1,48}", expected_database):
        raise InvalidInput("dedicated_enterprise_database_required")
    try:
        url = make_url(database_url)
        if (
            url.drivername != "postgresql+psycopg"
            or url.database != expected_database
            or not url.host
            or not url.username
            or set(url.query) - {"sslmode", "sslrootcert", "sslcert", "sslkey"}
        ):
            raise InvalidInput("migration_target_mismatch")
        return url
    except (ValueError, SQLAlchemyError):
        raise InvalidInput("invalid_migration_target") from None


def require_empty_database(tables: set[str]) -> None:
    if tables:
        raise Conflict("initial_migration_requires_empty_database")


def apply_initial(database_url: str, expected_database: str) -> None:
    """Perform guarded transactional DDL only when explicitly invoked by an operator."""
    url = validate_target(database_url, expected_database)
    read_initial_sql()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_database:
                raise InvalidInput("migration_actual_database_mismatch")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            connection.execute(text("SELECT pg_advisory_xact_lock(1162761296)"))
            inspector = inspect(connection)
            schemas = inspector.get_schema_names()
            if "public" not in schemas:
                raise InvalidInput("public_schema_required")
            existing: set[str] = set()
            for schema in schemas:
                if schema == "information_schema" or schema.startswith("pg_"):
                    continue
                names = inspector.get_table_names(schema=schema) + inspector.get_view_names(schema=schema)
                names += inspector.get_materialized_view_names(schema=schema)
                existing.update(f"{schema}.{name}" for name in names)
            require_empty_database(existing)
            for statement in initial_statements():
                connection.exec_driver_sql(statement)
    except SQLAlchemyError:
        raise PersistenceError("enterprise_migration_failed") from None
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit enterprise migration 0001; no application startup migration")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print-sql", action="store_true", help="Inspect packaged SQL without connecting to a database")
    mode.add_argument("--apply", action="store_true", help="Create the four tables in an empty dedicated database")
    parser.add_argument("--url-env", default="ENTERPRISE_DATABASE_URL", help="Enterprise URL environment variable name")
    parser.add_argument(
        "--expected-database", help="Independent confirmation of the dedicated enterprise_* database name"
    )
    args = parser.parse_args(argv)
    try:
        if args.print_sql:
            sys.stdout.write(read_initial_sql())
            return 0
        if not re.fullmatch(r"ENTERPRISE_[A-Z0-9_]+", args.url_env) or not args.expected_database:
            raise InvalidInput("explicit_enterprise_target_required")
        database_url = os.environ.get(args.url_env)
        if not database_url:
            raise InvalidInput("migration_url_environment_missing")
        apply_initial(database_url, args.expected_database)
    except EnterpriseError as error:
        sys.stderr.write(f"{error.code}: {error}\n")
        return 1
    sys.stdout.write("Applied enterprise migration 0001: 4 tables in the public schema.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
