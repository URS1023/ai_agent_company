"""Explicit guarded additive 0010. No native app changes or automatic migrations."""

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

from .dashboard_models import DashboardBase
from .dashboard_sql_drafts import SqlDraftBase
from .migrate import read_initial_sql, validate_target
from .migrate_dashboards import read_dashboards_sql
from .migrate_schedules import read_schedules_sql
from .migrate_sources import read_sources_sql, require_schema
from .migrate_workflow_activation import read_workflow_activation_sql
from .migrate_workflow_credentials import read_workflow_credentials_sql
from .migrate_workflow_enrollment import read_workflow_enrollment_sql
from .migrate_workflow_provisioning import read_workflow_provisioning_sql
from .migrate_workflow_setups import read_workflow_setups_sql
from .models import Base
from .schedule_models import ScheduleBase
from .source_models import SourceBase
from .workflow_activation_models import ActivationBase
from .workflow_credential_models import CredentialBase
from .workflow_enrollment_models import EnrollmentBase
from .workflow_provisioning_models import ProvisioningBase
from .workflow_setup_models import SetupBase


def prerequisite_metadata() -> MetaData:
    metadata = MetaData()
    for source in (
        Base.metadata,
        SourceBase.metadata,
        SetupBase.metadata,
        CredentialBase.metadata,
        ProvisioningBase.metadata,
        EnrollmentBase.metadata,
        ActivationBase.metadata,
        ScheduleBase.metadata,
        DashboardBase.metadata,
    ):
        for table in source.sorted_tables:
            table.to_metadata(metadata)
    return metadata


def require_sql_draft_prerequisites(inspector: Inspector, *, schema: str = "public") -> None:
    require_schema(inspector, prerequisite_metadata(), schema=schema)


def sql_drafts_statements() -> tuple[str, ...]:
    metadata = MetaData()
    for table in SqlDraftBase.metadata.sorted_tables:
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


def render_sql_drafts_sql() -> str:
    return (
        "-- Enterprise migration 0010: version-pinned SQL draft snapshots for review.\n"
        "-- Apply only after verified 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008 and 0009 "
        "in the dedicated enterprise_* database.\n\n"
        "BEGIN;\nSET LOCAL search_path TO public;\n\n" + ";\n\n".join(sql_drafts_statements()) + ";\n\nCOMMIT;\n"
    )


def read_sql_drafts_sql() -> str:
    artifact = (
        files("enterprise_platform.persistence").joinpath("migrations/0010_sql_drafts.sql").read_text(encoding="utf-8")
    )
    if artifact != render_sql_drafts_sql():
        raise InvalidInput("migration_artifact_mismatch")
    return artifact


def apply_sql_drafts(database_url: str, expected_database: str) -> None:
    url = validate_target(database_url, expected_database)
    read_initial_sql()
    read_sources_sql()
    read_workflow_setups_sql()
    read_workflow_credentials_sql()
    read_workflow_provisioning_sql()
    read_workflow_enrollment_sql()
    read_workflow_activation_sql()
    read_schedules_sql()
    read_dashboards_sql()
    read_sql_drafts_sql()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_database:
                raise InvalidInput("migration_actual_database_mismatch")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            connection.execute(text("SELECT pg_advisory_xact_lock(1162761296)"))
            require_sql_draft_prerequisites(inspect(connection))
            for statement in sql_drafts_statements():
                connection.exec_driver_sql(statement)
    except SQLAlchemyError:
        raise PersistenceError("enterprise_sql_drafts_migration_failed") from None
    finally:
        engine.dispose()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise InvalidInput("invalid_migration_arguments")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description="Explicit enterprise migration 0010 after verified migrations 0001 through 0009")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-sql", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--url-env", default="ENTERPRISE_DATABASE_URL")
    parser.add_argument("--expected-database")
    try:
        args = parser.parse_args(argv)
        if args.print_sql:
            sys.stdout.write(read_sql_drafts_sql())
            return 0
        if not re.fullmatch(r"ENTERPRISE_[A-Z0-9_]+", args.url_env) or not args.expected_database:
            raise InvalidInput("explicit_enterprise_target_required")
        url = os.environ.get(args.url_env)
        if not url:
            raise InvalidInput("migration_url_environment_missing")
        apply_sql_drafts(url, args.expected_database)
    except EnterpriseError as error:
        sys.stderr.write(error.code + "\n")
        return 1
    sys.stdout.write("Applied enterprise migration 0010.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
