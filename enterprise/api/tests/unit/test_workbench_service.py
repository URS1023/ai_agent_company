import asyncio
from importlib import import_module
from threading import get_ident
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from test_workbench_messages import intent

from enterprise_platform.adapters.workbench_context_client import ContextCheckRejected, DifyWorkbenchContextClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, PersistenceError
from enterprise_platform.application.workbench_messages import claim_message, create_message_intent, mark_send_uncertain
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

SESSION = NativeSetupSession("access_token=native; csrf_token=csrf", None, "csrf")


@pytest.fixture
def context_checker():
    return Mock(check_context=AsyncMock(), check_selected_attachments=AsyncMock())


@pytest.fixture
def setup(context_checker):
    module = import_module("enterprise_platform.application.workbench_service")
    repository, authority = Mock(), Mock(require_send=AsyncMock())
    repository.create_or_get.return_value = intent()
    repository.claim.return_value = claim_message(intent())
    principal = Principal(actor_id="actor", workspace_id="w", workspace_role="normal", display_name="User")
    return module.WorkbenchSendService(repository, authority, context_checker), repository, authority, principal


def run_prepare(service, principal, **kwargs):
    return asyncio.run(service.prepare(principal, SESSION, **kwargs))


def prepare(setup):
    service, _, _, principal = setup
    return run_prepare(
        service,
        principal,
        installed_app_id=UUID(int=1),
        branch_id="branch",
        client_message_id=UUID(int=2),
        payload={"query": "  设备分析\n", "inputs": {}},
    )


def test_identity_is_derived_and_authority_precedes_storage_and_claim(setup, context_checker):
    _, repository, authority, principal = setup
    order = Mock()
    order.attach_mock(context_checker, "context")
    order.attach_mock(authority, "authority")
    order.attach_mock(repository, "repository")
    result = prepare(setup)
    assert result.dispatch_claimed is True
    assert result.intent == claim_message(intent())
    context_checker.check_context.assert_awaited_once_with(principal, SESSION, intent())
    context_checker.check_selected_attachments.assert_awaited_once_with(principal, SESSION, intent())
    authority.require_send.assert_awaited_once_with(principal, SESSION, intent())
    assert [call[0] for call in order.mock_calls] == [
        "context.check_context",
        "context.check_selected_attachments",
        "authority.require_send",
        "repository.create_or_get",
        "repository.claim",
    ]
    repository.claim.assert_called_once_with(intent().scope, UUID(int=2), expected_revision=1)


def test_denied_authority_never_touches_storage(setup):
    _, repository, authority, _ = setup
    authority.require_send.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        prepare(setup)
    assert not repository.mock_calls


@pytest.mark.parametrize("uncertain", [False, True])
def test_retry_rechecks_authority_but_never_reclaims_sent_message(setup, context_checker, uncertain):
    _, repository, authority, _ = setup
    current = claim_message(intent())
    if uncertain:
        current = mark_send_uncertain(current)
    repository.create_or_get.return_value = current
    result = prepare(setup)
    assert result.intent == current and result.dispatch_claimed is False
    authority.require_send.assert_called_once()
    context_checker.check_selected_attachments.assert_awaited_once()
    repository.claim.assert_not_called()


@pytest.mark.parametrize("failure", [Conflict(), PersistenceError()])
def test_failed_claim_never_returns_dispatch_permission(setup, failure):
    _, repository, _, _ = setup
    repository.claim.side_effect = failure
    with pytest.raises(type(failure)):
        prepare(setup)


def test_repository_foreign_snapshot_is_rejected_before_claim(setup):
    _, repository, _, _ = setup
    repository.create_or_get.return_value = intent().model_copy(update={"payload_json": '{"query":"other"}'})
    with pytest.raises(Conflict):
        prepare(setup)
    repository.claim.assert_not_called()


def test_repository_must_return_exact_claim_transition(setup):
    _, repository, _, _ = setup
    repository.claim.return_value = intent()
    with pytest.raises(PersistenceError):
        prepare(setup)


@pytest.mark.parametrize(
    "update",
    [
        {"workspace_id": "foreign"},
        {"actor_id": "foreign"},
        {"model_config": {}},
        {"response_mode": "blocking"},
        {"query": 42},
        {"inputs": []},
        {"files": ["file"]},
        {"conversation_id": "bad-id"},
        {"parent_message_id": False},
        {"retriever_from": None},
    ],
)
def test_invalid_native_request_is_rejected_before_authority_and_storage(setup, update):
    service, repository, authority, principal = setup
    with pytest.raises(InvalidInput, match="^message_request_invalid$"):
        run_prepare(
            service,
            principal,
            installed_app_id=UUID(int=1),
            branch_id="branch",
            client_message_id=UUID(int=2),
            payload={"query": "hello", "inputs": {}, **update},
        )
    assert not authority.mock_calls and not repository.mock_calls


def test_native_context_inputs_and_attachments_are_preserved_exactly(setup):
    service, repository, authority, principal = setup
    payload = {
        "query": "  分析\n",
        "inputs": {"workspace_id": "a business input", "count": 123},
        "conversation_id": str(UUID(int=3)),
        "parent_message_id": "",
        "files": [{"type": "document", "transfer_method": "local_file", "upload_file_id": str(UUID(int=4))}],
        "retriever_from": "explore_app",
    }
    requested = create_message_intent(intent().scope, UUID(int=2), payload)
    repository.create_or_get.return_value = requested
    repository.claim.return_value = claim_message(requested)
    result = run_prepare(
        service,
        principal,
        installed_app_id=UUID(int=1),
        branch_id="branch",
        client_message_id=UUID(int=2),
        payload=payload,
    )
    assert result.intent.payload_json == requested.payload_json
    authority.require_send.assert_awaited_once_with(principal, SESSION, requested)


