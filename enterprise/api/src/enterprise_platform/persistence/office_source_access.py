"""Check immutable snapshot bindings under live, transaction-scoped source locks.

Caller owns the transaction and file-head lock. Capture must append snapshots;
source permission changes must take these same source locks. No cached decisions,
source refresh, commit, or file access entitlement is granted by this adapter.
PostgreSQL requires READ COMMITTED: a stronger isolation level can retain an old
grant snapshot even after locking a source head that has not itself changed.
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.office_edits import OfficeFileRecord, OfficeGrant

from .office_source_models import OfficeSnapshotRow, OfficeSourceGrantRow, OfficeSourceRow


class SqlAlchemyOfficeSourceAccess:
    def require(self, session: Session, grant: OfficeGrant, record: OfficeFileRecord) -> None:
        if (
            (grant.workspace_id, grant.file_id) != (record.workspace_id, record.content.file_id)
            or grant.action not in {"read", "edit"}
            or len(set(record.source_snapshot_ids)) != len(record.source_snapshot_ids)
        ):
            raise AccessDenied()
        connection = session.connection()
        if connection.dialect.name == "postgresql" and connection.get_isolation_level() != "READ COMMITTED":
            raise PersistenceError("office_source_isolation_invalid")
        snapshots: list[OfficeSnapshotRow] = []
        for snapshot_id in sorted(record.source_snapshot_ids):
            snapshot = session.scalar(
                select(OfficeSnapshotRow)
                .where(
                    OfficeSnapshotRow.workspace_id == grant.workspace_id,
                    OfficeSnapshotRow.snapshot_id == snapshot_id,
                )
                .execution_options(populate_existing=True)
            )
            if snapshot is None:
                raise AccessDenied()
            snapshots.append(snapshot)
        for source_id in sorted({snapshot.source_id for snapshot in snapshots}):
            source = session.scalar(
                select(OfficeSourceRow)
                .where(
                    OfficeSourceRow.workspace_id == grant.workspace_id,
                    OfficeSourceRow.source_id == source_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if source is None or not source.enabled:
                raise AccessDenied()
            permission = session.scalar(
                select(OfficeSourceGrantRow)
                .where(
                    OfficeSourceGrantRow.workspace_id == grant.workspace_id,
                    OfficeSourceGrantRow.source_id == source_id,
                    OfficeSourceGrantRow.actor_id == grant.actor_id,
                )
                .execution_options(populate_existing=True)
            )
            if permission is None or not permission.can_read:
                raise AccessDenied()
        for snapshot in snapshots:
            if hashlib.sha256(snapshot.payload_json.encode("utf-8")).hexdigest() != snapshot.payload_hash:
                raise PersistenceError("office_snapshot_invalid")
