"""Bounded current-ACL scans, with independent source checks before exposing metadata.

Offsets describe scanned grants, not authorized result counts. A page may be empty
and still have a continuation. Concurrent grant changes can shift pages; refresh
starts a new browse, not a repeatable database snapshot.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from .contracts import Contract, Principal
from .errors import AccessDenied, InvalidInput, PersistenceError
from .office_edits import OfficeEditService


@dataclass(frozen=True, slots=True)
class OfficeCandidates:
    file_ids: tuple[UUID, ...]
    has_more: bool


class OfficeCandidateRepository(Protocol):
    def candidates(self, principal: Principal, *, offset: int, limit: int) -> OfficeCandidates: ...


class OfficeFileSummary(Contract):
    file_id: UUID
    revision: str
    kind: Literal["document", "presentation"]
    template_id: str
    template_revision: str


class OfficeDirectoryPage(Contract):
    items: tuple[OfficeFileSummary, ...]
    next_offset: int | None


class OfficeDirectoryService:
    def __init__(self, candidates: OfficeCandidateRepository, files: OfficeEditService) -> None:
        self._candidates, self._files = candidates, files

    def list_files(self, principal: Principal, *, offset: int = 0) -> OfficeDirectoryPage:
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise InvalidInput("office_directory_offset_invalid")
        candidates = self._candidates.candidates(principal, offset=offset, limit=50)
        ids = candidates.file_ids
        if len(ids) > 50 or len(set(ids)) != len(ids) or (not ids and candidates.has_more):
            raise PersistenceError("office_directory_page_invalid")
        if candidates.has_more and offset + len(ids) > 2147483647:
            raise InvalidInput("office_directory_offset_exhausted")
        items: list[OfficeFileSummary] = []
        for file_id in ids:
            try:
                record = self._files.read(principal, file_id)
            except AccessDenied:
                continue
            items.append(
                OfficeFileSummary(
                    file_id=record.content.file_id,
                    revision=str(record.content.revision),
                    kind=record.content.kind,
                    template_id=record.template_id,
                    template_revision=str(record.template_revision),
                )
            )
        return OfficeDirectoryPage(items=tuple(items), next_offset=offset + len(ids) if candidates.has_more else None)
