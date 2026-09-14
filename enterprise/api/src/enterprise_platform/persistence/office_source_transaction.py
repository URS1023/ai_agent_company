"""Reject transaction modes that cannot retain Office source locks until commit."""

from typing import Protocol, cast

from sqlalchemy.orm import Session

from enterprise_platform.application.errors import PersistenceError


class _PostgresDriverConnection(Protocol):
    @property
    def autocommit(self) -> bool: ...


def require_source_transaction(session: Session) -> None:
    connection = session.connection()
    if connection.dialect.name != "postgresql":
        return
    driver = connection.connection.dbapi_connection
    # SQLAlchemy get_isolation_level intentionally excludes the AUTOCOMMIT flag.
    if (
        connection.dialect.driver not in {"psycopg", "psycopg2"}
        or driver is None
        or cast(_PostgresDriverConnection, driver).autocommit is not False
        or connection.get_isolation_level() != "READ COMMITTED"
    ):
        raise PersistenceError("office_source_isolation_invalid")
