import asyncio
from importlib import import_module
from threading import get_ident
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import intent

from enterprise_platform.application.errors import AccessDenied, InvalidState, NotFound
from enterprise_platform.application.workbench_branches import create_root_branch, fork_branch
from enterprise_platform.application.workbench_messages import claim_message


def make_authority(branch=None):
    repository = Mock()
    repository.get.return_value = branch or create_root_branch(intent().scope)
    inputs = Mock(check_inputs=AsyncMock())
    authority = import_module("enterprise_platform.application.workbench_service").WorkbenchSendAuthority(
        repository, inputs
    )
    return authority, repository, inputs


def run(authority, principal=PRINCIPAL):
    return asyncio.run(authority.require_send(principal, SESSION, intent()))


def test_branch_read_is_scoped_and_off_loop_before_input_authority():
    authority, repository, inputs = make_authority()
    main_thread = get_ident()
    order = []

    def get(scope):
        assert scope == intent().scope
        assert get_ident() != main_thread
        order.append("branch")
        return create_root_branch(scope)

    async def check(*args):
        assert args == (PRINCIPAL, SESSION, intent())
        order.append("inputs")

    repository.get.side_effect = get
    inputs.check_inputs.side_effect = check
    run(authority)
    assert order == ["branch", "inputs"]
    assert [call[0] for call in repository.mock_calls] == ["get"]


@pytest.mark.parametrize("field", ["actor_id", "workspace_id"])
def test_foreign_principal_prevents_all_repository_and_input_work(field):
    authority, repository, inputs = make_authority()
    with pytest.raises(AccessDenied):
        run(authority, PRINCIPAL.model_copy(update={field: "other"}))
    assert not repository.mock_calls
    inputs.check_inputs.assert_not_awaited()


def test_repository_foreign_branch_is_not_accepted():
    foreign = intent().scope.model_copy(update={"branch_id": "other"})
    authority, _, inputs = make_authority(create_root_branch(foreign))
    with pytest.raises(AccessDenied):
        run(authority)
    inputs.check_inputs.assert_not_awaited()


@pytest.mark.parametrize("preparing", [False, True])
def test_unready_or_archived_branch_is_rejected(preparing):
    branch = create_root_branch(intent().scope)
    if preparing:
        parent = branch.model_copy(
            update={"scope": branch.scope.model_copy(update={"branch_id": "parent"}), "conversation_id": UUID(int=3)}
        )
        branch = fork_branch(parent, "branch", UUID(int=4))
    else:
        branch = branch.model_copy(update={"state": "archived"})
    authority, _, inputs = make_authority(branch)
    with pytest.raises(InvalidState):
        run(authority)
    inputs.check_inputs.assert_not_awaited()


def test_missing_branch_stays_missing_and_does_not_auto_create():
    authority, repository, inputs = make_authority()
    repository.get.side_effect = NotFound()
    with pytest.raises(NotFound):
        run(authority)
    assert [call[0] for call in repository.mock_calls] == ["get"]
    inputs.check_inputs.assert_not_awaited()


def test_inflight_branch_does_not_break_duplicate_reconciliation():
    branch = create_root_branch(intent().scope).model_copy(
        update={"inflight_client_message_id": intent().client_message_id}
    )
    authority, repository, inputs = make_authority(branch)
    run(authority)
    inputs.check_inputs.assert_awaited_once()
    assert repository.get.return_value.inflight_client_message_id == intent().client_message_id


def test_input_authority_denial_propagates_without_branch_mutation():
    authority, repository, inputs = make_authority()
    inputs.check_inputs.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        run(authority)
    assert [call[0] for call in repository.mock_calls] == ["get"]


@pytest.mark.parametrize("deny_inputs", [False, True])
def test_real_authority_composes_with_send_service_before_claim(deny_inputs):
    module = import_module("enterprise_platform.application.workbench_service")
    authority, branches, inputs = make_authority()
    ledger = Mock()
    ledger.create_or_get.return_value = intent()
    ledger.claim.return_value = claim_message(intent())
    native = Mock(check_context=AsyncMock(), check_selected_attachments=AsyncMock())
    order = Mock()
    for name, mock in [("native", native), ("branches", branches), ("inputs", inputs), ("ledger", ledger)]:
        order.attach_mock(mock, name)
    service = module.WorkbenchSendService(ledger, authority, native)

    async def prepare():
        return await service.prepare(
            PRINCIPAL,
            SESSION,
            installed_app_id=UUID(int=1),
            branch_id="branch",
            client_message_id=UUID(int=2),
            payload={"query": "  设备分析\n", "inputs": {}},
        )

    if deny_inputs:
        inputs.check_inputs.side_effect = AccessDenied()
        with pytest.raises(AccessDenied):
            asyncio.run(prepare())
        assert not ledger.mock_calls
    else:
        assert asyncio.run(prepare()).dispatch_claimed is True
    expected = ["native.check_context", "native.check_selected_attachments", "branches.get", "inputs.check_inputs"]
    if not deny_inputs:
        expected += ["ledger.create_or_get", "ledger.claim"]
    assert [call[0] for call in order.mock_calls] == expected
