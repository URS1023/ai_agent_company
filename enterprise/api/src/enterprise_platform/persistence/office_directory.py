"""Scan readable file grants only; no content read or long-lived cross-file locks."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import InvalidInput, PersistenceError
from enterprise_platform.application.office_directory import OfficeCandidates

from .mapping import transaction
from .office_models import OfficeGrantRow


def candidate_query(principal: Principal, *, offset: int, limit: int) -> Select[tuple[str]]:
    if type(offset) is not int or not 0 <= offset <= 2147483647 or type(limit) is not int or not 1 <= limit <= 50:
        raise InvalidInput("office_directory_page_invalid")
    return (
        select(OfficeGrantRow.file_id)
        .where(
            OfficeGrantRow.workspace_id == principal.workspace_id,
            OfficeGrantRow.actor_id == principal.actor_id,
            OfficeGrantRow.can_read.is_(True),
        )
        .order_by(OfficeGrantRow.file_id)
        .offset(offset)
        .limit(limit + 1)
    )


class SqlAlchemyOfficeDirectory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def candidates(self, principal: Principal, *, offset: int, limit: int) -> OfficeCandidates:
        query = candidate_query(principal, offset=offset, limit=limit)
        with transaction(self._sessions) as session:
            rows = session.scalars(query).all()
        try:
            ids = tuple(UUID(value) for value in rows[:limit])
        except (ValueError, TypeError, AttributeError):
            raise PersistenceError("office_directory_identity_invalid") from None
        return OfficeCandidates(ids, len(rows) > limit)
