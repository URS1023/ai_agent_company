from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy import CursorResult
from sqlalchemy.orm import Session
from test_business_service import binding
from test_scheduling import START

from enterprise_platform.application.contracts import BindingWrite, RunSpec, canonical_hash
from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.persistence.mapping import serialized
from enterprise_platform.persistence.models import BindingRow, DeviceRow, RunRow
from enterprise_platform.persistence.runs import RunOperations


def fixture():
    value = binding()
    payload = BindingWrite.model_validate(
        value.model_dump(
            exclude={
                "id",
                "workspace_id",
                "device_id",
                "scenario",
                "revision",
                "created_at",
                "updated_at",
                "active_run_id",
            }
        )
    )
    row = BindingRow(
        workspace_id="w",
        binding_id="b",
        device_id="d",
        scenario="alert",
        revision=2,
        binding_json=serialized(payload),
        active_run_id=None,
        created_at=START,
        updated_at=START,
    )
    session = create_autospec(Session, instance=True)
    session.scalar.side_effect = [None, row, DeviceRow(workspace_id="w", device_id="d", deleted=False)]
    result = create_autospec(CursorResult, instance=True)
    result.rowcount = 1
    session.execute.return_value = result
    sessions = MagicMock()
    ops = RunOperations(sessions, clock=lambda: START, id_factory=lambda: "run-1")
    spec = RunSpec.from_binding(value)
    digest = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": None})
    return ops, sessions, session, row, spec, digest


def test_enqueue_can_join_callers_transaction_without_opening_or_committing_another() -> None:
    ops, sessions, session, _, spec, digest = fixture()
    value = ops.enqueue_run_in_session(session, "w", "key", digest, spec, None, actor_id="service")
    assert value.status == "queued" and value.actor_id == "service"
    assert value.input_snapshot is None
    assert isinstance(session.add.call_args_list[0].args[0], RunRow)
    assert session.add.call_count == 2
    sessions.begin.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_invalid_hash_or_changed_binding_does_not_insert_a_run() -> None:
    ops, _, session, row, spec, digest = fixture()
    with pytest.raises(InvalidInput):
        ops.enqueue_run_in_session(session, "w", "key", "bad", spec, None, actor_id="service")
    session.scalar.assert_not_called()
    row.revision += 1
    with pytest.raises(Conflict):
        ops.enqueue_run_in_session(session, "w", "key", digest, spec, None, actor_id="service")
    session.add.assert_not_called()


def test_same_request_key_reads_existing_run_without_duplicate_insertion() -> None:
    ops, _, session, _, spec, digest = fixture()
    first = ops.enqueue_run_in_session(session, "w", "key", digest, spec, None, actor_id="service")
    stored = session.add.call_args_list[0].args[0]
    session.scalar.side_effect = None
    session.scalar.return_value = stored
    session.add.reset_mock()
    second = ops.enqueue_run_in_session(session, "w", "key", digest, spec, None, actor_id="service")
    assert second == first
    session.add.assert_not_called()
