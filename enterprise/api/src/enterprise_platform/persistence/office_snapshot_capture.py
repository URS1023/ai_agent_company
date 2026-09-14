"""Append trusted source-reader output; never refresh, overwrite, or commit here.

The caller must obtain FrozenRows from the authorized original source read and
participate in its revocation protocol. Constructing a FrozenRows object does not
prove provenance: this function must not be exposed to model/client submissions.
Source identities/grants must already exist; this primitive creates neither.
Caller owns the transaction and audit, with file-then-sorted-source lock ordering.
"""

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_platform.application.contracts import Principal, canonical_json
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.input_capture import encode_rows
from enterprise_platform.domain.data_sources import FrozenRows

from .office_source_models import OfficeSnapshotRow, OfficeSourceGrantRow, OfficeSourceRow
from .office_source_transaction import require_source_transaction


def capture_office_snapshot(session: Session, principal: Principal, snapshot_id: UUID, rows: FrozenRows) -> str:
    if principal.workspace_id != rows.source.workspace_id:
        raise AccessDenied()
    if not isinstance(snapshot_id, UUID):
        raise InvalidInput("office_snapshot_id_invalid")
    payload = canonical_json(encode_rows(rows))
    encoded = payload.encode("utf-8")
    if len(encoded) > 16 * 1024 * 1024:
        raise InvalidInput("input_snapshot_size_limit")
    digest = hashlib.sha256(encoded).hexdigest()
    require_source_transaction(session)
    source = session.scalar(
        select(OfficeSourceRow)
        .where(
            OfficeSourceRow.workspace_id == principal.workspace_id,
            OfficeSourceRow.source_id == rows.source.source_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or not source.enabled:
        raise AccessDenied()
    permission = session.scalar(
        select(OfficeSourceGrantRow)
        .where(
            OfficeSourceGrantRow.workspace_id == principal.workspace_id,
            OfficeSourceGrantRow.source_id == rows.source.source_id,
            OfficeSourceGrantRow.actor_id == principal.actor_id,
        )
        .execution_options(populate_existing=True)
    )
    if permission is None or not permission.can_read:
        raise AccessDenied()
    identifier = str(snapshot_id)
    existing = session.scalar(
        select(OfficeSnapshotRow)
        .where(
            OfficeSnapshotRow.workspace_id == principal.workspace_id,
            OfficeSnapshotRow.snapshot_id == identifier,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if (existing.source_id, existing.source_revision, existing.payload_json, existing.payload_hash) != (
            rows.source.source_id,
            rows.source.revision,
            payload,
            digest,
        ):
            raise Conflict("office_snapshot_reused")
        return identifier
    session.add(
        OfficeSnapshotRow(
            workspace_id=principal.workspace_id,
            snapshot_id=identifier,
            source_id=rows.source.source_id,
            source_revision=rows.source.revision,
            payload_json=payload,
            payload_hash=digest,
        )
    )
    session.flush()
    return identifier
