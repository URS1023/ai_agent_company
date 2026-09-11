from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy.orm import Session
from test_workflow_activation_contracts import create, profile
from test_workflow_enrollment_contracts import SECRET_REF

from enterprise_platform.application.workflow_credential_ports import SealedCredential
from enterprise_platform.application.workflow_enrollment_contracts import StoredEnrollment
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry
from enterprise_platform.persistence.mapping import serialized
from enterprise_platform.persistence.models import BindingRow, DeviceRow
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup
from enterprise_platform.persistence.workflow_activation_mapping import activation_row
from enterprise_platform.persistence.workflow_credentials import credential_row
from enterprise_platform.persistence.workflow_enrollment_mapping import enrollment_row


def fixture():
    value = create()
    binding = value.binding
    payload = binding.model_dump(
        exclude={"id", "workspace_id", "device_id", "scenario", "revision", "created_at", "updated_at", "active_run_id"}
    )
    from enterprise_platform.application.contracts import BindingWrite

    rows = [
        activation_row(value),
        enrollment_row(StoredEnrollment(view=value.enrollment, secret_ref=SECRET_REF)),
        BindingRow(
            workspace_id=binding.workspace_id,
            binding_id=binding.id,
            device_id=binding.device_id,
            scenario=binding.scenario,
            revision=binding.revision,
            binding_json=serialized(BindingWrite.model_validate(payload)),
            active_run_id="in-flight-run",
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        ),
        credential_row(value.enrollment.credential, SealedCredential("key", "nonce", "ciphertext")),
        DeviceRow(workspace_id=binding.workspace_id, device_id=binding.device_id, deleted=False),
    ]
    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.side_effect = rows
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    return (
        SqlAlchemyActiveExecutionKeyLookup(sessions, WorkflowPluginProfileRegistry((profile(),))),
        session,
        rows,
        value,
    )


def test_live_activation_resolves_exact_version_during_active_run():
    lookup, session, _, value = fixture()
    result = lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id)
    assert result.key.key_id == value.key_id
    assert result.workflow_id == value.binding.workflow_id
    assert result.key.node_ids == frozenset({"assessment"})
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_schedule_publication_lookup_pins_exact_binding_and_all_live_dependencies():
    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [rows[0], *rows]
    result = lookup.find_registration(
        value.binding.workspace_id,
        value.binding.app_id,
        workflow_id=value.binding.workflow_id,
        expected_binding=value.binding,
    )
    assert result == value.enrollment.publication
    params = session.scalar.call_args_list[0].args[0].compile().params
    assert params["binding_id_1"] == value.binding.id
    assert params["binding_revision_1"] == value.binding.revision
    assert session.scalar.call_count == 6


@pytest.mark.parametrize(
    "patch", [{"id": "replacement"}, {"revision": 99}, {"device_id": "other"}, {"manifest": {"input_keys": ["forged"]}}]
)
def test_schedule_lookup_rejects_another_binding_even_for_same_publication(patch):
    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [rows[0], *rows]
    assert (
        lookup.find_registration(
            value.binding.workspace_id,
            value.binding.app_id,
            workflow_id=value.binding.workflow_id,
            expected_binding=value.binding.model_copy(update=patch),
        )
        is None
    )
    assert session.scalar.call_count == 1


def test_schedule_lookup_ignores_only_transient_active_run_marker():
    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [rows[0], *rows]
    assert (
        lookup.find_registration(
            value.binding.workspace_id,
            value.binding.app_id,
            workflow_id=value.binding.workflow_id,
            expected_binding=value.binding.model_copy(update={"active_run_id": "in-flight-run"}),
        )
        == value.enrollment.publication
    )


@pytest.mark.parametrize("missing", range(5))
def test_missing_live_dependency_denies_resolution(missing):
    lookup, session, rows, value = fixture()
    rows[missing] = None
    session.scalar.side_effect = rows
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None


@pytest.mark.parametrize(
    "index,field,changed", [(0, "active", False), (2, "revision", 2), (3, "active", False), (4, "deleted", True)]
)
def test_revoked_or_changed_records_never_resolve(index, field, changed):
    lookup, _, rows, value = fixture()
    setattr(rows[index], field, changed)
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None


def test_changed_master_key_does_not_silently_activate_old_record():
    lookup, _, _, value = fixture()
    changed = profile().model_copy(update={"master_key_id": "new-master"})
    lookup._profiles = WorkflowPluginProfileRegistry((changed,))
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None


def test_each_resolution_reads_live_activation_again():
    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [*rows, None]
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is not None
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None
    assert session.scalar.call_count == 6


def test_profile_removal_denies_resolution():
    lookup, _, _, value = fixture()
    lookup._profiles = WorkflowPluginProfileRegistry(())
    assert lookup.resolve(value.key_id, workflow_id=value.binding.workflow_id) is None


def test_unknown_key_format_never_queries_storage():
    lookup, session, _, value = fixture()
    assert lookup.resolve("caller-supplied", workflow_id=value.binding.workflow_id) is None
    session.scalar.assert_not_called()


def test_native_registration_returns_only_exact_publication_after_live_checks():
    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [rows[0], *rows]
    result = lookup.find_registration(
        value.binding.workspace_id, value.binding.app_id, workflow_id=value.binding.workflow_id
    )
    assert result == value.enrollment.publication
    assert set(result.model_dump()) == {
        "workspace_id",
        "app_id",
        "workflow_id",
        "graph_hash",
        "provider_id",
        "tool_name",
        "credential_id",
        "node_id",
    }
    session.add.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("missing", range(5))
def test_native_registration_requires_every_live_dependency(missing):
    lookup, session, rows, value = fixture()
    initial = rows[0]
    rows[missing] = None
    session.scalar.side_effect = [initial, *rows]
    assert (
        lookup.find_registration(
            value.binding.workspace_id, value.binding.app_id, workflow_id=value.binding.workflow_id
        )
        is None
    )


def test_native_registration_does_not_cross_app_scope():
    from uuid import UUID

    lookup, session, rows, value = fixture()
    session.scalar.side_effect = [rows[0]]
    assert (
        lookup.find_registration(value.binding.workspace_id, str(UUID(int=99)), workflow_id=value.binding.workflow_id)
        is None
    )
    assert session.scalar.call_count == 1


def test_native_registration_rejects_snapshot_changed_during_resolution():
    lookup, session, rows, value = fixture()
    changed = value.model_copy(update={"id": "00000000-0000-0000-0000-000000000099"})
    session.scalar.side_effect = [rows[0], activation_row(changed)]
    assert (
        lookup.find_registration(
            value.binding.workspace_id, value.binding.app_id, workflow_id=value.binding.workflow_id
        )
        is None
    )
