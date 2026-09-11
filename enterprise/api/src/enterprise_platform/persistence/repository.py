"""SQLAlchemy repository for enterprise-owned devices, stable bindings and run history.

Every operation scopes all reads and writes by workspace. Device deletion is logical;
running or queued work must be resolved first. Fixed run specifications remain intact
when a binding is edited, and no operation touches native Dify models or credentials.
Device code and department remain stable while work is outstanding so fresh source
reads cannot silently target a different device scope after their run was enqueued.
"""

from sqlalchemy import JSON, cast, func, or_, select, type_coerce, update

from enterprise_platform.application.contracts import (
    Binding,
    BindingWrite,
    Device,
    DeviceCreate,
    DeviceUpdate,
    Page,
    Scenario,
)
from enterprise_platform.application.errors import Conflict, InvalidInput, InvalidState, NotFound, PersistenceError

from .mapping import (
    ACTIVE_STATES,
    as_binding,
    as_device,
    audit,
    binding_row,
    cas,
    device_row,
    lock_device,
    serialized,
    transaction,
    validate_page,
    validate_revision,
)
from .models import BindingRow, DeviceRow, RunRow
from .runs import RunOperations


def validate_device_scope_change(current: DeviceCreate, replacement: DeviceUpdate, *, has_active_runs: bool) -> None:
    """Presentation fields stay editable; fields used by registered reads stay stable."""
    if has_active_runs and (current.device_code, current.department) != (
        replacement.device_code,
        replacement.department,
    ):
        raise InvalidState("device_scope_has_outstanding_runs")


