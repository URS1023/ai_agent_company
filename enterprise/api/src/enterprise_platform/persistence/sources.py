"""Transactional source heads and immutable encrypted revisions in the enterprise DB.

No connection testing, key resolution or decryption happens in this repository.
All source/device/audit writes share one transaction. CAS losers append no version.
"""

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Page
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.source_contracts import SourceView
from enterprise_platform.application.source_ports import SealedSource, StoredSource

from .mapping import audit, cas, decode, lock_device, serialized, transaction, validate_page, validate_revision
from .source_models import SourceHeadRow, SourceVersionRow


def source_version_row(source: StoredSource) -> SourceVersionRow:
    view, sealed = source.view, source.sealed
    return SourceVersionRow(
        workspace_id=view.workspace_id,
        source_id=view.source_id,
        revision=view.revision,
        source_revision=view.source_revision,
        read_id=view.read_id,
        read_revision=view.read_revision,
        public_json=serialized(view),
        encryption_key_id=sealed.key_id,
        encryption_nonce=sealed.nonce,
        ciphertext=sealed.ciphertext,
        request_key=source.request_key,
        request_hash=source.request_hash,
        fingerprint_key_id=source.fingerprint_key_id,
    )


def as_source(row: SourceVersionRow) -> StoredSource:
    view = decode(SourceView, row.public_json)
    if (view.workspace_id, view.source_id, view.revision, view.source_revision, view.read_id, view.read_revision) != (
        row.workspace_id,
        row.source_id,
        row.revision,
        row.source_revision,
        row.read_id,
        row.read_revision,
    ):
        raise PersistenceError("stored_source_scope_invalid")
    return StoredSource(
        view,
        SealedSource(row.encryption_key_id, row.encryption_nonce, row.ciphertext),
        row.request_key,
        row.request_hash,
        row.fingerprint_key_id,
    )


def _heads(workspace_id: str) -> Select[tuple[SourceVersionRow]]:
    return (
        select(SourceVersionRow)
        .join(
            SourceHeadRow,
            and_(
                SourceHeadRow.workspace_id == SourceVersionRow.workspace_id,
                SourceHeadRow.source_id == SourceVersionRow.source_id,
                SourceHeadRow.revision == SourceVersionRow.revision,
                SourceHeadRow.read_id == SourceVersionRow.read_id,
            ),
        )
        .where(SourceHeadRow.workspace_id == workspace_id)
    )


class SqlAlchemySourceRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _append(session: Session, source: StoredSource, *, actor_id: str, event: str) -> None:
        for device_id in sorted(source.view.device_ids):
            lock_device(session, source.view.workspace_id, device_id)
        session.add(source_version_row(source))
        audit(
            session,
            source.view.workspace_id,
            "source",
            source.view.source_id,
            actor_id,
            event,
            source.view.updated_at,
            {
                "revision": source.view.revision,
                "source_revision": source.view.source_revision,
                "read_id": source.view.read_id,
                "read_revision": source.view.read_revision,
            },
        )

    def create(self, source: StoredSource, *, actor_id: str) -> StoredSource:
        if source.view.revision != 1:
            raise Conflict("source_initial_revision_required")
        with transaction(self._sessions) as session:
            session.add(
                SourceHeadRow(
                    workspace_id=source.view.workspace_id,
                    source_id=source.view.source_id,
                    read_id=source.view.read_id,
                    revision=1,
                    request_key=source.request_key,
                )
            )
            session.flush()
            self._append(session, source, actor_id=actor_id, event="source_created")
        return source

    def update(self, source: StoredSource, *, expected_revision: int, actor_id: str) -> StoredSource:
        validate_revision(expected_revision)
        if source.view.revision != expected_revision + 1:
            raise Conflict("source_next_revision_required")
        with transaction(self._sessions) as session:
            cas(
                session,
                update(SourceHeadRow)
                .where(
                    SourceHeadRow.workspace_id == source.view.workspace_id,
                    SourceHeadRow.source_id == source.view.source_id,
                    SourceHeadRow.revision == expected_revision,
                    SourceHeadRow.read_id == source.view.read_id,
                    SourceHeadRow.request_key == source.request_key,
                )
                .values(revision=source.view.revision),
            )
            self._append(session, source, actor_id=actor_id, event="source_updated")
        return source

    def get(self, workspace_id: str, source_id: str) -> StoredSource:
        with transaction(self._sessions) as session:
            row = session.scalar(_heads(workspace_id).where(SourceHeadRow.source_id == source_id))
            if row is None:
                raise NotFound("source_not_found")
            return as_source(row)

    def list(self, workspace_id: str, *, offset: int, limit: int) -> Page[SourceView]:
        validate_page(offset, limit)
        with transaction(self._sessions) as session:
            query = _heads(workspace_id)
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(query.order_by(SourceVersionRow.source_id).offset(offset).limit(limit)).all()
            return Page(items=tuple(as_source(row).view for row in rows), total=total, offset=offset, limit=limit)

    def find_request(self, workspace_id: str, request_key: str) -> StoredSource | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(SourceVersionRow).where(
                    SourceVersionRow.workspace_id == workspace_id,
                    SourceVersionRow.request_key == request_key,
                    SourceVersionRow.revision == 1,
                )
            )
            return as_source(row) if row is not None else None

    def find_read(
        self, workspace_id: str, source_id: str, source_revision: str, read_id: str, read_revision: str
    ) -> StoredSource | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(SourceVersionRow).where(
                    SourceVersionRow.workspace_id == workspace_id,
                    SourceVersionRow.source_id == source_id,
                    SourceVersionRow.source_revision == source_revision,
                    SourceVersionRow.read_id == read_id,
                    SourceVersionRow.read_revision == read_revision,
                )
            )
            return as_source(row) if row is not None else None
