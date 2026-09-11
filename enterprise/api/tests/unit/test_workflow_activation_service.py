import asyncio
from unittest.mock import MagicMock

import pytest
from test_workflow_activation_contracts import create, profile

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.workflow_activation_contracts import revoke_activation
from enterprise_platform.application.workflow_activation_service import WorkflowActivationService
from enterprise_platform.application.workflow_enrollment_contracts import StoredEnrollment
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry


def harness():
    value = create()
    p = value.enrollment.provisioning
    principal = Principal(
        workspace_id=p.workspace_id, actor_id=p.actor_id, workspace_role="owner", display_name="Owner"
    )
    repo, enrollment = MagicMock(), MagicMock()
    repo.get.return_value = value
    repo.find_enrollment.return_value = value
    enrollment.get.return_value = StoredEnrollment(view=value.enrollment, secret_ref=value.binding.secret_ref)
    repo.revoke.side_effect = lambda ws, identity, **kw: revoke_activation(value, now=value.updated_at, **kw)
    business, specifications = MagicMock(), MagicMock()
    business.get_device.return_value = MagicMock(workspace_id=p.workspace_id, id=p.device_id, deleted_at=None)
    business.get_binding.side_effect = NotFound()
    specifications.resolve.return_value = MagicMock(
        workspace_id=p.workspace_id, scenario=p.scenario, specification_revision="spec-1"
    )
    service = WorkflowActivationService(
        repo,
        enrollment,
        business=business,
        profiles=WorkflowPluginProfileRegistry((profile(),)),
        specifications=specifications,
        clock=lambda: value.updated_at,
    )
    return service, repo, enrollment, principal, value


def test_reads_restore_receipt_without_mutation():
    service, repo, _, principal, value = harness()
    assert asyncio.run(service.get(principal, value.id)) == value
    assert asyncio.run(service.find(principal, value.enrollment.id)) == value
    repo.find_enrollment.return_value = None
    assert asyncio.run(service.find(principal, value.enrollment.id)) is None
    repo.revoke.assert_not_called()
    repo.create.assert_not_called()


def test_revocation_returns_exact_persisted_transition():
    service, repo, _, principal, value = harness()
    result = asyncio.run(service.revoke(principal, value.id, expected_revision=1))
    assert result.binding == value.binding and not result.active and result.revision == 2
    repo.revoke.assert_called_once_with(
        principal.workspace_id, value.id, expected_revision=1, actor_id=principal.actor_id
    )


@pytest.mark.parametrize("revision", [True, 0, 2])
def test_invalid_revision_never_mutates(revision):
    service, repo, _, principal, value = harness()
    with pytest.raises(Conflict):
        asyncio.run(service.revoke(principal, value.id, expected_revision=revision))
    repo.revoke.assert_not_called()


@pytest.mark.parametrize("field", ["workspace_id", "actor_id"])
def test_wrong_scope_never_mutates(field):
    service, repo, _, principal, value = harness()
    with pytest.raises(AccessDenied):
        asyncio.run(service.revoke(principal.model_copy(update={field: "other"}), value.id, expected_revision=1))
    repo.revoke.assert_not_called()


def test_member_cannot_revoke():
    service, repo, _, principal, value = harness()
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.revoke(principal.model_copy(update={"workspace_role": "normal"}), value.id, expected_revision=1)
        )
    repo.get.assert_not_called()
    repo.revoke.assert_not_called()


def test_invalid_identifier_never_reads_repository():
    service, repo, _, principal, _ = harness()
    with pytest.raises(InvalidInput):
        asyncio.run(service.get(principal, "not-a-uuid"))
    repo.get.assert_not_called()


def test_find_rejects_changed_enrollment_snapshot():
    service, _, enrollment, principal, value = harness()
    enrollment.get.return_value = enrollment.get.return_value.model_copy(
        update={"view": value.enrollment.model_copy(update={"updated_at": value.updated_at.replace(year=2027)})}
    )
    with pytest.raises((Conflict, PersistenceError)):
        asyncio.run(service.find(principal, value.enrollment.id))


