import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID

import pytest
from pydantic import SecretStr

from enterprise_platform.application.contracts import BusinessResult, Run, RunSpec, canonical_hash
from enterprise_platform.application.dispatcher import BusinessEnvelope
from enterprise_platform.application.errors import AccessDenied, InvalidState, Unauthenticated
from enterprise_platform.application.input_capture import InputCaptureService
from enterprise_platform.application.managed_execution import (
    AssessmentPort,
    ExecutionAuthenticator,
    ExecutionKey,
    ManagedExecutionService,
    NativeRunLookup,
)
from enterprise_platform.application.ports import EnterpriseRepository

SECRET = "s" * 48
NATIVE = str(UUID(int=2))


def claims(**changes):
    return {
        "workspace_id": "workspace-1",
        "app_id": "app-1",
        "workflow_id": str(UUID(int=1)),
        "native_run_id": NATIVE,
        "node_id": "managed-read",
        "node_execution_id": str(UUID(int=3)),
        "invoke_from": "service-api",
        "issued_at": 1000,
        "expires_at": 1030,
        **changes,
    }


def signed(**changes):
    body = json.dumps(claims(**changes), separators=(",", ":")).encode()
    return body, hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def auth():
    return ExecutionAuthenticator(
        (
            ExecutionKey(
                key_id="key-1",
                workspace_id="workspace-1",
                app_id="app-1",
                secret=SecretStr(SECRET),
                node_ids=frozenset({"managed-read"}),
            ),
        ),
        clock=lambda: 1001,
    )


def record():
    return Run(
        id="run-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        request_key="request-1",
        payload_hash="p",
        status="dispatched",
        dispatch_nonce="private-dispatch-nonce",
        dify_run_id=NATIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        input_snapshot={"snapshot": "persisted"},
        spec=RunSpec(
            binding_id="binding-1",
            binding_revision=1,
            device_id="device-1",
            scenario="alert",
            app_id="app-1",
            workflow_id=UUID(int=1),
            specification_revision="spec-1",
            secret_ref="private-ref",
            source_id="source-1",
            source_revision="s1",
            read_id="read-1",
            read_revision="r1",
        ),
    )


def dependencies():
    lookup = create_autospec(NativeRunLookup, instance=True, spec_set=True)
    lookup.find_native_run.return_value = record()
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repository.get_run.return_value = record()
    capture = create_autospec(InputCaptureService, instance=True, spec_set=True)
    assessment = create_autospec(AssessmentPort, instance=True, spec_set=True)
    assessment.assess.return_value = BusinessEnvelope(
        run_id="run-1",
        device_id="device-1",
        specification_revision="spec-1",
        input_snapshot_digest=canonical_hash(record().input_snapshot),
        result=BusinessResult(scenario="alert", conclusion="normal", complete=True),
    )
    return (
        ManagedExecutionService(auth(), lookup, repository, capture, assessment),
        lookup,
        repository,
        capture,
        assessment,
    )


def test_verified_native_association_drives_capture_without_model_run_id_or_nonce():
    service, lookup, repository, capture, assessment = dependencies()
    body, signature = signed()
    result = asyncio.run(service.evaluate(body, "key-1", signature))
    lookup.find_native_run.assert_called_once_with("workspace-1", "app-1", NATIVE)
    capture.capture.assert_awaited_once_with("workspace-1", "run-1", "private-dispatch-nonce")
    repository.get_run.assert_called_once_with("workspace-1", "run-1")
    assessment.assess.assert_called_once_with(repository.get_run.return_value)
    assert result.run_id == "run-1"
    assert "nonce" not in result.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"workspace_id": "other"},
        {"app_id": "other"},
        {"node_id": "model-node"},
        {"invoke_from": "debugger"},
        {"issued_at": 1007},
        {"expires_at": 1001},
        {"expires_at": 1200},
        {"native_run_id": "not-a-uuid"},
        {"run_id": "forged-enterprise-run"},
        {"dispatch_nonce": "forged"},
        {"issued_at": True},
    ],
)
def test_even_signed_wrong_scope_stale_or_unregistered_claims_never_reach_lookup(changes):
    service, lookup, _, capture, _ = dependencies()
    body, signature = signed(**changes)
    with pytest.raises((Unauthenticated, AccessDenied)):
        asyncio.run(service.evaluate(body, "key-1", signature))
    lookup.find_native_run.assert_not_called()
    capture.capture.assert_not_awaited()


