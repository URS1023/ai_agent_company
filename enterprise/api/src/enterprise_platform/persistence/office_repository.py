"""Transactional Office revision storage; live source authorization is mandatory.

All file/ACL mutations must take the same file-head lock. Historical revisions
are appended, never overwritten. This adapter is not wired to HTTP until schema
migration and PostgreSQL concurrency tests are verified.
"""

import hashlib
import re
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, PersistenceError
from enterprise_platform.application.office_edits import OfficeAction, OfficeEditReceipt, OfficeFileRecord, OfficeGrant

from .mapping import audit, transaction, utc_now
from .office_documents import decode_office_record, encode_office_record
from .office_models import OfficeEditReceiptRow, OfficeFileRow, OfficeGrantRow, OfficeRevisionRow


class OfficeSourceAccess(Protocol):
    def require(self, session: Session, grant: OfficeGrant, record: OfficeFileRecord) -> None:
        """Check/lock current source grants for this exact snapshot set or deny access.

        Implementations must participate in the transaction's revocation protocol;
        a previously cached Boolean or a permissive fallback is not sufficient.
        """
        ...


class SqlAlchemyOfficeEditRepository:
    def __init__(self, sessions: sessionmaker[Session], sources: OfficeSourceAccess) -> None:
        self._sessions, self._sources = sessions, sources

    @staticmethod
    def _head(session: Session, workspace_id: str, file_id: UUID) -> OfficeFileRow:
        head = session.scalar(
            select(OfficeFileRow)
            .where(OfficeFileRow.workspace_id == workspace_id, OfficeFileRow.file_id == str(file_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if head is None:
            raise AccessDenied()
        return head

    @staticmethod
    def _permission(session: Session, grant: OfficeGrant) -> None:
        permission = session.scalar(
            select(OfficeGrantRow)
            .where(
                OfficeGrantRow.workspace_id == grant.workspace_id,
                OfficeGrantRow.file_id == str(grant.file_id),
                OfficeGrantRow.actor_id == grant.actor_id,
            )
            .execution_options(populate_existing=True)
        )
        if permission is None or not permission.can_read or (grant.action == "edit" and not permission.can_edit):
            raise AccessDenied()

    @classmethod
    def _lock(cls, session: Session, grant: OfficeGrant) -> OfficeFileRow:
        if grant.action not in {"read", "edit"} or type(grant.acl_revision) is not int or grant.acl_revision < 1:
            raise AccessDenied()
        head = cls._head(session, grant.workspace_id, grant.file_id)
        if head.acl_revision != grant.acl_revision:
            raise AccessDenied()
        cls._permission(session, grant)
        return head

    def authorize(self, principal: Principal, file_id: UUID, action: OfficeAction) -> OfficeGrant:
        """Resolve the current scoped grant; subsequent operations recheck it under lock."""
        if action not in {"read", "edit"}:
            raise AccessDenied()
        with transaction(self._sessions) as session:
            head = self._head(session, principal.workspace_id, file_id)
            if type(head.acl_revision) is not int or head.acl_revision < 1:
                raise AccessDenied()
            grant = OfficeGrant(principal.workspace_id, principal.actor_id, file_id, action, head.acl_revision)
            self._permission(session, grant)
            record = self._history(session, grant, head.current_revision)
            self._sources.require(session, grant, record)
        return grant

    @staticmethod
    def _history(session: Session, grant: OfficeGrant, revision: int) -> OfficeFileRecord:
        row = session.scalar(
            select(OfficeRevisionRow).where(
                OfficeRevisionRow.workspace_id == grant.workspace_id,
                OfficeRevisionRow.file_id == str(grant.file_id),
                OfficeRevisionRow.revision == revision,
            )
        )
        if row is None or hashlib.sha256(row.document_json.encode("utf-8")).hexdigest() != row.document_hash:
            raise PersistenceError("office_history_invalid")
        record = decode_office_record(row.document_json)
        if (record.workspace_id, record.content.file_id, record.content.revision) != (
            grant.workspace_id,
            grant.file_id,
            revision,
        ):
            raise PersistenceError("office_history_invalid")
        return record

    def _receipt(self, session: Session, grant: OfficeGrant, request_id: UUID) -> OfficeEditReceipt | None:
        row = session.scalar(
            select(OfficeEditReceiptRow).where(
                OfficeEditReceiptRow.workspace_id == grant.workspace_id,
                OfficeEditReceiptRow.file_id == str(grant.file_id),
                OfficeEditReceiptRow.actor_id == grant.actor_id,
                OfficeEditReceiptRow.request_id == str(request_id),
            )
        )
        if row is None:
            return None
        record = self._history(session, grant, row.result_revision)
        self._sources.require(session, grant, record)
        return OfficeEditReceipt(
            grant.workspace_id, grant.actor_id, grant.file_id, request_id, row.command_hash, record
        )

    def get(self, grant: OfficeGrant) -> OfficeFileRecord:
        with transaction(self._sessions) as session:
            head = self._lock(session, grant)
            record = self._history(session, grant, head.current_revision)
            self._sources.require(session, grant, record)
        return record

    def find_receipt(self, grant: OfficeGrant, request_id: UUID) -> OfficeEditReceipt | None:
        with transaction(self._sessions) as session:
            self._lock(session, grant)
            receipt = self._receipt(session, grant, request_id)
        return receipt

    def commit(
        self,
        *,
        grant: OfficeGrant,
        expected: OfficeFileRecord,
        candidate: OfficeFileRecord,
        request_id: UUID,
        command_hash: str,
    ) -> OfficeEditReceipt:
        if grant.action != "edit":
            raise AccessDenied()
        if re.fullmatch(r"[0-9a-f]{64}", command_hash) is None:
            raise Conflict("office_command_hash_invalid")
        document = encode_office_record(candidate)
        with transaction(self._sessions) as session:
            head = self._lock(session, grant)
            current = self._history(session, grant, head.current_revision)
            self._sources.require(session, grant, current)
            previous = self._receipt(session, grant, request_id)
            if previous is not None:
                if previous.command_hash != command_hash:
                    raise Conflict("office_request_reused")
                return previous
            if current.fingerprint() != expected.fingerprint():
                raise Conflict("office_revision_conflict")
            if (
                candidate.workspace_id != current.workspace_id
                or candidate.content.file_id != current.content.file_id
                or candidate.content.revision != current.content.revision + 1
                or candidate.content.kind != current.content.kind
                or (candidate.template_id, candidate.template_revision, candidate.source_snapshot_ids)
                != (current.template_id, current.template_revision, current.source_snapshot_ids)
                or tuple((unit.unit_id, unit.kind) for unit in candidate.content.units)
                != tuple((unit.unit_id, unit.kind) for unit in current.content.units)
            ):
                raise Conflict("office_candidate_mismatch")
            now = utc_now()
            session.add(
                OfficeRevisionRow(
                    workspace_id=grant.workspace_id,
                    file_id=str(grant.file_id),
                    revision=candidate.content.revision,
                    document_json=document,
                    document_hash=hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    actor_id=grant.actor_id,
                    created_at=now,
                )
            )
            # No ORM relationship orders these independent mappers; persist the FK target first.
            session.flush()
            session.add(
                OfficeEditReceiptRow(
                    workspace_id=grant.workspace_id,
                    file_id=str(grant.file_id),
                    actor_id=grant.actor_id,
                    request_id=str(request_id),
                    command_hash=command_hash,
                    result_revision=candidate.content.revision,
                    created_at=now,
                )
            )
            head.current_revision = candidate.content.revision
            audit(
                session,
                grant.workspace_id,
                "office_file",
                str(grant.file_id),
                grant.actor_id,
                "office_revision_saved",
                now,
                {"revision": candidate.content.revision, "request_id": str(request_id)},
            )
            result = OfficeEditReceipt(
                grant.workspace_id, grant.actor_id, grant.file_id, request_id, command_hash, candidate
            )
        return result
