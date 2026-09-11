import asyncio
from unittest.mock import AsyncMock

import pytest

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_draft_execution import NativeDraftCredentialOutcome
from enterprise_platform.application.workflow_draft_read import NativeDraftMetadata
from enterprise_platform.application.workflow_plugin_credentials import NativePluginCredentialOutcome
from enterprise_platform.application.workflow_provisioning_contracts import (
    BindCredentialCommand,
    PrepareCredentialCommand,
    PublishCommand,
    ReadDraftCommand,
)
from enterprise_platform.application.workflow_provisioning_execution import WorkflowProvisioningExecutor
from enterprise_platform.application.workflow_publish_execution import NativePublishOutcome, PublishRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
DRAFT = "33333333-3333-4333-8333-333333333333"
CRED = "44444444-4444-4444-8444-444444444444"
OP = "55555555-5555-4555-8555-555555555555"
PUBLISHED = "66666666-6666-4666-8666-666666666666"
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession(None, "Bearer hidden", None)


@pytest.fixture
def ports():
    reader, clients, plugin, binder, publisher = (AsyncMock() for _ in range(5))
    clients.resolve.return_value = plugin
    reader.read.return_value = NativeDraftMetadata(workspace_id=WS, app_id=APP, draft_id=DRAFT, draft_hash="a" * 64)
    plugin.prepare.return_value = NativePluginCredentialOutcome(
        state="credential_created", credential_id=CRED, plugin_unique_identifier="plugin@version"
    )
    binder.bind_credential.return_value = NativeDraftCredentialOutcome(
        "draft_bound", APP, DRAFT, CRED, "a" * 64, "b" * 64
    )
    publisher.publish.return_value = NativePublishOutcome("published", APP, PUBLISHED, "b" * 64)
    executor = WorkflowProvisioningExecutor(reader=reader, plugin_clients=clients, binder=binder, publisher=publisher)
    return executor, reader, clients, plugin, binder, publisher


def command(kind):
    base = dict(kind=kind, operation_id=OP, app_id=APP)
    if kind == "read_draft":
        return ReadDraftCommand(**base)
    if kind == "prepare_credential":
        return PrepareCredentialCommand(**base, config_ref="config-1", config_revision=2)
    if kind == "bind_credential":
        return BindCredentialCommand(**base, draft_id=DRAFT, credential_id=CRED, expected_draft_hash="a" * 64)
    return PublishCommand(**base, expected_draft_hash="b" * 64)


@pytest.mark.parametrize("kind", ["read_draft", "prepare_credential", "bind_credential", "publish"])
def test_maps_confirmed_receipts_without_running_other_phases(ports, kind):
    executor, reader, clients, plugin, binder, publisher = ports
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command(kind)))
    assert result.state == "succeeded"
    assert result.receipt.kind == kind
    assert result.receipt.app_id == APP
    assert "hidden" not in result.model_dump_json()
    assert sum(p.await_count for p in (reader.read, plugin.prepare, binder.bind_credential, publisher.publish)) == 1
    if kind == "prepare_credential":
        assert clients.resolve.call_args.kwargs == dict(
            app_id=APP, configuration_ref="config-1", configuration_revision=2
        )
        assert plugin.prepare.call_args.kwargs == dict(app_id=APP, operation_id=OP)


def test_preflight_rejection_is_not_ambiguous(ports):
    executor, *_, publisher = ports
    publisher.publish.side_effect = PublishRejected("private-detail")
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command("publish")))
    assert result.state == "rejected"
    assert result.receipt is None
    assert "private-detail" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["prepare_credential", "bind_credential", "publish"])
def test_unknown_side_effect_failure_stays_uncertain_without_retry(ports, kind):
    executor, reader, clients, plugin, binder, publisher = ports
    call = {
        "prepare_credential": plugin.prepare,
        "bind_credential": binder.bind_credential,
        "publish": publisher.publish,
    }[kind]
    call.side_effect = RuntimeError("private-detail")
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command(kind)))
    assert result.state == "uncertain"
    assert result.receipt is None
    assert call.await_count == 1
    assert "private-detail" not in result.model_dump_json()


def test_wrong_native_publication_is_not_a_success_receipt(ports):
    executor, *_, publisher = ports
    publisher.publish.return_value = NativePublishOutcome("published", DRAFT, PUBLISHED, "b" * 64)
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command("publish")))
    assert result.state == "uncertain"
    assert result.receipt is None


def test_cancellation_propagates_without_retry(ports):
    executor, *_, publisher = ports
    publisher.publish.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(executor.execute(PRINCIPAL, SESSION, command("publish")))
    assert publisher.publish.await_count == 1


def test_configuration_failure_precedes_native_creation(ports):
    executor, reader, clients, plugin, binder, publisher = ports
    clients.resolve.side_effect = RuntimeError("secret configuration error")
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command("prepare_credential")))
    assert result.state == "rejected"
    plugin.prepare.assert_not_awaited()
    assert "secret configuration error" not in result.model_dump_json()


def test_read_failure_is_definite_and_wrong_workspace_is_rejected(ports):
    executor, reader, *_ = ports
    reader.read.return_value = NativeDraftMetadata(workspace_id=APP, app_id=APP, draft_id=DRAFT, draft_hash="a" * 64)
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command("read_draft")))
    assert result.state == "rejected"
    assert result.receipt is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", DRAFT),
        ("draft_id", APP),
        ("credential_id", APP),
        ("accepted_draft_hash", "c" * 64),
        ("draft_hash", "invalid"),
    ],
)
def test_bind_response_drift_stays_uncertain(ports, field, value):
    executor, reader, clients, plugin, binder, publisher = ports
    values = dict(
        state="draft_bound",
        app_id=APP,
        draft_id=DRAFT,
        credential_id=CRED,
        accepted_draft_hash="a" * 64,
        draft_hash="b" * 64,
    )
    values[field] = value
    binder.bind_credential.return_value = NativeDraftCredentialOutcome(**values)
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command("bind_credential")))
    assert result.state == "uncertain"
    assert result.receipt is None
    publisher.publish.assert_not_awaited()


@pytest.mark.parametrize("kind", ["prepare_credential", "bind_credential", "publish"])
def test_explicit_uncertain_outcomes_remain_unresolved(ports, kind):
    executor, reader, clients, plugin, binder, publisher = ports
    plugin.prepare.return_value = NativePluginCredentialOutcome(state="uncertain", reason_code="lost_response")
    binder.bind_credential.return_value = NativeDraftCredentialOutcome("uncertain", reason_code="lost_response")
    publisher.publish.return_value = NativePublishOutcome("uncertain", reason_code="lost_response")
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, command(kind)))
    assert result.state == "uncertain"
    assert result.receipt is None
