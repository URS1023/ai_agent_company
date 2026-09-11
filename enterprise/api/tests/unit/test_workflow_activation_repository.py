from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy import CursorResult
from sqlalchemy.orm import Session
from test_assessments import alert_spec
from test_source_persistence import stored as stored_source
from test_workflow_activation_contracts import create, profile
from test_workflow_enrollment_contracts import SECRET_REF

from enterprise_platform.application.assessments import ImmutableSpecificationCatalog
from enterprise_platform.application.contracts import BindingWrite
from enterprise_platform.application.errors import Conflict, PersistenceError
from enterprise_platform.application.workflow_credential_ports import SealedCredential
from enterprise_platform.application.workflow_enrollment_contracts import StoredEnrollment
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry
from enterprise_platform.persistence.mapping import serialized
from enterprise_platform.persistence.models import BindingRow
from enterprise_platform.persistence.source_models import SourceHeadRow
from enterprise_platform.persistence.sources import source_version_row
from enterprise_platform.persistence.workflow_activation_mapping import activation_row
from enterprise_platform.persistence.workflow_activation_models import WorkflowActivationRow
from enterprise_platform.persistence.workflow_activation_repository import SqlAlchemyWorkflowActivationRepository
from enterprise_platform.persistence.workflow_credentials import credential_row
from enterprise_platform.persistence.workflow_enrollment_mapping import enrollment_row


