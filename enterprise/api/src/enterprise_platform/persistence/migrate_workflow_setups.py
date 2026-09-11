"""Explicit guarded additive 0003. No native app changes or automatic migrations."""

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import Never, cast

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, PrimaryKeyConstraint

from enterprise_platform.application.errors import EnterpriseError, InvalidInput, PersistenceError

from .migrate import read_initial_sql, validate_target
from .migrate_sources import read_sources_sql, require_schema
from .models import Base
from .source_models import SourceBase
from .workflow_setup_models import SetupBase


def prerequisite_metadata() -> MetaData:
    metadata = MetaData()
    for source in (Base.metadata, SourceBase.metadata):
        for table in source.sorted_tables:
            table.to_metadata(metadata)
    return metadata


def require_setup_prerequisites(inspector: Inspector, *, schema: str = "public") -> None:
    require_schema(inspector, prerequisite_metadata(), schema=schema)


def workflow_setup_statements() -> tuple[str, ...]:
    metadata = MetaData()
    for table in SetupBase.metadata.sorted_tables:
        table.to_metadata(metadata, schema="public")
    dialect = cast(Callable[[], Dialect], postgresql.dialect)()
    creations: list[str] = []
    alterations: list[str] = []
    indexes: list[str] = []
    for table in metadata.sorted_tables:
        constraints = sorted(
            (c for c in table.constraints if not isinstance(c, PrimaryKeyConstraint)), key=lambda c: str(c.name or "")
        )
        table.constraints.difference_update(constraints)
        creations.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        alterations.extend(str(AddConstraint(c).compile(dialect=dialect)).strip() for c in constraints)
        indexes.extend(
            str(CreateIndex(i).compile(dialect=dialect)).strip()
            for i in sorted(table.indexes, key=lambda i: i.name or "")
        )
    return tuple(creations + alterations + indexes)


def render_workflow_setups_sql() -> str:
    return (
        "-- Enterprise migration 0003: durable draft-only workflow imports.\n"
        "-- Apply only after verified 0001 and 0002 in the dedicated enterprise_* database.\n\n"
        "BEGIN;\nSET LOCAL search_path TO public;\n\n" + ";\n\n".join(workflow_setup_statements()) + ";\n\nCOMMIT;\n"
    )


def read_workflow_setups_sql() -> str:
    artifact = (
        files("enterprise_platform.persistence")
        .joinpath("migrations/0003_workflow_setups.sql")
        .read_text(encoding="utf-8")
    )
    if artifact != render_workflow_setups_sql():
        raise InvalidInput("migration_artifact_mismatch")
    return artifact


def apply_workflow_setups(database_url: str, expected_database: str) -> None:
    url = validate_target(database_url, expected_database)
    read_initial_sql()
    read_sources_sql()
    read_workflow_setups_sql()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_database:
                raise InvalidInput("migration_actual_database_mismatch")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            connection.execute(text("SELECT pg_advisory_xact_lock(1162761296)"))
            require_setup_prerequisites(inspect(connection))
            for statement in workflow_setup_statements():
                connection.exec_driver_sql(statement)
    except SQLAlchemyError:
        raise PersistenceError("enterprise_workflow_setup_migration_failed") from None
    finally:
        engine.dispose()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise InvalidInput("invalid_migration_arguments")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description="Explicit enterprise migration 0003 after verified 0001 and 0002")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-sql", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--url-env", default="ENTERPRISE_DATABASE_URL")
    parser.add_argument("--expected-database")
    try:
        args = parser.parse_args(argv)
        if args.print_sql:
            sys.stdout.write(read_workflow_setups_sql())
            return 0
        if not re.fullmatch(r"ENTERPRISE_[A-Z0-9_]+", args.url_env) or not args.expected_database:
            raise InvalidInput("explicit_enterprise_target_required")
        url = os.environ.get(args.url_env)
        if not url:
            raise InvalidInput("migration_url_environment_missing")
        apply_workflow_setups(url, args.expected_database)
    except EnterpriseError as error:
        sys.stderr.write(error.code + "\n")
        return 1
    sys.stdout.write("Applied enterprise migration 0003.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
