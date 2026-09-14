"""Atomic initial Office files with mandatory creation authorization and exact replay."""

import hashlib
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.office_edits import OfficeFileRecord, OfficeGrant

from .mapping import audit, transaction, utc_now
from .office_documents import encode_office_record
from .office_models import OfficeFileRow, OfficeGrantRow, OfficeRevisionRow
from .office_repository import OfficeSourceAccess, SqlAlchemyOfficeEditRepository


class OfficeCreationAccess(Protocol):
    def require(self, session: Session, principal: Principal, record: OfficeFileRecord) -> None:
        """Check creation entitlement, template and sources under their revocation locks.

        Called again for retries; no cached authorization or permissive default.
        """
        ...


class RegisteredOfficeCreationAccess:
    def __init__(self, sources: OfficeSourceAccess, require_template: Callable[[OfficeFileRecord], None]) -> None:
        self._sources, self._require_template = sources, require_template

    def require(self, session: Session, principal: Principal, record: OfficeFileRecord) -> None:
        if not principal.can("run") or record.workspace_id != principal.workspace_id:
            raise AccessDenied()
        self._require_template(record)
        # This context checks source entitlements only; the creator grants file ACLs
        # after the complete creation policy succeeds in the same transaction.
        source_context = OfficeGrant(principal.workspace_id, principal.actor_id, record.content.file_id, "read", 1)
        self._sources.require(session, source_context, record)


class SqlAlchemyOfficeFileCreator:
    def __init__(self, sessions: sessionmaker[Session], policy: OfficeCreationAccess) -> None:
        self._sessions, self._policy = sessions, policy

    def create(self, principal: Principal, record: OfficeFileRecord) -> OfficeFileRecord:
        if record.workspace_id != principal.workspace_id:
            raise AccessDenied()
        if record.content.revision != 1:
            raise InvalidInput("office_initial_revision_required")
        document = encode_office_record(record)
        try:
            return self._create(principal, record, document, allow_insert=True)
        except Conflict as error:
            if str(error) != "database_constraint_conflict":
                raise
            # The losing insert has rolled back; resolve an identical committed winner once.
            return self._create(principal, record, document, allow_insert=False)

    def _create(
        self, principal: Principal, record: OfficeFileRecord, document: str, *, allow_insert: bool
    ) -> OfficeFileRecord:
        with transaction(self._sessions) as session:
            head = session.scalar(
                select(OfficeFileRow)
                .where(
                    OfficeFileRow.workspace_id == principal.workspace_id,
                    OfficeFileRow.file_id == str(record.content.file_id),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if head is not None:
                self._policy.require(session, principal, record)
                if head.created_by != principal.actor_id:
                    raise AccessDenied()
                grant = OfficeGrant(
                    principal.workspace_id, principal.actor_id, record.content.file_id, "edit", head.acl_revision
                )
                SqlAlchemyOfficeEditRepository._permission(session, grant)
                initial = SqlAlchemyOfficeEditRepository._history(session, grant, 1)
                if initial.fingerprint() != record.fingerprint():
                    raise Conflict("office_creation_reused")
                return initial
            if not allow_insert:
                raise Conflict("office_creation_conflict")
            now = utc_now()
            session.add(
                OfficeFileRow(
                    workspace_id=principal.workspace_id,
                    file_id=str(record.content.file_id),
                    current_revision=1,
                    acl_revision=1,
                    created_by=principal.actor_id,
                    created_at=now,
                )
            )
            session.flush()
            self._policy.require(session, principal, record)
            session.add(
                OfficeGrantRow(
                    workspace_id=principal.workspace_id,
                    file_id=str(record.content.file_id),
                    actor_id=principal.actor_id,
                    can_read=True,
                    can_edit=True,
                )
            )
            session.add(
                OfficeRevisionRow(
                    workspace_id=principal.workspace_id,
                    file_id=str(record.content.file_id),
                    revision=1,
                    document_json=document,
                    document_hash=hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    actor_id=principal.actor_id,
                    created_at=now,
                )
            )
            audit(
                session,
                principal.workspace_id,
                "office_file",
                str(record.content.file_id),
                principal.actor_id,
                "office_file_created",
                now,
                {"revision": 1},
            )
        return record
