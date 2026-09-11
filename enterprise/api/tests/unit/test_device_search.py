"""No database connections: service/HTTP boundaries and actual SQL compilation only."""

from unittest.mock import create_autospec, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Device, Page, Principal
from enterprise_platform.application.errors import InvalidInput, PersistenceError
from enterprise_platform.application.ports import EnterpriseRepository, IdentityProvider
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app
from enterprise_platform.persistence.repository import SqlAlchemyRepository


def principal() -> Principal:
    return Principal(actor_id="a", workspace_id="workspace-1", workspace_role="normal", display_name="Reader")


@pytest.mark.parametrize(
    "q,department,expected_q,expected_department",
    [(" 0001 ", " 生产一部 ", "0001", "生产一部"), (" \t ", " ", None, None), (None, None, None, None)],
)
def test_service_normalizes_filters_without_losing_zero_prefix(q, department, expected_q, expected_department) -> None:
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    service = BusinessService(repository)
    service.list_devices(principal(), q=q, department=department, offset=3, limit=7)
    repository.list_devices.assert_called_once_with(
        "workspace-1", offset=3, limit=7, q=expected_q, department=expected_department
    )


@pytest.mark.parametrize("field", ["q", "department"])
def test_service_rejects_oversized_filters_before_repository(field: str) -> None:
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with pytest.raises(InvalidInput, match="invalid_device_filter"):
        BusinessService(repository).list_devices(principal(), **{field: "x" * 201})
    repository.list_devices.assert_not_called()


def test_http_filters_reach_scoped_repository_and_appear_in_openapi() -> None:
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repository.list_devices.return_value = Page[Device](items=(), offset=2, limit=4, total=0)
    identity = create_autospec(IdentityProvider, instance=True, spec_set=True)
    identity.resolve.return_value = principal()
    app = create_app(BusinessService(repository), identity)
    with TestClient(app) as client:
        response = client.get(
            "/enterprise/api/v1/devices", params={"q": " 000%_/ ", "department": " 一部 ", "offset": 2, "limit": 4}
        )
    assert response.status_code == 200
    repository.list_devices.assert_called_once_with("workspace-1", offset=2, limit=4, q="000%_/", department="一部")
    parameters = {
        item["name"]: item for item in app.openapi()["paths"]["/enterprise/api/v1/devices"]["get"]["parameters"]
    }
    for field in ("q", "department"):
        assert {"type": "string", "maxLength": 200} in parameters[field]["schema"]["anyOf"]


@pytest.mark.parametrize("field", ["q", "department"])
def test_http_rejects_oversized_filters(field: str) -> None:
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    identity = create_autospec(IdentityProvider, instance=True, spec_set=True)
    identity.resolve.return_value = principal()
    with TestClient(create_app(BusinessService(repository), identity)) as client:
        assert client.get("/enterprise/api/v1/devices", params={field: "x" * 201}).status_code == 422
    repository.list_devices.assert_not_called()


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()], ids=["sqlite", "postgresql"])
def test_repository_filters_count_and_rows_with_same_bound_where_before_pagination(dialect) -> None:
    session = create_autospec(Session, instance=True, spec_set=True)
    engine = create_autospec(Engine, instance=True)
    engine.dialect = dialect
    session.get_bind.return_value = engine
    session.scalar.return_value = 7
    session.scalars.return_value = ()
    repository = SqlAlchemyRepository(create_autospec(sessionmaker, instance=True))
    with patch("enterprise_platform.persistence.repository.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        result = repository.list_devices("workspace-1", q=" 000%_/ ", department=" 生产一部 ", offset=3, limit=4)
    count = session.scalar.call_args.args[0]
    query = session.scalars.call_args.args[0]
    assert count.whereclause.compare(query.whereclause)
    assert query._offset_clause.value == 3
    assert query._limit_clause.value == 4
    assert result.total == 7
    sql = str(query.compile(dialect=dialect))
    params = query.compile(dialect=dialect).params
    assert "000%_/" not in sql
    assert "000/%/_//" in params.values()
    assert "生产一部" in params.values()
    assert "workspace-1" in params.values()
    assert "enterprise_devices.deleted" in sql
    assert "ESCAPE '/'" in sql
    if dialect.name == "sqlite":
        assert "JSON_EXTRACT(enterprise_devices.device_json" in sql
        assert "CAST(enterprise_devices.device_json AS JSON)" not in sql
    else:
        assert "CAST(enterprise_devices.device_json AS JSON)" in sql


def test_unknown_storage_dialect_is_explicit_failure_not_page_local_filtering() -> None:
    session = create_autospec(Session, instance=True, spec_set=True)
    engine = create_autospec(Engine, instance=True)
    engine.dialect = sqlite.dialect()
    engine.dialect.name = "unsupported"
    session.get_bind.return_value = engine
    repository = SqlAlchemyRepository(create_autospec(sessionmaker, instance=True))
    with patch("enterprise_platform.persistence.repository.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        with pytest.raises(PersistenceError, match="unsupported_database_dialect"):
            repository.list_devices("workspace-1", q="0001")
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
