"""Checks for explicit local test execution; these tests never connect to a database."""

import pytest
from database_environment import database_tests_enabled, local_database_connect_args, validate_local_database
from psycopg._conninfo_attempts import conninfo_attempts
from sqlalchemy import create_engine


def test_database_execution_requires_explicit_opt_in() -> None:
    assert not database_tests_enabled({})
    assert not database_tests_enabled({"ENTERPRISE_LOCAL_DATABASE_TESTS": "true"})
    assert database_tests_enabled({"ENTERPRISE_LOCAL_DATABASE_TESTS": "1"})
    assert database_tests_enabled({"CI": "true"})


def test_local_database_accepts_only_dedicated_loopback_target() -> None:
    validate_local_database("postgresql+psycopg://tester:password@127.0.0.1:55432/enterprise_test")


@pytest.mark.parametrize("host,address", [("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1"), ("[::1]", "::1")])
def test_local_connection_pins_address_despite_inherited_hostaddr(monkeypatch, host: str, address: str) -> None:
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.10")
    url = f"postgresql+psycopg://tester:password@{host}:55432/enterprise_test"
    engine = create_engine(url)
    try:
        _, parameters = engine.dialect.create_connect_args(engine.url)
        parameters.update(local_database_connect_args(url))
        attempts = conninfo_attempts(parameters)
        assert len(attempts) == 1
        assert attempts[0]["hostaddr"] == address
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://tester:password@127.0.0.1/dify",
        "postgresql+psycopg://tester:password@remote.example/enterprise_test",
        "postgresql+psycopg://tester:password@127.0.0.1/enterprise_test?host=remote.example",
        "sqlite:///existing.db",
        "not a database URL",
    ],
)
def test_local_database_rejects_other_targets_without_echoing_credentials(url: str) -> None:
    with pytest.raises(ValueError, match="Dedicated loopback enterprise_test database required") as error:
        validate_local_database(url)
    assert "password" not in str(error.value)
