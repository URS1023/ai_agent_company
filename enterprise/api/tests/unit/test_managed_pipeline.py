"""Real HTTP/SSE codecs, capture and assessment; no database or native daemon.

Only persistence and upstream transports are doubles. Signed node metadata is a
fixture here; native metadata forwarding and the deployed plugin need separate
runtime verification and are not claimed by these pipeline tests.
"""

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import create_autospec

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_dify_streaming import frame
from test_input_capture import device, registered, run_record
from test_managed_execution import NATIVE, auth, claims

from enterprise_platform.adapters.workflow_gateway import NativeWorkflowGateway, WorkflowCredential
from enterprise_platform.application.assessments import (
    AlertSpecification,
    AlertVariable,
    AssessmentService,
    ImmutableSpecificationCatalog,
    QualityIdentityColumns,
    QualitySpecification,
    QualityWideMapping,
    QualityWideMetric,
)
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dispatcher import RunDispatcher
from enterprise_platform.application.input_capture import (
    ImmutableReadCatalog,
    InputCaptureService,
    RegisteredSourceReader,
)
from enterprise_platform.application.managed_execution import ManagedExecutionService, NativeRunLookup
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.domain.quality import QualityMetric, QualityStandard
from enterprise_platform.domain.rules import AlertRule
from enterprise_platform.http.internal import create_managed_router


@pytest.mark.parametrize(
    "scenario,value,conclusion",
    [
        ("alert", "85.0000000000000001", "issues"),
        ("alert", "85", "normal"),
        ("quality", "85.0000000000000001", "failed"),
        ("quality", "85", "passed"),
    ],
)
def test_stream_to_attested_capture_to_precise_assessment_to_persisted_result(scenario, value, conclusion):
    current = run_record()
    current = current.model_copy(
        update={
            "status": "queued",
            "dispatch_nonce": None,
            "dify_run_id": None,
            "spec": current.spec.model_copy(update={"scenario": scenario}),
        }
    )
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repository.get_run.side_effect = lambda *_: current
    repository.get_device.return_value = device()

    def claim(ws, rid, nonce, *, actor_id):
        nonlocal current
        assert current.status == "queued"
        current = current.model_copy(update={"status": "claimed", "dispatch_nonce": nonce})
        return current

    def associate(ws, rid, nonce, native, *, actor_id):
        nonlocal current
        assert current.status == "claimed"
        current = current.model_copy(update={"status": "dispatched", "dify_run_id": native})
        return current

    def capture(ws, rid, nonce, snapshot, *, actor_id):
        nonlocal current
        assert current.status == "dispatched" and nonce == current.dispatch_nonce
        current = current.model_copy(update={"input_snapshot": snapshot})
        return current

    def complete(ws, rid, nonce, result, digest, *, actor_id):
        nonlocal current
        assert digest == canonical_hash(result.model_dump(mode="json"))
        current = current.model_copy(update={"status": "succeeded", "result": result, "result_digest": digest})
        return current

    repository.claim_run.side_effect = claim
    repository.mark_dispatched.side_effect = associate
    repository.capture_input.side_effect = capture
    repository.complete_run.side_effect = complete
    lookup = create_autospec(NativeRunLookup, instance=True, spec_set=True)
    lookup.find_native_run.side_effect = lambda ws, app, native: (
        current
        if (ws == current.workspace_id and app == current.spec.app_id and native == current.dify_run_id)
        else None
    )
    source_calls = []

    def read_source(request):
        source_calls.append(request)
        assert request.url.params["device"] == "0001"
        assert request.url.params["batch"] == "batch-0001"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "device": "0001",
                        "value": value,
                        "record": "00001",
                        "sample": "0001",
                        "revision": "r1",
                        "time": "2026-09-08T12:00:00+00:00",
                    }
                ]
            },
        )

    specification = (
        AlertSpecification(
            workspace_id=current.workspace_id,
            specification_revision=current.spec.specification_revision,
            variables=(AlertVariable(name="temperature", column="value", kind="decimal"),),
            rules=(AlertRule("hot", "rule-1", "temperature > 85", "high", "Hot"),),
        )
        if scenario == "alert"
        else QualitySpecification(
            workspace_id=current.workspace_id,
            specification_revision=current.spec.specification_revision,
            standard=QualityStandard(
                "standard", "std-1", (QualityMetric("limit", "temperature", "C", upper_bound=Decimal("85")),)
            ),
            mapping=QualityWideMapping(
                identity=QualityIdentityColumns(
                    record_id="record", sample_id="sample", measurement_revision="revision", measured_at="time"
                ),
                metrics=(QualityWideMetric(metric="temperature", value="value", unit="C"),),
            ),
        )
    )
    managed = ManagedExecutionService(
        auth(),
        lookup,
        repository,
        InputCaptureService(
            repository,
            ImmutableReadCatalog((registered(),)),
            RegisteredSourceReader(http_transport=httpx.MockTransport(read_source)),
        ),
        AssessmentService(ImmutableSpecificationCatalog((specification,))),
    )
    app = FastAPI()
    app.include_router(create_managed_router(managed))

    class WorkflowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield frame("workflow_started", native_id=NATIVE, workflow_id=str(current.spec.workflow_id))
            assert current.dify_run_id == NATIVE
            assert not source_calls
            body = json.dumps(claims()).encode()
            signature = hmac.new(("s" * 48).encode(), body, hashlib.sha256).hexdigest()
            with TestClient(app) as client:
                response = client.post(
                    "/enterprise/internal/v1/evaluate",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Enterprise-Key-ID": "key-1",
                        "X-Enterprise-Signature": signature,
                    },
                )
            assert response.status_code == 200, response.text
            assert current.input_snapshot is not None
            assert response.json()["input_snapshot_digest"] == canonical_hash(current.input_snapshot)
            assert response.json()["result"]["conclusion"] == conclusion
            event = {
                "event": "workflow_finished",
                "workflow_run_id": NATIVE,
                "task_id": "task-1",
                "data": {
                    "id": NATIVE,
                    "workflow_id": str(current.spec.workflow_id),
                    "status": "succeeded",
                    "outputs": {"result": response.text},
                },
            }
            yield ("data: " + json.dumps(event) + "\n\n").encode()

    native_calls = []

    def native(request):
        native_calls.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=WorkflowStream())

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(
            WorkflowCredential(
                workspace_id=current.workspace_id,
                app_id=current.spec.app_id,
                secret_ref=current.spec.secret_ref,
                api_key=SecretStr("workflow-key"),
            ),
        ),
        transport=httpx.MockTransport(native),
    )
    final = RunDispatcher(repository, gateway, nonce_factory=lambda: "private-dispatch-nonce").dispatch(
        current.workspace_id,
        current.id,
    )
    assert final.status == "succeeded"
    assert final.result is not None and final.result.conclusion == conclusion
    assert len(native_calls) == len(source_calls) == 1
    assert b"private-dispatch-nonce" not in native_calls[0].content
    assert b"workflow-key" not in native_calls[0].content
    repository.mark_dispatched.assert_called_once()
    repository.complete_run.assert_called_once()
    repository.mark_uncertain.assert_not_called()
    repository.fail_run.assert_not_called()
