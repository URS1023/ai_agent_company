"""Reject drift between schedule authority JSON and indexed/mirrored columns."""

from datetime import datetime

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.scheduling import IntervalSchedule

from .mapping import decode, serialized, utc
from .schedule_models import ScheduleRow


def schedule_row(value: IntervalSchedule, created_at: datetime, updated_at: datetime) -> ScheduleRow:
    value = IntervalSchedule.model_validate(value.model_dump())
    if created_at.utcoffset() is None or updated_at.utcoffset() is None or updated_at < created_at:
        raise ValueError("Invalid schedule timestamps")
    return ScheduleRow(
        workspace_id=value.workspace_id,
        schedule_id=value.id,
        binding_id=value.binding_id,
        binding_revision=value.binding_revision,
        device_id=value.device_id,
        scenario=value.scenario,
        service_actor_id=value.service_actor_id,
        revision=value.revision,
        enabled=value.enabled,
        next_due_at=value.next_due_at,
        public_json=serialized(value),
        created_at=created_at,
        updated_at=updated_at,
    )


def as_schedule(row: ScheduleRow) -> IntervalSchedule:
    value = decode(IntervalSchedule, row.public_json)
    try:
        expected = schedule_row(value, utc(row.created_at), utc(row.updated_at))
        for column in ScheduleRow.__table__.columns:
            key = column.key
            if key not in {"public_json", "next_due_at", "created_at", "updated_at"}:
                if getattr(row, key) != getattr(expected, key):
                    raise PersistenceError("stored_schedule_scope_invalid")
        if utc(row.next_due_at) != value.next_due_at:
            raise PersistenceError("stored_schedule_cursor_invalid")
        return value
    except (ValueError, TypeError, AttributeError):
        raise PersistenceError("stored_schedule_invalid") from None
