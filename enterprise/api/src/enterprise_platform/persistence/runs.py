"""Durable run queue and evidence transitions, with no workflow dispatch side effects.

Only a successful first claim permits its caller to dispatch. Repeating a claim is an
error, including the same nonce. An uncertain run retains its lane throughout
reconciliation until a terminal transition; this repository never retries external work.
"""

import re
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.adapters.dify_workflows import validate_native_run_id
from enterprise_platform.application.contracts import (
    AuditEvent,
    BusinessResult,
    JsonObject,
    Page,
    Run,
    RunSpec,
    Scenario,
    canonical_hash,
)
from enterprise_platform.application.errors import Conflict, InvalidInput, InvalidState

from .mapping import (
    TERMINAL_STATES,
    RunChanges,
    as_binding,
    as_event,
    as_run,
    audit,
    binding_row,
    cas,
    change_run,
    decode,
    device_row,
    document,
    lock_device,
    run_row,
    serialized,
    transaction,
    utc,
    utc_now,
    validate_page,
)
from .models import AuditEventRow, BindingRow, RunRow


def verify_recompute(spec: RunSpec, input_json: str | None, previous: RunRow) -> None:
    """Re-evaluation changes the decision policy, never the historical read or its input."""
    if input_json is None or previous.input_json is None:
        raise InvalidState("recompute_evidence_required")
    scope = {"device_id", "scenario", "source_id", "source_revision", "read_id", "read_revision", "parameters"}
    old_spec = decode(RunSpec, previous.spec_json)
    if (
        canonical_hash(old_spec.model_dump(mode="json", include=scope))
        != canonical_hash(spec.model_dump(mode="json", include=scope))
        or previous.input_json != input_json
    ):
        raise Conflict("recompute_snapshot_conflict")


def dispatched_changes(state: str, stored_id: str | None, native_id: str) -> RunChanges | None:
    """Reconciliation may restore the same in-flight execution, never revive terminal work."""
    if stored_id is not None:
        if stored_id != native_id:
            raise Conflict("dify_run_identity_conflict")
        if state != "uncertain":
            return None
    elif state != "claimed":
        raise InvalidState("run_not_claimed")
    return {"state": "dispatched", "dify_run_id": native_id, "reason_code": None}


def uncertain_changes(state: str, stored_reason: str | None, reason: str) -> RunChanges | None:
    """Record a new uncertainty cause without claiming again or releasing its lane."""
    if state == "uncertain" and stored_reason == reason:
        return None
    if state not in {"claimed", "dispatched", "uncertain"}:
        raise InvalidState("run_not_in_flight")
    return {"state": "uncertain", "reason_code": reason}


