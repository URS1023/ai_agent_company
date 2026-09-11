"""Storage boundaries: typed snapshots, UTC normalization and rollback-safe sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TypedDict

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import CursorResult, Update, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import (
    AuditEvent,
    Binding,
    BindingWrite,
    BusinessResult,
    Device,
    DeviceCreate,
    JsonObject,
    Run,
    RunSpec,
    RunStatus,
    Scenario,
    canonical_json,
)
from enterprise_platform.application.errors import Conflict, EnterpriseError, InvalidInput, NotFound, PersistenceError

from .models import AuditEventRow, BindingRow, DeviceRow, RunRow

JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
RUN_STATUS: TypeAdapter[RunStatus] = TypeAdapter(RunStatus)
SCENARIO: TypeAdapter[Scenario] = TypeAdapter(Scenario)
ACTIVE_STATES = ("queued", "claimed", "dispatched", "uncertain")
TERMINAL_STATES = ("succeeded", "failed", "cancelled")


class RunChanges(TypedDict, total=False):
    state: str
    dispatch_nonce: str
    dify_run_id: str
    reason_code: str | None
    input_json: str
    input_digest: str
    result_json: str
    result_digest: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc(value: datetime) -> datetime:
    # SQLite omits timezone offsets; all writes to these tables already use UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_page(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise InvalidInput("invalid_pagination")


def validate_revision(revision: int) -> None:
    if type(revision) is not int or revision < 1:
        raise InvalidInput("invalid_revision")


def serialized(model: BaseModel) -> str:
    try:
        return canonical_json(model.model_dump(mode="json"))
    except (ValueError, TypeError):
        raise InvalidInput("invalid_document") from None


def document(value: JsonObject) -> str:
    try:
        return canonical_json(JSON_OBJECT.validate_python(value))
    except (ValueError, TypeError):
        raise InvalidInput("invalid_document") from None


def decode[T: BaseModel](model: type[T], value: str) -> T:
    try:
        return model.model_validate_json(value)
    except ValidationError:
        raise PersistenceError("stored_contract_invalid") from None


def as_device(row: DeviceRow) -> Device:
    data = decode(DeviceCreate, row.device_json)
    return Device(
        **data.model_dump(),
        id=row.device_id,
        workspace_id=row.workspace_id,
        revision=row.revision,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
        deleted_at=utc(row.updated_at) if row.deleted else None,
    )


def as_binding(row: BindingRow) -> Binding:
    data = decode(BindingWrite, row.binding_json)
    return Binding(
        **data.model_dump(),
        id=row.binding_id,
        workspace_id=row.workspace_id,
        device_id=row.device_id,
        scenario=SCENARIO.validate_python(row.scenario),
        revision=row.revision,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
        active_run_id=row.active_run_id,
    )


def as_run(row: RunRow) -> Run:
    return Run(
        id=row.run_id,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        request_key=row.request_key,
        payload_hash=row.payload_hash,
        spec=decode(RunSpec, row.spec_json),
        input_snapshot=JSON_OBJECT.validate_json(row.input_json) if row.input_json is not None else None,
        status=RUN_STATUS.validate_python(row.state),
        dispatch_nonce=row.dispatch_nonce,
        dify_run_id=row.dify_run_id,
        result=decode(BusinessResult, row.result_json) if row.result_json else None,
        result_digest=row.result_digest,
        reason_code=row.reason_code,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
    )


def as_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        sequence=row.sequence,
        workspace_id=row.workspace_id,
        resource_id=row.resource_id,
        run_id=row.run_id,
        actor_id=row.actor_id,
        action=row.event_type,
        data=JSON_OBJECT.validate_json(row.detail_json),
        created_at=utc(row.created_at),
    )


@contextmanager
def transaction(sessions: sessionmaker[Session]) -> Iterator[Session]:
    """Commit exactly one operation or roll back; hide driver text and bound parameters."""
    try:
        with sessions.begin() as session:
            connection = session.connection()
            if connection.dialect.name == "sqlite":
                # SQLite has no row locks; reserve its write transaction before reading.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield session
    except IntegrityError:
        raise Conflict("database_constraint_conflict") from None
    except SQLAlchemyError:
        raise PersistenceError() from None


def device_row(session: Session, workspace_id: str, device_id: str) -> DeviceRow:
    row = session.scalar(
        select(DeviceRow)
        .where(DeviceRow.workspace_id == workspace_id, DeviceRow.device_id == device_id, DeviceRow.deleted.is_(False))
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("device_not_found")
    return row


def binding_row(session: Session, workspace_id: str, binding_id: str) -> BindingRow:
    row = session.scalar(
        select(BindingRow)
        .where(BindingRow.workspace_id == workspace_id, BindingRow.binding_id == binding_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("binding_not_found")
    return row


def run_row(session: Session, workspace_id: str, run_id: str, *, lock: bool = False) -> RunRow:
    statement = (
        select(RunRow)
        .where(RunRow.workspace_id == workspace_id, RunRow.run_id == run_id)
        .execution_options(populate_existing=True)
    )
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise NotFound("run_not_found")
    return row


def cas(session: Session, statement: Update, error: type[EnterpriseError] = Conflict) -> None:
    result = session.execute(statement.execution_options(synchronize_session=False))
    if not isinstance(result, CursorResult) or result.rowcount != 1:
        raise error("concurrent_revision_conflict")


def lock_device(session: Session, workspace_id: str, device_id: str) -> None:
    """Serialize binding/enqueue against logical deletion without editing the revision."""
    cas(
        session,
        update(DeviceRow)
        .where(DeviceRow.workspace_id == workspace_id, DeviceRow.device_id == device_id, DeviceRow.deleted.is_(False))
        .values(revision=DeviceRow.revision),
        NotFound,
    )


def change_run(session: Session, row: RunRow, changes: RunChanges, now: datetime) -> RunRow:
    cas(
        session,
        update(RunRow)
        .where(RunRow.workspace_id == row.workspace_id, RunRow.run_id == row.run_id, RunRow.revision == row.revision)
        .values(**changes, revision=row.revision + 1, updated_at=now),
    )
    return run_row(session, row.workspace_id, row.run_id)


def audit(
    session: Session,
    workspace_id: str,
    resource_type: str,
    resource_id: str,
    actor_id: str,
    action: str,
    now: datetime,
    data: JsonObject | None = None,
    *,
    run_id: str | None = None,
) -> None:
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            resource_type=resource_type,
            resource_id=resource_id,
            run_id=run_id,
            actor_id=actor_id,
            event_type=action,
            detail_json=document(data or {}),
            created_at=now,
        )
    )