def fixture(update_binding=False):
    value = create()
    if update_binding:
        enrollment = value.enrollment.model_copy(
            update={"provisioning": value.enrollment.provisioning.model_copy(update={"expected_binding_revision": 1})}
        )
        value = value.model_copy(
            update={"enrollment": enrollment, "binding": value.binding.model_copy(update={"revision": 2})}
        )
    p = value.enrollment.provisioning
    source = stored_source()
    source = replace(
        source,
        view=source.view.model_copy(
            update={
                "workspace_id": p.workspace_id,
                "source_id": p.source_id,
                "source_revision": p.source_revision,
                "read_id": p.read_id,
                "read_revision": p.read_revision,
                "revision": p.expected_source_revision,
                "device_ids": (p.device_id,),
            }
        ),
    )
    rows = [
        enrollment_row(StoredEnrollment(view=value.enrollment, secret_ref=SECRET_REF)),
        None,
        credential_row(value.enrollment.credential, SealedCredential("key", "nonce", "ciphertext")),
        SourceHeadRow(
            workspace_id=p.workspace_id, source_id=p.source_id, read_id=p.read_id, revision=p.expected_source_revision
        ),
        source_version_row(source),
        None,
        None,
    ]
    if update_binding:
        payload = value.binding.model_dump(
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
        rows[-1] = BindingRow(
            workspace_id=p.workspace_id,
            device_id=p.device_id,
            scenario=p.scenario,
            binding_id=value.binding.id,
            revision=1,
            binding_json=serialized(BindingWrite.model_validate(payload)),
            active_run_id=None,
            created_at=value.binding.created_at,
            updated_at=value.binding.created_at,
        )
    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    result = create_autospec(CursorResult, instance=True)
    result.rowcount = 1
    session.execute.return_value = result
    session.scalar.side_effect = rows
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    spec = alert_spec().model_copy(
        update={"workspace_id": p.workspace_id, "specification_revision": value.binding.specification_revision}
    )
    repo = SqlAlchemyWorkflowActivationRepository(
        sessions, WorkflowPluginProfileRegistry((profile(),)), ImmutableSpecificationCatalog((spec,))
    )
    return repo, session, sessions, rows, value


def test_binding_and_activation_share_transaction_and_audit():
    repo, session, sessions, _, value = fixture()
    assert repo.create(value, actor_id="actor") == value
    added = [call.args[0] for call in session.add.call_args_list]
    assert sum(isinstance(row, BindingRow) for row in added) == 1
    assert sum(isinstance(row, WorkflowActivationRow) for row in added) == 1
    assert len(added) == 4
    assert sessions.begin.call_count == 1
    session.commit.assert_not_called()


@pytest.mark.parametrize("index", [0, 2, 3, 4])
def test_missing_dependency_writes_neither_binding_nor_activation(index):
    repo, session, _, rows, value = fixture()
    rows[index] = None
    session.scalar.side_effect = rows
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_queued_or_running_work_blocks_activation():
    repo, session, _, rows, value = fixture()
    rows[-2] = "outstanding-run"
    session.scalar.side_effect = rows
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_audit_error_aborts_the_whole_transaction(monkeypatch):
    import enterprise_platform.persistence.workflow_activation_repository as module

    repo, session, sessions, _, value = fixture()
    monkeypatch.setattr(module, "audit", MagicMock(side_effect=PersistenceError("audit_failed")))
    with pytest.raises(PersistenceError):
        repo.create(value, actor_id="actor")
    assert sessions.begin.return_value.__exit__.call_args.args[0] is PersistenceError
    session.commit.assert_not_called()


def test_existing_binding_is_updated_with_revision_cas():
    repo, session, _, _, value = fixture(update_binding=True)
    assert repo.create(value, actor_id="actor") == value
    assert session.execute.call_count == 2
    assert "enterprise_bindings.revision" in str(session.execute.call_args.args[0])
    assert not any(isinstance(call.args[0], BindingRow) for call in session.add.call_args_list)


def test_binding_cas_loss_does_not_insert_activation():
    repo, session, _, _, value = fixture(update_binding=True)
    passed, failed = create_autospec(CursorResult, instance=True), create_autospec(CursorResult, instance=True)
    passed.rowcount, failed.rowcount = 1, 0
    session.execute.side_effect = [passed, failed]
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_source_revision_drift_aborts_before_binding_write():
    repo, session, _, rows, value = fixture()
    rows[3].revision += 1
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_duplicate_activation_is_not_recreated():
    repo, session, _, rows, value = fixture()
    rows[1] = WorkflowActivationRow()
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_binding_timestamp_never_moves_backwards():
    repo, session, _, rows, value = fixture(update_binding=True)
    rows[-1].updated_at = value.binding.updated_at + timedelta(seconds=1)
    with pytest.raises(Conflict):
        repo.create(value, actor_id="actor")
    session.add.assert_not_called()


def test_find_and_get_restore_exact_activation_without_mutating():
    repo, session, _, _, value = fixture()
    session.scalar.side_effect = [activation_row(value), None, activation_row(value)]
    assert repo.find_enrollment(value.binding.workspace_id, value.enrollment.id) == value
    assert repo.find_enrollment(value.binding.workspace_id, value.enrollment.id) is None
    assert repo.get(value.binding.workspace_id, value.id) == value
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_revoke_is_revision_fenced_and_audited_in_same_transaction():
    repo, session, sessions, _, value = fixture()
    session.scalar.side_effect = [activation_row(value)]
    revoked = repo.revoke(value.binding.workspace_id, value.id, expected_revision=1, actor_id="actor")
    assert not revoked.active and revoked.revision == 2
    assert revoked.binding == value.binding
    assert sessions.begin.call_count == 1
    assert session.execute.call_count == 1
    assert session.add.call_count == 1


def test_revocation_conflict_adds_no_audit():
    repo, session, _, _, value = fixture()
    session.scalar.side_effect = [activation_row(value)]
    with pytest.raises(Conflict):
        repo.revoke(value.binding.workspace_id, value.id, expected_revision=2, actor_id="actor")
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_revocation_cas_loss_adds_no_audit():
    repo, session, _, _, value = fixture()
    session.scalar.side_effect = [activation_row(value)]
    session.execute.return_value.rowcount = 0
    with pytest.raises(Conflict):
        repo.revoke(value.binding.workspace_id, value.id, expected_revision=1, actor_id="actor")
    session.add.assert_not_called()
    statement = session.execute.call_args.args[0]
    assert "enterprise_workflow_activations.active IS true" in str(statement)
    assert "enterprise_workflow_activations.revision" in str(statement)


def test_revocation_audit_failure_aborts_transaction(monkeypatch):
    import enterprise_platform.persistence.workflow_activation_repository as module

    repo, session, sessions, _, value = fixture()
    session.scalar.side_effect = [activation_row(value)]
    monkeypatch.setattr(module, "audit", MagicMock(side_effect=PersistenceError("audit_failed")))
    with pytest.raises(PersistenceError):
        repo.revoke(value.binding.workspace_id, value.id, expected_revision=1, actor_id="actor")
    assert sessions.begin.return_value.__exit__.call_args.args[0] is PersistenceError
    session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["get", "find", "revoke"])
def test_activation_reads_reject_cross_workspace_rows(operation):
    repo, session, _, _, value = fixture()
    session.scalar.side_effect = [activation_row(value)]
    with pytest.raises(PersistenceError):
        if operation == "find":
            repo.find_enrollment("other-workspace", value.enrollment.id)
        elif operation == "get":
            repo.get("other-workspace", value.id)
        else:
            repo.revoke("other-workspace", value.id, expected_revision=1, actor_id="actor")
    session.execute.assert_not_called()
    session.add.assert_not_called()