class RunOperations:
    """Session-factory injection and atomic queue operations shared by the repository."""

    _sessions: sessionmaker[Session]
    _clock: Callable[[], datetime]
    _id_factory: Callable[[], str]

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._sessions, self._clock, self._id_factory = sessions, clock, id_factory

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        return utc(now)

    @staticmethod
    def _nonce(row: RunRow, nonce: str) -> None:
        if not nonce or nonce != row.dispatch_nonce:
            raise Conflict("dispatch_nonce_mismatch")

    @staticmethod
    def _reason(reason: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", reason) is None:
            raise InvalidInput("invalid_reason_code")

    def enqueue_run_in_session(
        self,
        session: Session,
        workspace_id: str,
        request_key: str,
        payload_hash: str,
        spec: RunSpec,
        input_snapshot: JsonObject | None,
        *,
        actor_id: str,
    ) -> Run:
        """Join an owned transaction; caller handles commit, rollback and conflict recovery."""
        spec_json = serialized(spec)
        input_json = document(input_snapshot) if input_snapshot is not None else None
        actual_hash = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": input_snapshot})
        if payload_hash != actual_hash:
            raise InvalidInput("payload_hash_mismatch")
        existing = session.scalar(
            select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.request_key == request_key)
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise Conflict("request_key_payload_conflict")
            return as_run(existing)
        lock_device(session, workspace_id, spec.device_id)
        binding = binding_row(session, workspace_id, spec.binding_id)
        device_row(session, workspace_id, spec.device_id)
        expected = RunSpec.from_binding(as_binding(binding))
        if expected.model_dump(exclude={"parameters", "recompute_of"}) != spec.model_dump(
            exclude={"parameters", "recompute_of"}
        ):
            raise Conflict("binding_snapshot_changed")
        if spec.recompute_of is not None:
            verify_recompute(spec, input_json, run_row(session, workspace_id, spec.recompute_of))
        now = self._now()
        row = RunRow(
            workspace_id=workspace_id,
            run_id=self._id_factory(),
            actor_id=actor_id,
            binding_id=spec.binding_id,
            device_id=spec.device_id,
            scenario=spec.scenario,
            request_key=request_key,
            payload_hash=payload_hash,
            spec_json=spec_json,
            input_json=input_json,
            input_digest=canonical_hash(input_snapshot) if input_snapshot is not None else None,
            state="queued",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        audit(session, workspace_id, "run", row.run_id, actor_id, "run.queued", now, run_id=row.run_id)
        return as_run(row)

    def enqueue_run(
        self,
        workspace_id: str,
        request_key: str,
        payload_hash: str,
        spec: RunSpec,
        input_snapshot: JsonObject | None,
        *,
        actor_id: str,
    ) -> Run:
        """Persist one frozen request; identical workspace request keys reuse its run."""
        actual_hash = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": input_snapshot})
        if payload_hash != actual_hash:
            raise InvalidInput("payload_hash_mismatch")
        try:
            with transaction(self._sessions) as session:
                return self.enqueue_run_in_session(
                    session,
                    workspace_id,
                    request_key,
                    payload_hash,
                    spec,
                    input_snapshot,
                    actor_id=actor_id,
                )
        except Conflict:
            # A concurrent identical insert may have won its unique request key.
            with transaction(self._sessions) as session:
                existing = session.scalar(
                    select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.request_key == request_key)
                )
                if existing is not None and existing.payload_hash == payload_hash:
                    return as_run(existing)
            raise

    def get_run(self, workspace_id: str, run_id: str) -> Run:
        with transaction(self._sessions) as session:
            return as_run(run_row(session, workspace_id, run_id))

    def find_run_by_request_key(self, workspace_id: str, request_key: str) -> Run | None:
        """Read the authoritative original request, including terminal/deleted-device history."""
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.request_key == request_key)
            )
            return as_run(row) if row is not None else None

    def list_runs(
        self, workspace_id: str, device_id: str, scenario: Scenario, *, offset: int = 0, limit: int = 50
    ) -> Page[Run]:
        validate_page(offset, limit)
        condition = (RunRow.workspace_id == workspace_id, RunRow.device_id == device_id, RunRow.scenario == scenario)
        with transaction(self._sessions) as session:
            total = session.scalar(select(func.count()).select_from(RunRow).where(*condition)) or 0
            rows = session.scalars(
                select(RunRow).where(*condition).order_by(RunRow.sequence.desc()).offset(offset).limit(limit)
            )
            return Page[Run](items=tuple(as_run(row) for row in rows), offset=offset, limit=limit, total=total)

    def claim_run(self, workspace_id: str, run_id: str, dispatch_nonce: str, *, actor_id: str) -> Run:
        """Atomically reserve the device/scenario lane and its oldest queued request."""
        if not dispatch_nonce.strip() or dispatch_nonce != dispatch_nonce.strip() or len(dispatch_nonce) > 128:
            raise InvalidInput("invalid_dispatch_nonce")
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            if row.state != "queued":
                raise InvalidState("run_already_claimed")
            device_row(session, workspace_id, row.device_id)
            cas(
                session,
                update(BindingRow)
                .where(
                    BindingRow.workspace_id == workspace_id,
                    BindingRow.binding_id == row.binding_id,
                    BindingRow.active_run_id.is_(None),
                )
                .values(active_run_id=run_id),
                InvalidState,
            )
            first = session.scalar(
                select(RunRow.run_id)
                .where(
                    RunRow.workspace_id == workspace_id, RunRow.binding_id == row.binding_id, RunRow.state == "queued"
                )
                .order_by(RunRow.sequence)
                .limit(1)
            )
            if first != run_id:
                raise InvalidState("run_is_not_queue_head")
            now = self._now()
            row = change_run(session, row, {"state": "claimed", "dispatch_nonce": dispatch_nonce}, now)
            audit(session, workspace_id, "run", run_id, actor_id, "run.claimed", now, run_id=run_id)
            return as_run(row)

    def mark_dispatched(self, workspace_id: str, run_id: str, nonce: str, dify_run_id: str, *, actor_id: str) -> Run:
        try:
            validate_native_run_id(dify_run_id)
        except ValidationError:
            raise InvalidInput("invalid_dify_run_id") from None
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            self._nonce(row, nonce)
            changes = dispatched_changes(row.state, row.dify_run_id, dify_run_id)
            if changes is None:
                return as_run(row)
            now = self._now()
            row = change_run(session, row, changes, now)
            audit(
                session,
                workspace_id,
                "run",
                run_id,
                actor_id,
                "run.dispatched",
                now,
                {"dify_run_id": dify_run_id},
                run_id=run_id,
            )
            return as_run(row)

    def mark_uncertain(self, workspace_id: str, run_id: str, nonce: str, reason_code: str, *, actor_id: str) -> Run:
        self._reason(reason_code)
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            self._nonce(row, nonce)
            changes = uncertain_changes(row.state, row.reason_code, reason_code)
            if changes is None:
                return as_run(row)
            now = self._now()
            row = change_run(session, row, changes, now)
            audit(
                session,
                workspace_id,
                "run",
                run_id,
                actor_id,
                "run.uncertain",
                now,
                {"reason_code": reason_code},
                run_id=run_id,
            )
            return as_run(row)

    def capture_input(self, workspace_id: str, run_id: str, nonce: str, snapshot: JsonObject, *, actor_id: str) -> Run:
        snapshot_json, digest = document(snapshot), canonical_hash(snapshot)
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            self._nonce(row, nonce)
            if row.input_json is not None:
                if row.input_digest != digest or row.input_json != snapshot_json:
                    raise Conflict("input_snapshot_conflict")
                return as_run(row)
            if row.state not in {"claimed", "dispatched", "uncertain"}:
                raise InvalidState("run_not_in_flight")
            now = self._now()
            row = change_run(session, row, {"input_json": snapshot_json, "input_digest": digest}, now)
            audit(
                session,
                workspace_id,
                "run",
                run_id,
                actor_id,
                "run.input_captured",
                now,
                {"input_digest": digest},
                run_id=run_id,
            )
            return as_run(row)

    @staticmethod
    def _release_lane(session: Session, row: RunRow) -> None:
        cas(
            session,
            update(BindingRow)
            .where(
                BindingRow.workspace_id == row.workspace_id,
                BindingRow.binding_id == row.binding_id,
                BindingRow.active_run_id == row.run_id,
            )
            .values(active_run_id=None),
            InvalidState,
        )

    def complete_run(
        self, workspace_id: str, run_id: str, nonce: str, result: BusinessResult, result_digest: str, *, actor_id: str
    ) -> Run:
        """Accept a verified business result only after source evidence has been captured."""
        result_json = serialized(result)
        if result_digest != canonical_hash(result.model_dump(mode="json")):
            raise InvalidInput("result_digest_mismatch")
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            self._nonce(row, nonce)
            if result.scenario != row.scenario:
                raise InvalidInput("result_scenario_mismatch")
            if row.state in TERMINAL_STATES:
                if row.state == "succeeded" and row.result_digest == result_digest and row.result_json == result_json:
                    return as_run(row)
                raise Conflict("terminal_result_conflict")
            if row.state not in {"claimed", "dispatched", "uncertain"} or row.input_json is None:
                raise InvalidState("input_evidence_required")
            now = self._now()
            row = change_run(
                session,
                row,
                {"state": "succeeded", "result_json": result_json, "result_digest": result_digest, "reason_code": None},
                now,
            )
            self._release_lane(session, row)
            audit(
                session,
                workspace_id,
                "run",
                run_id,
                actor_id,
                "run.succeeded",
                now,
                {"result_digest": result_digest},
                run_id=run_id,
            )
            return as_run(row)

    def fail_run(self, workspace_id: str, run_id: str, nonce: str, reason_code: str, *, actor_id: str) -> Run:
        """An explicit execution failure releases its lane without a business report."""
        self._reason(reason_code)
        with transaction(self._sessions) as session:
            row = run_row(session, workspace_id, run_id, lock=True)
            self._nonce(row, nonce)
            if row.state in TERMINAL_STATES:
                if row.state == "failed" and row.reason_code == reason_code:
                    return as_run(row)
                raise Conflict("terminal_result_conflict")
            if row.state not in {"claimed", "dispatched", "uncertain"}:
                raise InvalidState("run_not_in_flight")
            now = self._now()
            row = change_run(session, row, {"state": "failed", "reason_code": reason_code}, now)
            self._release_lane(session, row)
            audit(
                session,
                workspace_id,
                "run",
                run_id,
                actor_id,
                "run.failed",
                now,
                {"reason_code": reason_code},
                run_id=run_id,
            )
            return as_run(row)

    def list_events(self, workspace_id: str, run_id: str, *, after_sequence: int = 0) -> tuple[AuditEvent, ...]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise InvalidInput("invalid_event_cursor")
        with transaction(self._sessions) as session:
            run_row(session, workspace_id, run_id)
            rows = session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.workspace_id == workspace_id,
                    AuditEventRow.run_id == run_id,
                    AuditEventRow.sequence > after_sequence,
                )
                .order_by(AuditEventRow.sequence)
            )
            return tuple(as_event(row) for row in rows)