def test_failed_context_never_checks_authority_or_stores_request(setup, context_checker):
    _, repository, authority, _ = setup
    context_checker.check_context.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        prepare(setup)
    assert not repository.mock_calls and not authority.mock_calls
    context_checker.check_selected_attachments.assert_not_awaited()


def test_cancelled_preflight_never_claims_send(setup, context_checker):
    service, repository, authority, principal = setup

    async def scenario():
        entered = asyncio.Event()

        async def pending(*args):
            entered.set()
            await asyncio.Future()

        context_checker.check_context.side_effect = pending
        task = asyncio.create_task(
            service.prepare(
                principal,
                SESSION,
                installed_app_id=UUID(int=1),
                branch_id="branch",
                client_message_id=UUID(int=2),
                payload={"query": "q", "inputs": {}},
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not repository.mock_calls and not authority.mock_calls
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not repository.mock_calls and not authority.mock_calls

    asyncio.run(scenario())


def test_real_context_client_precedes_authority_and_database_runs_off_event_loop(setup):
    _, repository, authority, principal = setup
    order = []
    main_thread = get_ident()

    def handler(request):
        if request.url.path.endswith("/attachment-check"):
            order.append("attachments")
            return httpx.Response(
                200,
                json={
                    "workspace_id": "w",
                    "actor_id": "actor",
                    "installed_app_id": str(UUID(int=1)),
                    "file_count": 0,
                    "selected_files_verified": True,
                    "input_files_verified": False,
                    "branch_verified": False,
                },
            )
        order.append("native")
        assert request.headers["X-Enterprise-Expected-Actor"] == principal.actor_id
        return httpx.Response(
            200,
            json={
                "workspace_id": "w",
                "actor_id": "actor",
                "installed_app_id": str(UUID(int=1)),
                "context_verified": True,
                "attachments_verified": False,
                "branch_verified": False,
            },
        )

    async def authorize(*args):
        order.append("authority")
        assert args == (principal, SESSION, intent())

    def create(requested):
        assert get_ident() != main_thread
        order.append("create")
        return requested

    def claim(*args, **kwargs):
        assert get_ident() != main_thread
        order.append("claim")
        return claim_message(intent())

    client = DifyWorkbenchContextClient(base_url="https://native/console/api", transport=httpx.MockTransport(handler))
    service = import_module("enterprise_platform.application.workbench_service").WorkbenchSendService(
        repository, authority, client
    )
    authority.require_send.side_effect = authorize
    repository.create_or_get.side_effect = create
    repository.claim.side_effect = claim
    assert prepare((service, repository, authority, principal)).dispatch_claimed
    assert order == ["native", "attachments", "authority", "create", "claim"]


def test_attachment_denial_prevents_remaining_authority_and_all_storage(setup, context_checker):
    _, repository, authority, _ = setup
    context_checker.check_selected_attachments.side_effect = ContextCheckRejected()
    with pytest.raises(ContextCheckRejected):
        prepare(setup)
    assert not repository.mock_calls
    assert not authority.mock_calls


def test_attachment_preflight_cancellation_does_not_create_or_claim(setup, context_checker):
    service, repository, authority, principal = setup

    async def scenario():
        entered = asyncio.Event()

        async def pending(*args):
            entered.set()
            await asyncio.Future()

        context_checker.check_selected_attachments.side_effect = pending
        task = asyncio.create_task(
            service.prepare(
                principal,
                SESSION,
                installed_app_id=UUID(int=1),
                branch_id="branch",
                client_message_id=UUID(int=2),
                payload={"query": "q", "inputs": {}, "files": []},
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not repository.mock_calls
        assert not authority.mock_calls

    asyncio.run(scenario())


def test_real_context_http_denial_stops_service_before_authority_and_storage(setup):
    _, repository, authority, principal = setup
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(lambda _: httpx.Response(403))
    )
    service = import_module("enterprise_platform.application.workbench_service").WorkbenchSendService(
        repository, authority, client
    )
    with pytest.raises(ContextCheckRejected):
        prepare((service, repository, authority, principal))
    assert not repository.mock_calls and not authority.mock_calls


@pytest.mark.parametrize("status", [403, 200])
def test_real_attachment_denial_or_overclaimed_authority_never_reaches_storage(setup, status):
    _, repository, authority, principal = setup
    paths = []

    def handler(request):
        paths.append(request.url.path.rsplit("/", 1)[-1])
        identity = {"workspace_id": "w", "actor_id": "actor", "installed_app_id": str(UUID(int=1))}
        if paths[-1] == "context-check":
            return httpx.Response(
                200,
                json={
                    **identity,
                    "context_verified": True,
                    "attachments_verified": False,
                    "branch_verified": False,
                },
            )
        assert paths[-1] == "attachment-check"
        return httpx.Response(
            status,
            json={
                **identity,
                "file_count": 0,
                "selected_files_verified": True,
                "input_files_verified": True,
                "branch_verified": True,
            },
        )

    client = DifyWorkbenchContextClient(base_url="https://native/console/api", transport=httpx.MockTransport(handler))
    service = import_module("enterprise_platform.application.workbench_service").WorkbenchSendService(
        repository, authority, client
    )
    with pytest.raises(ContextCheckRejected):
        prepare((service, repository, authority, principal))
    assert paths == ["context-check", "attachment-check"]
    assert not repository.mock_calls
    assert not authority.mock_calls
