"""Explicit opt-in for disposable tests, independent of native Dify configuration.

Local PostgreSQL fixtures additionally require the dedicated loopback database.
Fixtures still own a fresh random schema; this helper never connects or mutates data.
"""

from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def database_tests_enabled(environment: Mapping[str, str]) -> bool:
    return environment.get("CI") == "true" or environment.get("ENTERPRISE_LOCAL_DATABASE_TESTS") == "1"


def validate_local_database(database_url: str) -> None:
    try:
        url = make_url(database_url)
        valid = (
            url.drivername == "postgresql+psycopg"
            and url.host in {"127.0.0.1", "localhost", "::1"}
            and url.database == "enterprise_test"
            and bool(url.username)
            and not url.query
        )
    except (ArgumentError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Dedicated loopback enterprise_test database required") from None


def local_database_connect_args(database_url: str) -> dict[str, str]:
    """Pin libpq's effective endpoint instead of trusting inherited PGHOSTADDR."""
    validate_local_database(database_url)
    url = make_url(database_url)
    return {
        "hostaddr": "::1" if url.host == "::1" else "127.0.0.1",
        "port": str(url.port or 5432),
        "connect_timeout": "10",
    }
