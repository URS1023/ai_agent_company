"""Current, explicitly granted schema metadata for model context, not SQL execution rights."""

import time
from typing import Protocol, Self

from pydantic import Field, StrictBool, ValidationError, model_validator

from enterprise_platform.adapters.data_sources import DatabaseSourceReader
from enterprise_platform.domain.data_sources import DatabaseSourceConfig, DataSourceError, SourceRef

from .contracts import Contract, Identifier, Principal, canonical_hash
from .dashboard_sql_generation import AuthorizedSqlSchema, SqlSchemaColumn, SqlSchemaTable
from .dashboard_sql_proposals import Name
from .errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput
from .input_capture import RegisteredReadRegistry
from .source_contracts import DbSourceView
from .source_ports import SourceRepository


class SchemaTableGrant(Contract):
    name: Name
    columns: tuple[Name, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_columns(self) -> Self:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Duplicate granted columns")
        return self


class DashboardSchemaGrant(Contract):
    source: SourceRef
    actor_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)
    tables: tuple[SchemaTableGrant, ...] = Field(min_length=1, max_length=100)
    enabled: StrictBool = True

    @model_validator(mode="after")
    def unique_grants(self) -> Self:
        if len(set(self.actor_ids)) != len(self.actor_ids) or len({t.name for t in self.tables}) != len(self.tables):
            raise ValueError("Duplicate schema grant")
        return self


class DashboardSchemaGrants(Protocol):
    def get(self, workspace_id: str, source_id: str) -> DashboardSchemaGrant: ...


class RegisteredSqlSchemaResolver:
    """Resolve granted metadata with one source timeout shared across all tables.

    The budget covers reader construction and catalog collection, not the surrounding
    grant/source repository reads. Driver calls use their configured timeouts; expiry
    stops subsequent work and discards partial or overdue catalog results.
    """

    def __init__(self, sources: SourceRepository, reads: RegisteredReadRegistry, grants: DashboardSchemaGrants) -> None:
        self._sources, self._reads, self._grants = sources, reads, grants

    def resolve(self, principal: Principal, source_id: str) -> AuthorizedSqlSchema:
        if not principal.can("manage"):
            raise AccessDenied()
        grant = self._grants.get(principal.workspace_id, source_id)
        if (
            not grant.enabled
            or principal.actor_id not in grant.actor_ids
            or (grant.source.workspace_id, grant.source.source_id) != (principal.workspace_id, source_id)
        ):
            raise AccessDenied("dashboard_schema_not_granted")
        view = self._sources.get(principal.workspace_id, source_id).view
        if (view.workspace_id, view.source_id) != (principal.workspace_id, source_id):
            raise AccessDenied()
        if view.source_revision != grant.source.revision:
            raise Conflict("dashboard_schema_source_changed")
        if not isinstance(view.connection, DbSourceView):
            raise InvalidInput("dashboard_schema_requires_database")
        table_names = {table.name for table in grant.tables}
        if not table_names.issubset(view.connection.allowed_tables):
            raise AccessDenied("dashboard_schema_table_not_granted")
        entry = self._reads.resolve(
            principal.workspace_id, source_id, view.source_revision, view.read_id, view.read_revision
        )
        config = entry.connection
        if (
            not isinstance(config, DatabaseSourceConfig)
            or config.source != grant.source
            or entry.read.source != grant.source
            or (entry.read.read_id, entry.read.revision) != (view.read_id, view.read_revision)
            or config.dialect != view.connection.dialect
            or not table_names.issubset(config.allowed_tables)
        ):
            raise AccessDenied("dashboard_schema_registration_mismatch")
        tables: list[SqlSchemaTable] = []
        deadline = time.monotonic() + config.limits.timeout_seconds
        try:
            reader = DatabaseSourceReader(config)
            try:
                for table in sorted(grant.tables, key=lambda item: item.name):
                    if time.monotonic() >= deadline:
                        raise DataSourceError("timeout")
                    columns = reader.describe_columns(table.name, table.columns, deadline=deadline)
                    if time.monotonic() >= deadline:
                        raise DataSourceError("timeout")
                    if len(columns) != len(table.columns) or {name for name, _ in columns} != set(table.columns):
                        raise InvalidInput("dashboard_schema_columns_mismatch")
                    tables.append(
                        SqlSchemaTable(
                            name=table.name,
                            columns=tuple(SqlSchemaColumn(name=name, sql_type=kind) for name, kind in columns),
                        )
                    )
            finally:
                reader.close()
        except (DataSourceError, ValidationError):
            raise DependencyUnavailable("dashboard_schema_unavailable") from None
        if self._grants.get(principal.workspace_id, source_id) != grant:
            raise Conflict("dashboard_schema_grant_changed")
        current = self._sources.get(principal.workspace_id, source_id).view
        if current != view:
            raise Conflict("dashboard_schema_source_changed")
        return AuthorizedSqlSchema(
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            source_id=source_id,
            source_revision=view.source_revision,
            schema_revision=canonical_hash([table.model_dump(mode="json") for table in tables]),
            dialect=view.connection.dialect,
            tables=tuple(tables),
        )
