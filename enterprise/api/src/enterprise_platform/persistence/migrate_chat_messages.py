"""Explicit additive 0012 after verified 0001-0011; never migrates at startup."""

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
from sqlalchemy.schema import AddConstraint, CreateTable, PrimaryKeyConstraint

from enterprise_platform.application.errors import EnterpriseError, InvalidInput, PersistenceError

from . import migrate_sql_trials as previous
from .dashboard_sql_trial_evidence import SqlTrialEvidenceBase
from .migrate import read_initial_sql, validate_target
from .migrate_dashboards import read_dashboards_sql
from .migrate_schedules import read_schedules_sql
from .migrate_sources import read_sources_sql, require_schema
from .migrate_sql_drafts import read_sql_drafts_sql
from .migrate_workflow_activation import read_workflow_activation_sql
from .migrate_workflow_credentials import read_workflow_credentials_sql
from .migrate_workflow_enrollment import read_workflow_enrollment_sql
from .migrate_workflow_provisioning import read_workflow_provisioning_sql
from .migrate_workflow_setups import read_workflow_setups_sql
from .workbench_messages import MessageIntentBase


def prerequisite_metadata() -> MetaData:
    metadata = previous.prerequisite_metadata()
    for table in SqlTrialEvidenceBase.metadata.sorted_tables:
        table.to_metadata(metadata)
    return metadata


def require_chat_prerequisites(inspector: Inspector, *, schema: str = "public") -> None:
    require_schema(inspector, prerequisite_metadata(), schema=schema)


def chat_messages_statements() -> tuple[str, ...]:
    metadata = MetaData()
    for table in MessageIntentBase.metadata.sorted_tables:
        table.to_metadata(metadata, schema="public")
    dialect = cast(Callable[[], Dialect], postgresql.dialect)()
    statements: list[str] = []
    for table in metadata.sorted_tables:
        constraints = sorted(
            (item for item in table.constraints if not isinstance(item, PrimaryKeyConstraint)),
            key=lambda item: str(item.name or ""),
        )
        table.constraints.difference_update(constraints)
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        statements.extend(str(AddConstraint(item).compile(dialect=dialect)).strip() for item in constraints)
    return tuple(statements)


def render_chat_messages_sql() -> str:
    return (
        "-- Enterprise migration 0012: scoped chat send intent ledger.\n"
        "-- Apply only after verified 0001-0011 in the dedicated enterprise_* database.\n\n"
        "BEGIN;\nSET LOCAL search_path TO public;\n\n" + ";\n\n".join(chat_messages_statements()) + ";\n\nCOMMIT;\n"
    )


def read_chat_messages_sql() -> str:
    artifact = (
        files("enterprise_platform.persistence")
        .joinpath("migrations/0012_chat_messages.sql")
        .read_text(encoding="utf-8")
    )
    if artifact != render_chat_messages_sql():
        raise InvalidInput("migration_artifact_mismatch")
    return artifact


def apply_chat_messages(database_url: str, expected_database: str) -> None:
    url = validate_target(database_url, expected_database)
    for read_artifact in (
        read_initial_sql,
        read_sources_sql,
        read_workflow_setups_sql,
        read_workflow_credentials_sql,
        read_workflow_provisioning_sql,
        read_workflow_enrollment_sql,
        read_workflow_activation_sql,
        read_schedules_sql,
        read_dashboards_sql,
        read_sql_drafts_sql,
        previous.read_sql_trials_sql,
        read_chat_messages_sql,
    ):
        read_artifact()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_database:
                raise InvalidInput("migration_actual_database_mismatch")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            connection.execute(text("SELECT pg_advisory_xact_lock(1162761296)"))
            require_chat_prerequisites(inspect(connection))
            for statement in chat_messages_statements():
                connection.exec_driver_sql(statement)
    except SQLAlchemyError:
        raise PersistenceError("enterprise_chat_migration_failed") from None
    finally:
        engine.dispose()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise InvalidInput("invalid_migration_arguments")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description="Explicit enterprise migration 0012 after verified 0001-0011")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-sql", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--url-env", default="ENTERPRISE_DATABASE_URL")
    parser.add_argument("--expected-database")
    try:
        args = parser.parse_args(argv)
        if args.print_sql:
            sys.stdout.write(read_chat_messages_sql())
            return 0
        if not re.fullmatch(r"ENTERPRISE_[A-Z0-9_]+", args.url_env) or not args.expected_database:
            raise InvalidInput("explicit_enterprise_target_required")
        url = os.environ.get(args.url_env)
        if not url:
            raise InvalidInput("migration_url_environment_missing")
        apply_chat_messages(url, args.expected_database)
    except EnterpriseError as error:
        sys.stderr.write(error.code + "\n")
        return 1
    sys.stdout.write("Applied enterprise migration 0012.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
