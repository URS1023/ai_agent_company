from unittest.mock import Mock
from uuid import UUID

import pytest
from test_managed_execution import auth, signed

from enterprise_platform.application.errors import AccessDenied, Unauthenticated
from enterprise_platform.application.managed_execution import ActiveExecutionKey, ExecutionAuthenticator


def grant():
    return ActiveExecutionKey(key=auth()._keys["key-1"], workflow_id=UUID(int=1))


def test_dynamic_key_is_resolved_per_version_without_caching():
    lookup = Mock()
    lookup.resolve.return_value = grant()
    verifier = ExecutionAuthenticator((), active_keys=lookup, clock=lambda: 1001)
    body, signature = signed()
    assert verifier.verify(body, "key-1", signature).workflow_id == UUID(int=1)
    lookup.resolve.assert_called_once_with("key-1", workflow_id=UUID(int=1))
    lookup.resolve.return_value = None
    with pytest.raises(Unauthenticated):
        verifier.verify(body, "key-1", signature)
    assert lookup.resolve.call_count == 2


def test_wrong_published_version_from_resolver_is_rejected():
    lookup = Mock()
    lookup.resolve.return_value = grant().model_copy(update={"workflow_id": UUID(int=2)})
    body, signature = signed()
    with pytest.raises(Unauthenticated):
        ExecutionAuthenticator((), active_keys=lookup, clock=lambda: 1001).verify(body, "key-1", signature)


def test_dynamic_grant_still_checks_signature_and_scope():
    lookup = Mock()
    lookup.resolve.return_value = grant()
    verifier = ExecutionAuthenticator((), active_keys=lookup, clock=lambda: 1001)
    body, signature = signed(workspace_id="other")
    with pytest.raises(AccessDenied):
        verifier.verify(body, "key-1", signature)
    with pytest.raises(Unauthenticated):
        verifier.verify(body, "key-1", "0" * 64)


def test_static_key_does_not_fall_back_to_dynamic_lookup():
    lookup = Mock(side_effect=AssertionError("No fallback"))
    verifier = ExecutionAuthenticator(tuple(auth()._keys.values()), active_keys=lookup, clock=lambda: 1001)
    body, signature = signed()
    assert verifier.verify(body, "key-1", signature)
    lookup.resolve.assert_not_called()


@pytest.mark.parametrize("body,signature", [(b"x" * 8193, "0" * 64), (b"{}", "bad"), (b"not-json", "0" * 64)])
def test_invalid_request_does_not_query_registry(body, signature):
    lookup = Mock()
    with pytest.raises(Unauthenticated):
        ExecutionAuthenticator((), active_keys=lookup).verify(body, "key-1", signature)
    lookup.resolve.assert_not_called()


def test_registry_error_is_sanitized():
    lookup = Mock()
    lookup.resolve.side_effect = RuntimeError("private database error")
    body, signature = signed()
    with pytest.raises(Unauthenticated) as failure:
        ExecutionAuthenticator((), active_keys=lookup).verify(body, "key-1", signature)
    assert "private" not in str(failure.value)
