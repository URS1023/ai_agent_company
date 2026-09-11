"""Transactional Office permission changes; management policy must be explicitly supplied."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import Conflict
from enterprise_platform.application.office_permissions import OfficeManagementContext, OfficePermissionChange

from .mapping import audit, transaction, utc_now
from .office_models import OfficeGrantRow
from .office_repository import SqlAlchemyOfficeEditRepository


class OfficeManagementAccess(Protocol):
    def require(
        self, session: Session, principal: Principal, head: OfficeManagementContext, command: OfficePermissionChange
    ) -> None:
        """Check management, target membership and delegation after acquiring the file lock.

        Check current source/template delegation using the same revocation protocol.
        Removing all access must be possible for former members. No cached grants.
        """
        ...


class SqlAlchemyOfficeAcl:
    def __init__(self, sessions: sessionmaker[Session], policy: OfficeManagementAccess) -> None:
        self._sessions, self._policy = sessions, policy

    def change(self, principal: Principal, file_id: UUID, command: OfficePermissionChange) -> int:
        with transaction(self._sessions) as session:
            head = SqlAlchemyOfficeEditRepository._head(session, principal.workspace_id, file_id)
            context = OfficeManagementContext(head.workspace_id, file_id, head.created_by, head.acl_revision)
            self._policy.require(session, principal, context, command)
            if head.acl_revision != command.expected_acl_revision:
                raise Conflict("office_acl_revision_conflict")
            permission = session.scalar(
                select(OfficeGrantRow)
                .where(
                    OfficeGrantRow.workspace_id == principal.workspace_id,
                    OfficeGrantRow.file_id == str(file_id),
                    OfficeGrantRow.actor_id == command.actor_id,
                )
                .execution_options(populate_existing=True)
            )
            current = (permission.can_read, permission.can_edit) if permission is not None else (False, False)
            if current == (command.can_read, command.can_edit):
                return head.acl_revision
            if head.acl_revision == 9223372036854775807:
                raise Conflict("office_acl_revision_exhausted")
            if permission is None:
                permission = OfficeGrantRow(
                    workspace_id=principal.workspace_id, file_id=str(file_id), actor_id=command.actor_id
                )
                session.add(permission)
            permission.can_read, permission.can_edit = command.can_read, command.can_edit
            head.acl_revision += 1
            audit(
                session,
                principal.workspace_id,
                "office_file",
                str(file_id),
                principal.actor_id,
                "office_permissions_changed",
                utc_now(),
                {
                    "target_actor_id": command.actor_id,
                    "can_read": command.can_read,
                    "can_edit": command.can_edit,
                    "acl_revision": head.acl_revision,
                },
            )
            revision = head.acl_revision
        return revision
