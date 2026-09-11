import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_dispatch import setup_dispatch
from test_workbench_messages import intent, receipt
from test_workbench_routes import HEADERS, URL, client_for

from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.workbench_messages import acknowledge_message, claim_message, mark_send_uncertain


def read(dispatcher):
    return dispatcher.read_message(
        PRINCIPAL,
        SESSION,
        installed_app_id=intent().scope.installed_app_id,
        branch_id=intent().scope.branch_id,
        client_message_id=intent().client_message_id,
    )


@pytest.mark.parametrize(
    "current",
    [
        intent(),
        claim_message(intent()),
        mark_send_uncertain(claim_message(intent())),
        acknowledge_message(claim_message(intent()), receipt()),
    ],
)
def test_state_read_is_scoped_and_never_claims_or_dispatches(current):
    dispatcher, ledger, calls, preparer = setup_dispatch(current=current)
    dispatcher._native.check_branch_access = AsyncMock()
    ledger.get = Mock(return_value=current)
    result = asyncio.run(read(dispatcher))
    assert result == current
    dispatcher._native.check_branch_access.assert_awaited_once_with(PRINCIPAL, SESSION, current.scope)
    ledger.get.assert_called_once_with(current.scope, current.client_message_id)
    preparer.prepare.assert_not_called()
    assert calls == [] and ledger.acks == ledger.uncertain == 0


def test_native_denial_precedes_ledger_read():
    dispatcher, ledger, _, _ = setup_dispatch()
    dispatcher._native.check_branch_access = AsyncMock(side_effect=AccessDenied())
    ledger.get = Mock()
    with pytest.raises(AccessDenied):
        asyncio.run(read(dispatcher))
    ledger.get.assert_not_called()


@pytest.mark.parametrize("field", ["client_message_id", "workspace_id", "actor_id", "installed_app_id", "branch_id"])
def test_foreign_ledger_record_is_not_returned(field):
    dispatcher, ledger, _, _ = setup_dispatch()
    dispatcher._native.check_branch_access = AsyncMock()
    stored = intent()
    if field == "client_message_id":
        stored = stored.model_copy(update={field: UUID(int=999)})
    else:
        value = UUID(int=999) if field == "installed_app_id" else "other"
        stored = stored.model_copy(update={"scope": stored.scope.model_copy(update={field: value})})
    ledger.get = Mock(return_value=stored)
    with pytest.raises(PersistenceError):
        asyncio.run(read(dispatcher))


def test_http_status_returns_only_send_projection_with_no_store():
    dispatcher, ledger, calls, preparer = setup_dispatch(
        current=acknowledge_message(claim_message(intent()), receipt())
    )
    dispatcher._native.check_branch_access = AsyncMock()
    client, _ = client_for(dispatcher)
    with client:
        response = client.get(f"{URL}/{intent().client_message_id}", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "client_message_id": str(intent().client_message_id),
        "status": "accepted",
        "revision": ledger.current.revision,
        "conversation_id": str(receipt().conversation_id),
        "message_id": str(receipt().message_id),
        "task_id": receipt().task_id,
        "outcome": None,
    }
    assert calls == []
    preparer.prepare.assert_not_called()


@pytest.mark.parametrize("field", ["installed_app_id", "client_message_id"])
def test_zero_identity_is_rejected_before_native_or_database_access(field):
    from enterprise_platform.application.errors import InvalidInput

    dispatcher, ledger, _, _ = setup_dispatch()
    dispatcher._native.check_branch_access = AsyncMock()
    ledger.get = Mock()
    kwargs = dict(
        installed_app_id=intent().scope.installed_app_id,
        branch_id="branch",
        client_message_id=intent().client_message_id,
    )
    kwargs[field] = UUID(int=0)
    with pytest.raises(InvalidInput):
        asyncio.run(dispatcher.read_message(PRINCIPAL, SESSION, **kwargs))
    dispatcher._native.check_branch_access.assert_not_called()
    ledger.get.assert_not_called()


def test_http_denied_native_access_does_not_read_or_expose_ledger():
    dispatcher, ledger, _, _ = setup_dispatch()
    dispatcher._native.check_branch_access = AsyncMock(side_effect=AccessDenied())
    ledger.get = Mock()
    client, _ = client_for(dispatcher)
    with client:
        response = client.get(f"{URL}/{intent().client_message_id}", headers=HEADERS)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    assert "payload" not in response.text
    ledger.get.assert_not_called()


def test_http_absent_record_is_not_queued_or_recreated():
    from enterprise_platform.application.errors import NotFound

    dispatcher, ledger, _, preparer = setup_dispatch()
    dispatcher._native.check_branch_access = AsyncMock()
    ledger.get = Mock(side_effect=NotFound())
    client, _ = client_for(dispatcher)
    with client:
        response = client.get(f"{URL}/{intent().client_message_id}", headers=HEADERS)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, no-store"
    preparer.prepare.assert_not_called()


@pytest.mark.parametrize("outcome", ["succeeded", "stopped", "failed"])
def test_http_reports_only_already_persisted_terminal(outcome):
    from enterprise_platform.application.workbench_messages import NativeGenerationTerminal, finish_message

    current = finish_message(
        acknowledge_message(claim_message(intent()), receipt()),
        NativeGenerationTerminal(receipt=receipt(), outcome=outcome),
    )
    dispatcher, ledger, calls, preparer = setup_dispatch(current=current)
    dispatcher._native.check_branch_access = AsyncMock()
    client, _ = client_for(dispatcher)
    with client:
        response = client.get(f"{URL}/{intent().client_message_id}", headers=HEADERS)
    assert response.json()["outcome"] == outcome
    assert ledger.current == current and calls == []
    preparer.prepare.assert_not_called()