@pytest.mark.parametrize(
    "key,signature,body",
    [
        ("unknown", "a" * 64, b"{}"),
        ("key-1", "bad", b"{}"),
        ("key-1", "a" * 64, b"x" * 8193),
    ],
)
def test_invalid_auth_is_rejected_before_identity_resolution(key, signature, body):
    service, lookup, _, _, _ = dependencies()
    with pytest.raises(Unauthenticated):
        asyncio.run(service.evaluate(body, key, signature))
    lookup.find_native_run.assert_not_called()


def test_authenticated_but_not_yet_associated_execution_never_falls_back_to_inputs():
    service, lookup, _, capture, _ = dependencies()
    lookup.find_native_run.return_value = None
    with pytest.raises(InvalidState, match="association_pending"):
        asyncio.run(service.evaluate(*((lambda pair: (pair[0], "key-1", pair[1]))(signed()))))
    capture.capture.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "other"},
        {"dify_run_id": str(UUID(int=4))},
        {"status": "claimed"},
        {"status": "succeeded"},
        {"dispatch_nonce": None},
    ],
)
def test_lookup_results_are_revalidated_before_source_access(change):
    service, lookup, _, capture, _ = dependencies()
    lookup.find_native_run.return_value = record().model_copy(update=change)
    body, signature = signed()
    with pytest.raises((AccessDenied, InvalidState)):
        asyncio.run(service.evaluate(body, "key-1", signature))
    capture.capture.assert_not_awaited()


def test_capture_must_be_read_back_before_assessment_and_result_identity_is_verified():
    service, _, repository, capture, assessment = dependencies()
    repository.get_run.return_value = record().model_copy(update={"input_snapshot": None})
    body, signature = signed()
    with pytest.raises(InvalidState):
        asyncio.run(service.evaluate(body, "key-1", signature))
    capture.capture.assert_awaited_once()
    assessment.assess.assert_not_called()


def test_duplicate_key_configuration_and_short_secret_are_rejected():
    key = ExecutionKey(key_id="k", workspace_id="w", app_id="a", secret=SecretStr(SECRET), node_ids=frozenset({"n"}))
    with pytest.raises(ValueError):
        ExecutionAuthenticator((key, key))
    with pytest.raises(ValueError):
        ExecutionKey(key_id="k", workspace_id="w", app_id="a", secret=SecretStr("short"), node_ids=frozenset({"n"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "succeeded"},
        {"dispatch_nonce": "changed"},
        {"dify_run_id": str(UUID(int=9))},
        {"id": "different-business-run"},
    ],
)
def test_capture_readback_state_changes_prevent_assessment(changes):
    service, _, repository, capture, assessment = dependencies()
    repository.get_run.return_value = record().model_copy(update=changes)
    body, signature = signed()
    with pytest.raises((AccessDenied, InvalidState)):
        asyncio.run(service.evaluate(body, "key-1", signature))
    capture.capture.assert_awaited_once()
    assessment.assess.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "other"},
        {"device_id": "other"},
        {"specification_revision": "other"},
        {"input_snapshot_digest": "wrong"},
        {"result": BusinessResult(scenario="quality", conclusion="passed", complete=True)},
    ],
)
def test_assessment_result_must_match_the_persisted_execution(changes):
    service, _, _, _, assessment = dependencies()
    assessment.assess.return_value = assessment.assess.return_value.model_copy(update=changes)
    body, signature = signed()
    with pytest.raises(InvalidState, match="execution_result_mismatch"):
        asyncio.run(service.evaluate(body, "key-1", signature))


def test_tampering_with_signed_bytes_never_reaches_a_source():
    service, lookup, _, capture, _ = dependencies()
    body, signature = signed()
    with pytest.raises(Unauthenticated):
        asyncio.run(service.evaluate(body + b" ", "key-1", signature))
    lookup.find_native_run.assert_not_called()
    capture.capture.assert_not_awaited()
