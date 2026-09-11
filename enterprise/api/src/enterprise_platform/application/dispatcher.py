"""Claim-before-dispatch and verified result return for managed worker jobs.

Transport uncertainty retains the device/scenario lane. Reconciliation only reads
an existing native execution; it never submits a replacement workflow run.
"""

import secrets
from collections.abc import Callable
from typing import Protocol

from pydantic import ValidationError

from enterprise_platform.adapters.dify_workflows import (
    DifyProtocolError,
    DifyRejected,
    DispatchUncertain,
    WorkflowResult,
    WorkflowStarted,
)

from .contracts import BusinessResult, Contract, Identifier, JsonObject, Run, canonical_hash, canonical_json
from .errors import AccessDenied, EnterpriseError, InvalidInput, InvalidState
from .input_capture import restore_capture
from .ports import EnterpriseRepository


class WorkflowGateway(Protocol):
    def run(self, run: Run, inputs: JsonObject, *, on_started: Callable[[WorkflowStarted], None]) -> WorkflowResult: ...
    def get_run(self, run: Run) -> WorkflowResult: ...


class WorkflowPreparationFailed(Exception):
    """A server configuration failure known to occur before native dispatch."""


class BusinessEnvelope(Contract):
    run_id: Identifier
    device_id: Identifier
    specification_revision: Identifier
    input_snapshot_digest: str
    result: BusinessResult


class RunDispatcher:
    repository: EnterpriseRepository
    gateway: WorkflowGateway
    nonce_factory: Callable[[], str]
    worker_id: str

    def __init__(
        self,
        repository: EnterpriseRepository,
        gateway: WorkflowGateway,
        *,
        nonce_factory: Callable[[], str] = secrets.token_urlsafe,
        worker_id: str = "enterprise-worker",
    ) -> None:
        self.repository, self.gateway = repository, gateway
        self.nonce_factory, self.worker_id = nonce_factory, worker_id

    def dispatch(self, workspace_id: str, run_id: str) -> Run:
        nonce = self.nonce_factory()
        run = self.repository.claim_run(workspace_id, run_id, nonce, actor_id=self.worker_id)
        context: JsonObject = {
            "run_id": run.id,
            "workspace_id": run.workspace_id,
            "device_id": run.spec.device_id,
            "scenario": run.spec.scenario,
            "specification_revision": run.spec.specification_revision,
            "source_id": run.spec.source_id,
            "source_revision": run.spec.source_revision,
            "read_id": run.spec.read_id,
            "read_revision": run.spec.read_revision,
            "recompute_of": run.spec.recompute_of,
            "input_snapshot_digest": canonical_hash(run.input_snapshot) if run.input_snapshot is not None else None,
        }
        inputs: JsonObject = {**run.spec.parameters, "enterprise_context": canonical_json(context)}
        associated: WorkflowStarted | None = None

        def on_started(event: WorkflowStarted) -> None:
            nonlocal associated
            if associated is not None or event.workflow_id != run.spec.workflow_id:
                raise DifyProtocolError("Native start identity is duplicated or mismatched.")
            try:
                self.repository.mark_dispatched(workspace_id, run_id, nonce, event.run_id, actor_id=self.worker_id)
            except EnterpriseError:
                raise DispatchUncertain("Native start association was not confirmed.") from None
            associated = event

        try:
            native = self.gateway.run(run, inputs, on_started=on_started)
            if associated is None or (
                native.workflow_id != associated.workflow_id
                or native.run_id != associated.run_id
                or native.task_id != associated.task_id
            ):
                raise DifyProtocolError("Native final response has no matching start association.")
        except WorkflowPreparationFailed:
            if associated is not None:
                return self.repository.mark_uncertain(
                    workspace_id, run_id, nonce, "dify_dispatch_uncertain", actor_id=self.worker_id
                )
            return self.repository.fail_run(
                workspace_id, run_id, nonce, "workflow_configuration_unavailable", actor_id=self.worker_id
            )
        except DifyRejected as error:
            if associated is not None:
                return self.repository.mark_uncertain(
                    workspace_id, run_id, nonce, "dify_dispatch_uncertain", actor_id=self.worker_id
                )
            return self.repository.fail_run(
                workspace_id, run_id, nonce, f"dify_rejected_{error.status_code}", actor_id=self.worker_id
            )
        except DispatchUncertain:
            return self.repository.mark_uncertain(
                workspace_id, run_id, nonce, "dify_dispatch_uncertain", actor_id=self.worker_id
            )
        return self._receive(
            run.model_copy(update={"status": "dispatched", "dify_run_id": associated.run_id}), nonce, native
        )

    def reconcile(self, workspace_id: str, run_id: str) -> Run:
        run = self.repository.get_run(workspace_id, run_id)
        if run.status in {"succeeded", "failed", "cancelled"}:
            return run
        if run.status not in {"dispatched", "uncertain"} or not run.dify_run_id or not run.dispatch_nonce:
            raise InvalidState("native_execution_identity_required")
        try:
            native = self.gateway.get_run(run)
        except (DispatchUncertain, DifyRejected, WorkflowPreparationFailed):
            return self.repository.mark_uncertain(
                workspace_id,
                run_id,
                run.dispatch_nonce,
                "dify_reconcile_uncertain",
                actor_id=self.worker_id,
            )
        return self._receive(run, run.dispatch_nonce, native)

    def _receive(self, run: Run, nonce: str, native: WorkflowResult) -> Run:
        ws, run_id = run.workspace_id, run.id
        if native.workflow_id != run.spec.workflow_id or (run.dify_run_id and native.run_id != run.dify_run_id):
            return self.repository.mark_uncertain(
                ws, run_id, nonce, "native_execution_identity_mismatch", actor_id=self.worker_id
            )
        if run.status != "dispatched" or run.dify_run_id is None:
            self.repository.mark_dispatched(ws, run_id, nonce, native.run_id, actor_id=self.worker_id)
        if native.status in {"running", "paused"}:
            return self.repository.get_run(ws, run_id)
        if native.status != "succeeded":
            return self.repository.fail_run(
                ws, run_id, nonce, f"dify_execution_{native.status}", actor_id=self.worker_id
            )
        current = self.repository.get_run(ws, run_id)
        if current.input_snapshot is None:
            return self.repository.fail_run(ws, run_id, nonce, "missing_input_snapshot", actor_id=self.worker_id)
        try:
            captured_rows = restore_capture(current)
            value = native.outputs.get("result")
            envelope = (
                BusinessEnvelope.model_validate_json(value)
                if isinstance(value, str)
                else BusinessEnvelope.model_validate(value)
            )
            if (
                envelope.run_id != run_id
                or envelope.device_id != run.spec.device_id
                or envelope.specification_revision != run.spec.specification_revision
                or envelope.result.scenario != run.spec.scenario
                or envelope.input_snapshot_digest != canonical_hash(current.input_snapshot)
                or (not captured_rows.rows and envelope.result.conclusion not in {"no_data", "incomplete"})
            ):
                raise ValueError("Result does not match this frozen execution")
        except (ValidationError, ValueError, InvalidInput, InvalidState, AccessDenied):
            return self.repository.fail_run(ws, run_id, nonce, "invalid_business_output", actor_id=self.worker_id)
        digest = canonical_hash(envelope.result.model_dump(mode="json"))
        return self.repository.complete_run(ws, run_id, nonce, envelope.result, digest, actor_id=self.worker_id)
