"""Internal, transaction-scoped source revocation; never an authorization endpoint.

The trusted caller must authorize the originating source management operation and
write its audit event in the SAME transaction. This primitive only removes access;
it neither grants access, creates source identities, commits nor calls native APIs.
Lock file heads first when needed, then source heads in sorted order. SQLite callers
must reserve writes with BEGIN IMMEDIATE; PostgreSQL requires READ COMMITTED.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_platform.application.errors import AccessDenied, Conflict

from .office_source_models import OfficeSourceGrantRow, OfficeSourceRow
from .office_source_transaction import require_source_transaction


def revoke_source_reader(
    session: Session, *, workspace_id: str, source_id: str, actor_id: str, expected_revision: int
) -> int:
    if type(expected_revision) is not int or not 1 <= expected_revision <= 9223372036854775807:
        raise Conflict("office_source_revision_conflict")
    require_source_transaction(session)
    head = session.scalar(
        select(OfficeSourceRow)
        .where(OfficeSourceRow.workspace_id == workspace_id, OfficeSourceRow.source_id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if head is None:
        raise AccessDenied()
    if head.acl_revision != expected_revision:
        raise Conflict("office_source_revision_conflict")
    permission = session.scalar(
        select(OfficeSourceGrantRow)
        .where(
            OfficeSourceGrantRow.workspace_id == workspace_id,
            OfficeSourceGrantRow.source_id == source_id,
            OfficeSourceGrantRow.actor_id == actor_id,
        )
        .execution_options(populate_existing=True)
    )
    if permission is None or not permission.can_read:
        return head.acl_revision
    if head.acl_revision == 9223372036854775807:
        raise Conflict("office_source_revision_exhausted")
    permission.can_read = False
    head.acl_revision += 1
    session.flush()
    return head.acl_revision
