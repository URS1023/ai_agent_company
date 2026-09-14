from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from test_office_export import setup

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.persistence.office_directory import SqlAlchemyOfficeDirectory, candidate_query


def test_candidate_query_is_scoped_to_readable_actor_grants_and_bounded():
    _, principal, _, _, _, _ = setup()
    compiled = candidate_query(principal, offset=50, limit=50).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "enterprise_office_grants.workspace_id =" in sql
    assert "enterprise_office_grants.actor_id =" in sql
    assert "enterprise_office_grants.can_read IS true" in sql
    assert "ORDER BY enterprise_office_grants.file_id" in sql
    assert "FOR UPDATE" not in sql
    assert set(compiled.params.values()) == {"workspace", "actor", 50, 51}


def test_directory_adapter_returns_only_one_scan_page_and_continuation():
    _, principal, _, _, _, _ = setup()
    session = MagicMock()
    session.scalars.return_value.all.return_value = [str(UUID(int=i)) for i in range(1, 4)]
    sessions = Mock()
    sessions.begin.return_value = session
    session.__enter__.return_value = session
    page = SqlAlchemyOfficeDirectory(sessions).candidates(principal, offset=0, limit=2)
    assert page.file_ids == (UUID(int=1), UUID(int=2))
    assert page.has_more is True


def test_invalid_persisted_file_identity_is_not_returned():
    _, principal, _, _, _, _ = setup()
    session = MagicMock()
    session.scalars.return_value.all.return_value = ["not-a-uuid"]
    session.__enter__.return_value = session
    with pytest.raises(PersistenceError):
        SqlAlchemyOfficeDirectory(Mock(begin=Mock(return_value=session))).candidates(principal, offset=0, limit=2)
