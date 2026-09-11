import asyncio
from unittest.mock import AsyncMock, create_autospec

import pytest
from test_workflow_activation_contracts import create

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.adapters.schedule_owner_client import DifyScheduleOwnerClient
from enterprise_platform.adapters.schedule_owner_probe import ActivationScheduleOwnerProbe
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup


def fixture():
    activation = create()
    lookup = create_autospec(SqlAlchemyActiveExecutionKeyLookup, instance=True)
    lookup.find_registration.return_value = activation.enrollment.publication
    identity = create_autospec(DifyIdentityClient, instance=True)
    identity.resolve.return_value = Principal(
        workspace_id=activation.binding.workspace_id,
        actor_id="inspector",
        workspace_role="editor",
        display_name="Inspector",
    )
    client = create_autospec(DifyScheduleOwnerClient, instance=True)
    client.read.return_value = False
    session = NativeSetupSession("access_token=secret; csrf_token=csrf", None, "csrf")
    sessions = AsyncMock(return_value=session)
    probe = ActivationScheduleOwnerProbe(lookup, identity, client, sessions)
    return probe, lookup, identity, client, sessions, activation


def test_probe_uses_activated_hash_and_rechecks_registration_after_native_read() -> None:
    probe, lookup, _, client, sessions, activation = fixture()
    assert asyncio.run(probe(activation.binding)) is False
    assert lookup.find_registration.call_count == 2
    lookup.find_registration.assert_called_with(
        activation.binding.workspace_id,
        activation.binding.app_id,
        workflow_id=activation.binding.workflow_id,
        expected_binding=activation.binding,
    )
    assert client.read.call_args.kwargs["expected_hash"] == activation.enrollment.publication.graph_hash
    assert client.read.call_args.kwargs["workflow_id"] == str(activation.binding.workflow_id)
    sessions.assert_awaited_once()


def test_missing_activation_prevents_session_and_native_requests() -> None:
    probe, lookup, _, client, sessions, activation = fixture()
    lookup.find_registration.return_value = None
    with pytest.raises(DependencyUnavailable):
        asyncio.run(probe(activation.binding))
    sessions.assert_not_awaited()
    client.read.assert_not_awaited()


def test_revocation_during_native_read_rejects_no_owner_receipt() -> None:
    probe, lookup, _, _, _, activation = fixture()
    lookup.find_registration.side_effect = [activation.enrollment.publication, None]
    with pytest.raises(DependencyUnavailable):
        asyncio.run(probe(activation.binding))


def test_native_session_in_another_workspace_does_not_query_owner() -> None:
    probe, _, identity, client, _, activation = fixture()
    identity.resolve.return_value = identity.resolve.return_value.model_copy(update={"workspace_id": "other"})
    with pytest.raises(AccessDenied):
        asyncio.run(probe(activation.binding))
    client.read.assert_not_awaited()


def test_native_owner_positive_is_preserved() -> None:
    probe, _, _, client, _, activation = fixture()
    client.read.return_value = True
    assert asyncio.run(probe(activation.binding)) is True
