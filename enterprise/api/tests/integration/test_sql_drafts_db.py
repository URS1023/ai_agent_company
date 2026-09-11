"""Draft transactions against ORM and release DDL; disposable CI database only.

PostgreSQL migration DDL is relocated into the fixture's generated schema. Neither
this fixture nor its trial-evidence consumer executes DDL in shared public schema.
"""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect, select
from test_dashboards import dashboards as dashboards
from test_dashboards import initial
from test_repository import repository as repository

from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRecord
from enterprise_platform.application.dashboard_sql_proposals import SqlProposals
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.domain.data_sources import SourceRef
from enterprise_platform.persistence.dashboard_sql_drafts import SqlAlchemySqlDraftRepository, SqlDraftBase, SqlDraftRow
from enterprise_platform.persistence.migrate_sql_drafts import read_sql_drafts_sql, sql_drafts_statements
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.repository import SqlAlchemyRepository

pytestmark = pytest.mark.skipif(os.environ.get("CI") != "true", reason="Database integration runs in CI only")


@pytest.fixture(params=["model", "migration-ddl"])
def sql_storage_mode(request: pytest.FixtureRequest, repository: SqlAlchemyRepository) -> str:
    mode = str(request.param)
    if mode == "migration-ddl" and repository._sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Release DDL targets PostgreSQL; SQLite retains ORM coverage")
    return mode


def apply_test_schema_ddl(repository: SqlAlchemyRepository, statements: tuple[str, ...]) -> None:
    bind = repository._sessions.kw["bind"]
    assert isinstance(bind, Engine) and bind.dialect.name == "postgresql"
    schema = bind.get_execution_options()["schema_translate_map"][None]
    assert isinstance(schema, str) and schema.startswith("enterprise_test_")
    suffix = schema.removeprefix("enterprise_test_")
    assert len(suffix) == 32 and all(character in "0123456789abcdef" for character in suffix)
    quoted_schema = bind.dialect.identifier_preparer.quote_schema(schema)
    with bind.begin() as connection:
        for statement in statements:
            # Raw SQL does not honor SQLAlchemy's schema_translate_map.
            connection.exec_driver_sql(statement.replace("public.", f"{quoted_schema}."))


@pytest.fixture
def drafts(repository, dashboards, sql_storage_mode):
    dashboards.create(initial(), actor_id="actor")
    if sql_storage_mode == "migration-ddl":
        read_sql_drafts_sql()
        apply_test_schema_ddl(repository, sql_drafts_statements())
    else:
        with repository._sessions() as session:
            SqlDraftBase.metadata.create_all(session.get_bind())
    return SqlAlchemySqlDraftRepository(repository._sessions)


def test_deployed_draft_identity_and_listing_index(drafts, repository):
    bind = repository._sessions.kw["bind"]
    schema = bind.get_execution_options().get("schema_translate_map", {}).get(None)
    inspector = inspect(bind)
    assert inspector.get_pk_constraint("enterprise_dashboard_sql_drafts", schema=schema)["constrained_columns"] == [
        "workspace_id",
        "draft_id",
    ]
    indexes = inspector.get_indexes("enterprise_dashboard_sql_drafts", schema=schema)
    assert any(
        index["name"] == "ix_sql_draft_dashboard"
        and index["column_names"] == ["workspace_id", "dashboard_id", "created_at", "draft_id"]
        for index in indexes
    )


def record():
    return SqlDraftRecord(
        draft_id="draft-1",
        workspace_id="w",
        actor_id="actor",
        dashboard_id="dashboard-1",
        dashboard_revision=1,
        design_identity=initial().design.identity,
        source=SourceRef(workspace_id="w", source_id="s", revision="v1"),
        schema_revision="schema-1",
        prompt="Count inspections",
        created_at=datetime(2026, 9, 11, tzinfo=UTC),
        proposals=SqlProposals.model_validate_json("""{"slots":[{"slot_id":"count",
        "sql":"SELECT COUNT(*) AS n FROM measurements",
        "field_map":{"value":"n"},"metric_definition":"Count","time_definition":"All time"}]}"""),
    )


def test_roundtrip_scope_and_duplicate_insert_leave_one_draft_audit(drafts, repository):
    assert drafts.create(record()) == record()
    assert drafts.get("w", "draft-1") == record()
    with pytest.raises(NotFound):
        drafts.get("other", "draft-1")
    with pytest.raises(Conflict):
        drafts.create(record())
    with repository._sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "dashboard_sql_draft")
        ).all()
        assert len(events) == 1
        assert len(session.scalars(select(SqlDraftRow)).all()) == 1


def test_parent_revision_conflict_leaves_no_draft_or_draft_audit(drafts, repository):
    with pytest.raises(Conflict):
        drafts.create(record().model_copy(update={"dashboard_revision": 2}))
    with repository._sessions() as session:
        assert not session.scalars(select(SqlDraftRow)).all()
        assert not session.scalars(
            select(AuditEventRow).where(AuditEventRow.resource_type == "dashboard_sql_draft")
        ).all()


def test_corrupt_stored_document_is_not_returned(drafts, repository):
    drafts.create(record())
    with repository._sessions.begin() as session:
        row = session.scalar(select(SqlDraftRow))
        row.document_json += " "
    with pytest.raises(PersistenceError):
        drafts.get("w", "draft-1")
