"""Export a saved revision, checking current access before and after rendering.

The final read is the authorization observation point, not a lock held over network
delivery. This service neither publishes a public URL nor creates a stored artifact.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from .contracts import Principal
from .errors import Conflict, InvalidInput
from .office_edits import OfficeEditService, OfficeFileRecord


@dataclass(frozen=True, slots=True)
class OfficeDocumentExport:
    content: bytes
    filename: str
    revision: int
    media_type: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class OfficeExportService:
    def __init__(self, files: OfficeEditService, render_document: Callable[[OfficeFileRecord], bytes]) -> None:
        self._files = files
        self._render_document = render_document

    def export_document(self, principal: Principal, file_id: UUID, *, expected_revision: int) -> OfficeDocumentExport:
        if type(expected_revision) is not int or expected_revision < 1:
            raise InvalidInput("office_revision_invalid")
        record = self._files.read(principal, file_id)
        if record.content.revision != expected_revision:
            raise Conflict("office_revision_conflict")
        if record.content.kind != "document":
            raise InvalidInput("office_document_required")
        content = self._render_document(record)
        fingerprint = record.fingerprint()
        current = self._files.read(principal, file_id)
        if current.fingerprint() != fingerprint:
            raise Conflict("office_revision_conflict")
        return OfficeDocumentExport(
            content=content,
            filename=f"office-{file_id}-r{expected_revision}.docx",
            revision=expected_revision,
        )
