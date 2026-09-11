from datetime import timedelta
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy import CursorResult
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from test_workflow_provisioning_contracts import APP, NONCE, NOW, WS, commands, initial, setup

from enterprise_platform.application.errors import Conflict, InvalidInput, PersistenceError
from enterprise_platform.application.workflow_provisioning_contracts import PhaseOutcome, claim_provisioning
from enterprise_platform.application.workflow_setup_ports import StoredSetup
from enterprise_platform.persistence.workflow_provisioning import (
    SqlAlchemyWorkflowProvisioningRepository,
    as_provisioning,
    provisioning_row,
)
from enterprise_platform.persistence.workflow_provisioning_models import ProvisioningBase
from enterprise_platform.persistence.workflow_setups import setup_row


def repository_with(value):
    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    changed = create_autospec(CursorResult, instance=True)
    changed.rowcount = 1
    session.execute.return_value = changed
    session.scalar.return_value = provisioning_row(value)
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    return SqlAlchemyWorkflowProvisioningRepository(sessions), session, sessions


def linked():
    return setup_row(StoredSetup(setup(), "creator", "setup-request", "a" * 64))


def test_metadata_and_roundtrip():
    assert set(ProvisioningBase.metadata.tables) == {"enterprise_workflow_provisioning"}
    sql = str(CreateTable(next(iter(ProvisioningBase.metadata.tables.values()))).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (workspace_id, request_key)" in sql
    assert as_provisioning(provisioning_row(initial())) == initial()


@pytest.mark.parametrize(
    "key,value",
    [
        ("workspace_id", APP),
        ("actor_id", "other"),
        ("setup_revision", 4),
        ("source_revision", "other"),
        ("claim_nonce", NONCE),
        ("phase_count", 1),
        ("request_hash", "bad"),
        ("public_json", "{}"),
        ("updated_at", NOW + timedelta(seconds=1)),
    ],
)
def test_corrupt_mirror_rejected(key, value):
    row = provisioning_row(initial())
    setattr(row, key, value)
    with pytest.raises(PersistenceError):
        as_provisioning(row)


def test_create_locks_validates_and_audits():
    repo, session, _ = repository_with(initial())
    session.scalar.side_effect = [linked(), None]
    assert repo.create(initial(), actor_id="actor") == initial()
    assert session.add.call_count == 2
    assert "enterprise_devices" in str(session.execute.call_args.args[0])


def test_create_replay_and_conflicting_actor():
    repo, session, _ = repository_with(initial())
    session.scalar.side_effect = [linked(), provisioning_row(initial())]
    assert repo.create(initial(), actor_id="actor") == initial()
    session.add.assert_not_called()
    other = initial().model_copy(update={"view": initial().view.model_copy(update={"actor_id": "other"})})
    with pytest.raises(Conflict):
        repo._replay(initial(), other)


def test_snapshot_drift_prevents_create():
    repo, session, _ = repository_with(initial())
    changed = setup().model_copy(update={"revision": 4})
    session.scalar.return_value = setup_row(StoredSetup(changed, "creator", "key", "a" * 64))
    with pytest.raises(Conflict):
        repo.create(initial(), actor_id="actor")
    session.add.assert_not_called()


def test_claim_fences_scope_actor_revision_and_device():
    repo, session, _ = repository_with(initial())
    session.scalar.side_effect = [provisioning_row(initial()), linked()]
    result = repo.claim(
        WS, initial().view.id, command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor"
    )
    assert result.claim_nonce == NONCE and result.view.revision == 2
    assert session.execute.call_count == 2
    sql = str(session.execute.call_args.args[0])
    for field in ["workspace_id", "provisioning_id", "revision", "actor_id", "claim_nonce"]:
        assert field in sql.split("WHERE")[1]


def test_finish_owned_outcome_without_live_device():
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    repo, session, _ = repository_with(value)
    result = repo.finish(
        WS,
        value.view.id,
        outcome=PhaseOutcome(state="uncertain", reason_code="transport_unknown"),
        nonce=NONCE,
        expected_revision=2,
        actor_id="actor",
    )
    assert result.view.state == "uncertain" and result.claim_nonce is None
    assert session.execute.call_count == 1
    assert "enterprise_devices" not in str(session.execute.call_args.args[0])


def test_get_and_paginated_history_are_scoped():
    repo, session, _ = repository_with(initial())
    assert repo.get(WS, initial().view.id) == initial()
    session.scalar.return_value = 1
    session.scalars.return_value.all.return_value = [provisioning_row(initial())]
    page = repo.list(WS, setup_id="setup", offset=20, limit=10)
    assert page.total == 1 and page.offset == 20 and page.items == (initial().view,)
    params = session.scalars.call_args.args[0].compile().params
    assert {WS, "setup", 20, 10} == set(params.values())


def test_nonce_check_explicitly_rejects_null_phase_with_nonce():
    table = next(iter(ProvisioningBase.metadata.tables.values()))
    check = next(c for c in table.constraints if c.name == "ck_workflow_provisioning_nonce")
    assert "phase_state IS NOT NULL AND phase_state = 'claimed'" in str(check.sqltext)


@pytest.mark.parametrize(
    "changes", [{"nonce": APP}, {"nonce": "非ASCII"}, {"actor_id": "other"}, {"expected_revision": 1}]
)
def test_finish_fencing_rejects_before_write(changes):
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    repo, session, _ = repository_with(value)
    kwargs = (
        dict(
            outcome=PhaseOutcome(state="uncertain", reason_code="unknown"),
            nonce=NONCE,
            expected_revision=2,
            actor_id="actor",
        )
        | changes
    )
    from enterprise_platform.application.errors import AccessDenied

    with pytest.raises((Conflict, AccessDenied)):
        repo.finish(WS, value.view.id, **kwargs)
    session.execute.assert_not_called()
    session.add.assert_not_called()


def test_claim_cas_loss_rolls_back_without_audit():
    repo, session, sessions = repository_with(initial())
    session.scalar.side_effect = [provisioning_row(initial()), linked()]
    success = create_autospec(CursorResult, instance=True)
    success.rowcount = 1
    loser = create_autospec(CursorResult, instance=True)
    loser.rowcount = 0
    session.execute.side_effect = [success, loser]
    with pytest.raises(Conflict):
        repo.claim(WS, initial().view.id, command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor")
    session.add.assert_not_called()
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict


def test_create_constraint_race_replays_only_matching_actor_hash():
    from sqlalchemy.exc import IntegrityError

    repo, session, sessions = repository_with(initial())
    session.scalar.side_effect = [linked(), None, provisioning_row(initial())]
    session.flush.side_effect = IntegrityError("private", {}, Exception("private"))
    assert repo.create(initial(), actor_id="actor") == initial()
    assert sessions.begin.call_count == 2
    assert session.add.call_count == 1


def test_deleted_device_create_stops_before_snapshot_or_insert():
    from enterprise_platform.application.errors import NotFound

    repo, session, _ = repository_with(initial())
    session.execute.return_value.rowcount = 0
    with pytest.raises(NotFound):
        repo.create(initial(), actor_id="actor")
    session.scalar.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("offset,limit", [(-1, 20), (0, 101), (True, 20)])
def test_invalid_page_never_opens_transaction(offset, limit):
    from enterprise_platform.application.errors import InvalidInput

    repo, _, sessions = repository_with(initial())
    with pytest.raises(InvalidInput):
        repo.list(WS, offset=offset, limit=limit)
    sessions.begin.assert_not_called()


@pytest.mark.parametrize("actor_id", [None, "actor", "a" * 128])
@pytest.mark.parametrize("setup_id", [None, "setup"])
def test_list_actor_scope_precedes_count_and_pagination(actor_id, setup_id):
    repo, session, _ = repository_with(initial())
    session.scalar.return_value = 0
    session.scalars.return_value.all.return_value = []

    page = repo.list(WS, setup_id=setup_id, actor_id=actor_id, offset=20, limit=10)

    assert page.total == 0 and page.items == () and page.offset == 20 and page.limit == 10
    count = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    rows = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    scope = {"workspace_id_1": WS}
    if setup_id is not None:
        scope["setup_id_1"] = setup_id
    if actor_id is not None:
        scope["actor_id_1"] = actor_id
    assert count.params == scope
    assert rows.params == {**scope, "param_1": 10, "param_2": 20}
    for statement in (count, rows):
        where = str(statement).split("WHERE", 1)[1]
        for field in ("workspace_id", "setup_id", "actor_id"):
            assert (f"enterprise_workflow_provisioning.{field} =" in where) == (f"{field}_1" in scope)
    assert "LIMIT" not in str(count) and "OFFSET" not in str(count)


@pytest.mark.parametrize("actor_id", ["", " ", " actor", "actor ", "a" * 129, 1, True])
def test_list_rejects_invalid_actor_before_session(actor_id):
    repo, session, sessions = repository_with(initial())

    with pytest.raises(InvalidInput, match="invalid_provisioning_actor"):
        repo.list(WS, actor_id=actor_id)

    sessions.begin.assert_not_called()
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