def test_unconfirmed_revocation_is_not_reported_as_success():
    service, repo, _, principal, value = harness()
    repo.revoke.side_effect = None
    repo.revoke.return_value = value
    with pytest.raises(PersistenceError):
        asyncio.run(service.revoke(principal, value.id, expected_revision=1))


def test_start_derives_binding_and_confirms_atomic_creation():
    service, repo, _, principal, value = harness()
    repo.find_enrollment.return_value = None
    repo.create.side_effect = lambda candidate, **kw: candidate
    result = asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1"))
    assert result.enrollment == value.enrollment
    assert result.binding.workflow_id == value.binding.workflow_id
    assert result.binding.secret_ref == value.binding.secret_ref
    assert result.binding.revision == 1 and result.active
    assert result.key_id == value.key_id
    assert result.id != value.id
    repo.create.assert_called_once_with(result, actor_id=principal.actor_id)
    service._business.put_binding.assert_not_called()


def test_start_recovers_existing_even_when_revoked_without_reactivation():
    service, repo, _, principal, value = harness()
    revoked = revoke_activation(value, expected_revision=1, actor_id=principal.actor_id, now=value.updated_at)
    repo.find_enrollment.return_value = revoked
    assert asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1")) == revoked
    repo.create.assert_not_called()


def test_start_rejects_existing_receipt_with_other_specification():
    service, repo, _, principal, value = harness()
    with pytest.raises(Conflict):
        asyncio.run(service.start(principal, value.enrollment.id, specification_revision="different"))
    repo.create.assert_not_called()


def test_start_detects_binding_changed_after_provisioning():
    service, repo, _, principal, value = harness()
    repo.find_enrollment.return_value = None
    service._business.get_binding.side_effect = None
    service._business.get_binding.return_value = value.binding
    with pytest.raises(Conflict):
        asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1"))
    repo.create.assert_not_called()


def test_start_rejects_deleted_device():
    service, repo, _, principal, value = harness()
    repo.find_enrollment.return_value = None
    service._business.get_device.return_value.deleted_at = value.updated_at
    with pytest.raises(NotFound):
        asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1"))
    repo.create.assert_not_called()


def test_start_rejects_unconfirmed_creation():
    service, repo, _, principal, value = harness()
    repo.find_enrollment.return_value = None
    repo.create.return_value = value
    with pytest.raises(PersistenceError):
        asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1"))


def test_start_preserves_existing_binding_identity_and_increments_frozen_revision():
    service, repo, enrollment, principal, value = harness()
    provisioning = value.enrollment.provisioning.model_copy(update={"expected_binding_revision": 1})
    enrollment.get.return_value = StoredEnrollment(
        view=value.enrollment.model_copy(update={"provisioning": provisioning}), secret_ref=value.binding.secret_ref
    )
    repo.find_enrollment.return_value = None
    repo.create.side_effect = lambda candidate, **kw: candidate
    service._business.get_binding.side_effect = None
    service._business.get_binding.return_value = value.binding
    result = asyncio.run(service.start(principal, value.enrollment.id, specification_revision="spec-1"))
    assert result.binding.id == value.binding.id
    assert result.binding.created_at == value.binding.created_at
    assert result.binding.revision == 2
    service._business.put_binding.assert_not_called()


def test_specification_choices_are_scoped_to_owned_enrollment():
    service, repo, _, principal, value = harness()
    service._specifications.list_revisions.return_value = ("spec-1", "spec-2")
    assert asyncio.run(service.specifications(principal, value.enrollment.id)) == ("spec-1", "spec-2")
    service._specifications.list_revisions.assert_called_once_with(principal.workspace_id, value.binding.scenario)
    repo.create.assert_not_called()


def test_specification_choices_reject_wrong_owner_before_catalog_access():
    service, _, _, principal, value = harness()
    with pytest.raises(AccessDenied):
        asyncio.run(service.specifications(principal.model_copy(update={"actor_id": "other"}), value.enrollment.id))
    service._specifications.list_revisions.assert_not_called()
