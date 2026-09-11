"""Generate review-only drafts from a server-authorized schema projection.

No credentials, sample rows or visuals enter the model request. Schema resolution
must enforce current source/table/column access, not accept a browser schema.
Generation neither approves queries nor changes existing dashboard bindings.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import Field, StringConstraints, model_validator
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from enterprise_platform.adapters.data_sources import validate_sql_structure
from enterprise_platform.adapters.sql_schema import ExactSqlSchema
from enterprise_platform.domain.dashboard import SlotContract
from enterprise_platform.domain.data_sources import DataSourceError, SourceRef

from .contracts import Contract, Identifier, Principal
from .dashboard_refresh_service import DashboardRecord, DashboardRepository
from .dashboard_sql_drafts import SqlDraftRecord, SqlDraftRepository
from .dashboard_sql_proposals import Name, SqlProposals, parse_sql_proposals
from .errors import AccessDenied, Conflict, InvalidInput, PersistenceError


class SqlSchemaColumn(Contract):
    name: Name
    sql_type: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]


class SqlSchemaTable(Contract):
    name: Name
    columns: tuple[SqlSchemaColumn, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_columns(self) -> Self:
        if len({column.name for column in self.columns}) != len(self.columns):
            raise ValueError("Duplicate schema columns")
        return self


class AuthorizedSqlSchema(Contract):
    workspace_id: Identifier
    actor_id: Identifier
    source_id: Identifier
    source_revision: Identifier
    schema_revision: Identifier
    dialect: Literal["postgresql", "mysql"]
    tables: tuple[SqlSchemaTable, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_tables(self) -> Self:
        if len({table.name for table in self.tables}) != len(self.tables):
            raise ValueError("Duplicate schema tables")
        return self


class GenerateSqlCommand(Contract):
    expected_revision: int = Field(strict=True, ge=1)
    expected_design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: Identifier
    prompt: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8000)]


class SqlGenerationRequest(Contract):
    prompt: str
    dialect: Literal["postgresql", "mysql"]
    tables: tuple[SqlSchemaTable, ...]
    slots: tuple[SlotContract, ...]


class SqlGenerationReceipt(Contract):
    draft_id: Identifier
    dashboard_id: Identifier
    dashboard_revision: int
    design_identity: str
    source_id: Identifier
    source_revision: Identifier
    schema_revision: Identifier
    proposals: SqlProposals
    status: Literal["draft"] = "draft"


class SqlSchemaResolver(Protocol):
    def resolve(self, principal: Principal, source_id: str) -> AuthorizedSqlSchema: ...


class SqlDraftInspection(Contract):
    draft: SqlDraftRecord
    current_dashboard_revision: int
    requires_regeneration: bool


class SqlDraftGenerator(Protocol):
    async def generate(self, principal: Principal, request: SqlGenerationRequest) -> str: ...


def validate_proposal_sql(proposals: SqlProposals, schema: AuthorizedSqlSchema) -> None:
    """Static SQL/column checks only; not execution approval or metric verification."""
    dialect = "postgres" if schema.dialect == "postgresql" else "mysql"
    try:
        catalog = ExactSqlSchema(
            {table.name: {column.name: column.sql_type for column in table.columns} for table in schema.tables},
            dialect=dialect,
        )
        for proposal in proposals.slots:
            tree = validate_sql_structure(
                proposal.sql, dialect=schema.dialect, allowed_tables=frozenset(table.name for table in schema.tables)
            )
            if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
                raise ValueError("Explicit projection required")
            if any(join.args.get("method") == "NATURAL" for join in tree.find_all(exp.Join)):
                raise ValueError("Explicit join columns required")
            if tree.find(exp.Placeholder):
                raise ValueError("No parameter contract in this draft")
            if any(not isinstance(item, (exp.Column, exp.Alias)) for item in tree.expressions):
                raise ValueError("Expressions require explicit output aliases")
            names = tree.named_selects
            if not all(names) or len(set(names)) != len(names) or not set(proposal.field_map.values()).issubset(names):
                raise ValueError("Invalid projection mapping")
            qualify(
                tree.copy(),
                dialect=dialect,
                schema=catalog,
                infer_schema=False,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            )
    except (DataSourceError, SqlglotError, ValueError, RecursionError):
        raise InvalidInput("dashboard_sql_validation_failed") from None


class DashboardSqlGeneration:
    def __init__(
        self,
        repository: DashboardRepository,
        schemas: SqlSchemaResolver,
        generator: SqlDraftGenerator,
        drafts: SqlDraftRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository, self._schemas, self._generator = repository, schemas, generator
        self._drafts = drafts
        self._id = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    async def inspect_draft(self, principal: Principal, dashboard_id: str, draft_id: str) -> SqlDraftInspection:
        if not principal.can("manage"):
            raise AccessDenied()
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (parent.workspace_id, parent.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, draft_id)
        if (draft.workspace_id, draft.dashboard_id, draft.draft_id, draft.source.workspace_id) != (
            principal.workspace_id,
            dashboard_id,
            draft_id,
            principal.workspace_id,
        ):
            raise AccessDenied()
        schema = await asyncio.to_thread(self._schemas.resolve, principal, draft.source.source_id)
        if (schema.workspace_id, schema.actor_id, schema.source_id) != (
            principal.workspace_id,
            principal.actor_id,
            draft.source.source_id,
        ):
            raise AccessDenied()
        try:
            validate_proposal_sql(draft.proposals, schema)
        except InvalidInput:
            raise AccessDenied("sql_draft_source_not_granted") from None
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (parent.workspace_id, parent.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        return SqlDraftInspection(
            draft=draft,
            current_dashboard_revision=parent.revision,
            requires_regeneration=(
                (parent.revision, parent.design.identity, schema.source_revision, schema.schema_revision)
                != (draft.dashboard_revision, draft.design_identity, draft.source.revision, draft.schema_revision)
            ),
        )

    async def generate(
        self, principal: Principal, dashboard_id: str, command: GenerateSqlCommand
    ) -> SqlGenerationReceipt:
        if not principal.can("manage"):
            raise AccessDenied()

        def check_record(record: DashboardRecord) -> None:
            if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
                raise AccessDenied()
            if (record.revision, record.design.identity) != (
                command.expected_revision,
                command.expected_design_identity,
            ):
                raise Conflict("dashboard_revision_conflict")

        record = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        check_record(record)
        schema = await asyncio.to_thread(self._schemas.resolve, principal, command.source_id)
        if (schema.workspace_id, schema.actor_id, schema.source_id) != (
            principal.workspace_id,
            principal.actor_id,
            command.source_id,
        ):
            raise AccessDenied("dashboard_schema_context_mismatch")
        request = SqlGenerationRequest(
            prompt=command.prompt, dialect=schema.dialect, tables=schema.tables, slots=record.design.slots
        )
        if len(request.model_dump_json().encode("utf-8")) > 262144:
            raise InvalidInput("dashboard_generation_context_too_large")
        text = await self._generator.generate(principal, request)
        proposals = parse_sql_proposals(text, record.design)
        validate_proposal_sql(proposals, schema)
        current_schema = await asyncio.to_thread(self._schemas.resolve, principal, command.source_id)
        if current_schema != schema:
            raise Conflict("dashboard_schema_changed")
        current_record = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        check_record(current_record)
        draft = SqlDraftRecord(
            draft_id=self._id(),
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            dashboard_id=dashboard_id,
            dashboard_revision=record.revision,
            design_identity=record.design.identity,
            source=SourceRef(
                workspace_id=principal.workspace_id, source_id=schema.source_id, revision=schema.source_revision
            ),
            schema_revision=schema.schema_revision,
            prompt=command.prompt,
            proposals=proposals,
            created_at=self._clock(),
        )
        saved = await asyncio.to_thread(self._drafts.create, draft)
        if saved != draft:
            raise PersistenceError("sql_draft_receipt_mismatch")
        return SqlGenerationReceipt(
            draft_id=saved.draft_id,
            dashboard_id=dashboard_id,
            dashboard_revision=record.revision,
            design_identity=record.design.identity,
            source_id=schema.source_id,
            source_revision=schema.source_revision,
            schema_revision=schema.schema_revision,
            proposals=proposals,
        )
