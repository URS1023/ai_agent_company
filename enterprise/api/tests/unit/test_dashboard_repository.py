from unittest.mock import create_autospec

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable

from enterprise_platform.persistence.dashboard_models import DashboardBase, DashboardRow
from enterprise_platform.persistence.dashboards import SqlAlchemyDashboardRepository


def test_dashboard_schema_is_independent_and_workspace_keyed() -> None:
    assert set(DashboardBase.metadata.tables) == {"enterprise_dashboards"}
    assert list(DashboardRow.__table__.primary_key.columns.keys()) == ["workspace_id", "dashboard_id"]
    ddl = str(CreateTable(DashboardRow.__table__).compile(dialect=postgresql.dialect()))
    assert "revision > 0" in ddl
    assert "design_identity" in ddl
    assert "bindings_hash" in ddl


def test_repository_construction_never_opens_a_database_connection() -> None:
    sessions = create_autospec(sessionmaker[Session], instance=True)
    SqlAlchemyDashboardRepository(sessions)
    sessions.assert_not_called()