class SqlAlchemyRepository(RunOperations):
    """A session factory is injected; constructing a repository opens no connection."""

    def create_device(self, workspace_id: str, command: DeviceCreate, *, actor_id: str) -> Device:
        payload = serialized(command)
        now = self._now()
        with transaction(self._sessions) as session:
            row = DeviceRow(
                workspace_id=workspace_id,
                device_id=self._id_factory(),
                device_code=command.device_code,
                revision=1,
                device_json=payload,
                deleted=False,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            audit(session, workspace_id, "device", row.device_id, actor_id, "device.created", now, {"revision": 1})
            return as_device(row)

    def get_device(self, workspace_id: str, device_id: str) -> Device:
        with transaction(self._sessions) as session:
            return as_device(device_row(session, workspace_id, device_id))

    def list_devices(
        self,
        workspace_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        q: str | None = None,
        department: str | None = None,
    ) -> Page[Device]:
        """Apply literal code/name search and exact department filtering before counting or paging."""
        validate_page(offset, limit)
        if any(value is not None and (not isinstance(value, str) or len(value) > 200) for value in (q, department)):
            raise InvalidInput("invalid_device_filter")
        q = q.strip() or None if q is not None else None
        department = department.strip() or None if department is not None else None
        condition = [DeviceRow.workspace_id == workspace_id, DeviceRow.deleted.is_(False)]
        with transaction(self._sessions) as session:
            dialect = session.get_bind().dialect.name
            if dialect not in {"sqlite", "postgresql"}:
                raise PersistenceError("unsupported_database_dialect")
            # Canonical JSON stays text at rest. SQLite JSON_EXTRACT accepts text,
            # whereas PostgreSQL needs a real JSON cast; SQLite's CAST AS JSON is numeric.
            payload = (
                cast(DeviceRow.device_json, JSON)
                if dialect == "postgresql"
                else type_coerce(DeviceRow.device_json, JSON)
            )
            if q:
                condition.append(
                    or_(
                        DeviceRow.device_code.icontains(q, autoescape=True, escape="/"),
                        payload["name"].as_string().icontains(q, autoescape=True, escape="/"),
                    )
                )
            if department:
                condition.append(payload["department"].as_string() == department)
            total = session.scalar(select(func.count()).select_from(DeviceRow).where(*condition)) or 0
            rows = session.scalars(
                select(DeviceRow)
                .where(*condition)
                .order_by(DeviceRow.created_at, DeviceRow.device_id)
                .offset(offset)
                .limit(limit)
            )
            return Page[Device](items=tuple(as_device(row) for row in rows), offset=offset, limit=limit, total=total)

    def update_device(
        self, workspace_id: str, device_id: str, command: DeviceUpdate, *, expected_revision: int, actor_id: str
    ) -> Device:
        validate_revision(expected_revision)
        payload = serialized(command)
        with transaction(self._sessions) as session:
            lock_device(session, workspace_id, device_id)
            current = as_device(device_row(session, workspace_id, device_id))
            outstanding = session.scalar(
                select(RunRow.run_id)
                .where(
                    RunRow.workspace_id == workspace_id, RunRow.device_id == device_id, RunRow.state.in_(ACTIVE_STATES)
                )
                .limit(1)
            )
            validate_device_scope_change(current, command, has_active_runs=outstanding is not None)
            now = self._now()
            cas(
                session,
                update(DeviceRow)
                .where(
                    DeviceRow.workspace_id == workspace_id,
                    DeviceRow.device_id == device_id,
                    DeviceRow.deleted.is_(False),
                    DeviceRow.revision == expected_revision,
                )
                .values(
                    device_json=payload, device_code=command.device_code, revision=expected_revision + 1, updated_at=now
                ),
            )
            audit(
                session,
                workspace_id,
                "device",
                device_id,
                actor_id,
                "device.updated",
                now,
                {"revision": expected_revision + 1},
            )
            return as_device(device_row(session, workspace_id, device_id))

    def delete_device(self, workspace_id: str, device_id: str, *, expected_revision: int, actor_id: str) -> None:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            device_row(session, workspace_id, device_id)
            # Reserve the device revision before checking its work to serialize against enqueue.
            now = self._now()
            cas(
                session,
                update(DeviceRow)
                .where(
                    DeviceRow.workspace_id == workspace_id,
                    DeviceRow.device_id == device_id,
                    DeviceRow.deleted.is_(False),
                    DeviceRow.revision == expected_revision,
                )
                .values(deleted=True, revision=expected_revision + 1, updated_at=now),
            )
            outstanding = session.scalar(
                select(RunRow.run_id)
                .where(
                    RunRow.workspace_id == workspace_id, RunRow.device_id == device_id, RunRow.state.in_(ACTIVE_STATES)
                )
                .limit(1)
            )
            if outstanding is not None:
                raise InvalidState("device_has_outstanding_runs")
            audit(
                session,
                workspace_id,
                "device",
                device_id,
                actor_id,
                "device.deleted",
                now,
                {"revision": expected_revision + 1},
            )

    def put_binding(
        self,
        workspace_id: str,
        device_id: str,
        scenario: Scenario,
        command: BindingWrite,
        *,
        expected_revision: int | None,
        actor_id: str,
    ) -> Binding:
        if expected_revision is not None:
            validate_revision(expected_revision)
        payload = serialized(command)
        with transaction(self._sessions) as session:
            device_row(session, workspace_id, device_id)
            lock_device(session, workspace_id, device_id)
            existing = session.scalar(
                select(BindingRow).where(
                    BindingRow.workspace_id == workspace_id,
                    BindingRow.device_id == device_id,
                    BindingRow.scenario == scenario,
                )
            )
            now = self._now()
            if existing is None:
                if expected_revision is not None:
                    raise NotFound("binding_not_found")
                row = BindingRow(
                    workspace_id=workspace_id,
                    binding_id=self._id_factory(),
                    device_id=device_id,
                    scenario=scenario,
                    revision=1,
                    binding_json=payload,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                audit(
                    session, workspace_id, "binding", row.binding_id, actor_id, "binding.created", now, {"revision": 1}
                )
                return as_binding(row)
            if expected_revision is None:
                raise Conflict("binding_already_exists")
            cas(
                session,
                update(BindingRow)
                .where(
                    BindingRow.workspace_id == workspace_id,
                    BindingRow.binding_id == existing.binding_id,
                    BindingRow.revision == expected_revision,
                )
                .values(binding_json=payload, revision=expected_revision + 1, updated_at=now),
            )
            audit(
                session,
                workspace_id,
                "binding",
                existing.binding_id,
                actor_id,
                "binding.updated",
                now,
                {"revision": expected_revision + 1},
            )
            return as_binding(binding_row(session, workspace_id, existing.binding_id))

    def get_binding(self, workspace_id: str, device_id: str, scenario: Scenario) -> Binding:
        with transaction(self._sessions) as session:
            device_row(session, workspace_id, device_id)
            row = session.scalar(
                select(BindingRow).where(
                    BindingRow.workspace_id == workspace_id,
                    BindingRow.device_id == device_id,
                    BindingRow.scenario == scenario,
                )
            )
            if row is None:
                raise NotFound("binding_not_found")
            return as_binding(row)
