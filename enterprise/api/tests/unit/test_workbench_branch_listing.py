from unittest.mock import MagicMock, create_autospec
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from test_workbench_messages import scope

from enterprise_platform.application.workbench_branch_listing import BranchListQuery
from enterprise_platform.application.workbench_branches import create_root_branch
from enterprise_platform.persistence.workbench_branch_repository import SqlAlchemyBranchRepository
from enterprise_platform.persistence.workbench_branches import branch_context_row


def query(**changes):
    return BranchListQuery(workspace_id="workspace", actor_id="actor", installed_app_id=UUID(int=1), **changes)


def row(branch_id, actor_id="actor"):
    return branch_context_row(
        create_root_branch(
            scope().model_copy(update={"workspace_id": "workspace", "actor_id": actor_id, "branch_id": branch_id})
        )
    )


@pytest.fixture
def repository():
    sessions = MagicMock(spec=sessionmaker)
    session = create_autospec(Session, instance=True)
    sessions.begin.return_value.__enter__.return_value = session
    return SqlAlchemyBranchRepository(sessions), session


def test_listing_is_scoped_bounded_and_cursor_ordered(repository):
    repo, session = repository
    session.scalars.return_value.all.return_value = [row("b"), row("c"), row("d")]
    result = repo.list_branches(query(after="a", limit=2))
    assert [item.scope.branch_id for item in result.items] == ["b", "c"]
    assert result.next_after == "c"
    statement = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(statement)
    assert "workspace_id =" in sql and "actor_id =" in sql and "installed_app_id =" in sql
    assert "branch_id >" in sql and "ORDER BY" in sql and "LIMIT" in sql
    assert 3 in statement.params.values()
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_last_and_empty_pages_have_no_continuation(repository):
    repo, session = repository
    session.scalars.return_value.all.return_value = [row("a")]
    assert repo.list_branches(query()).next_after is None
    session.scalars.return_value.all.return_value = []
    assert repo.list_branches(query()).items == ()


def test_foreign_or_corrupt_rows_never_escape_listing(repository):
    from enterprise_platform.application.errors import PersistenceError

    repo, session = repository
    session.scalars.return_value.all.return_value = [row("a", actor_id="other")]
    with pytest.raises(PersistenceError):
        repo.list_branches(query())
    corrupt = row("a")
    corrupt.document_hash = "0" * 64
    session.scalars.return_value.all.return_value = [corrupt]
    with pytest.raises(PersistenceError):
        repo.list_branches(query())


@pytest.mark.parametrize("changes", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"after": ""}, {"after": " x "}])
def test_invalid_page_requests_are_rejected(changes):
    with pytest.raises(ValueError):
        query(**changes)
