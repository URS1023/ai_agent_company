from unittest.mock import create_autospec, patch
from uuid import UUID

import pytest
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker
from test_managed_execution import NATIVE, record

from enterprise_platform.application.errors import InvalidState
from enterprise_platform.persistence.execution_lookup import SqlAlchemyExecutionLookup


def dependencies(dialect):
    session = create_autospec(Session, instance=True, spec_set=True)
    engine = create_autospec(Connection, instance=True)
    engine.dialect = dialect
    session.connection.return_value = engine
    sessions = create_autospec(sessionmaker, instance=True)
    sessions.begin.return_value.__enter__.return_value = session
    return SqlAlchemyExecutionLookup(sessions), session


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_native_lookup_is_bounded_and_scoped_in_actual_compiled_sql(dialect):
    lookup, session = dependencies(dialect)
    session.scalars.return_value = ()
    assert lookup.find_native_run("workspace-1", "app-1", NATIVE) is None
    statement = session.scalars.call_args.args[0].compile(dialect=dialect)
    assert "workspace-1" in statement.params.values()
    assert NATIVE in statement.params.values()
    assert 2 in statement.params.values()
    assert "workspace_id" in str(statement) and "dify_run_id" in str(statement)


def test_ambiguous_association_never_selects_an_arbitrary_run():
    lookup, session = dependencies(postgresql.dialect())
    session.scalars.return_value = (object(), object())
    with pytest.raises(InvalidState, match="ambiguous"):
        lookup.find_native_run("workspace-1", "app-1", NATIVE)


@pytest.mark.parametrize(
    "changes",
    [
        {"workspace_id": "other"},
        {"dify_run_id": str(UUID(int=8))},
        {"spec": record().spec.model_copy(update={"app_id": "other"})},
    ],
)
def test_reconstructed_run_scope_is_verified(changes):
    lookup, session = dependencies(postgresql.dialect())
    session.scalars.return_value = (object(),)
    with patch(
        "enterprise_platform.persistence.execution_lookup.as_run", return_value=record().model_copy(update=changes)
    ):
        with pytest.raises(InvalidState):
            lookup.find_native_run("workspace-1", "app-1", NATIVE)
