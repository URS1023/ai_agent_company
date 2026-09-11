"""Trusted plugin execution attestation around capture and deterministic assessment.

The signer is the deployed managed plugin, using execution metadata supplied by the
native server outside tool/model parameters. A plugin credential alone is not an
execution identity. Streaming dispatch must already have associated the native run;
this service never resolves an enterprise run or its nonce from model inputs.
Repeated calls may reuse a persisted capture, not submit another native workflow.
"""

import asyncio
import hashlib
import hmac
import re
import time
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, StrictInt, ValidationError, field_validator, model_validator

from .contracts import Contract, Identifier, Run, canonical_hash
from .dispatcher import BusinessEnvelope
from .errors import AccessDenied, InvalidState, Unauthenticated
from .input_capture import InputCaptureService
from .ports import EnterpriseRepository


class ExecutionKey(Contract):
    key_id: Identifier
    workspace_id: Identifier
    app_id: Identifier
    secret: SecretStr = Field(repr=False)
    node_ids: frozenset[Identifier] = Field(min_length=1)

    @field_validator("secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not 32 <= len(raw) <= 256 or any(not 33 <= ord(c) <= 126 for c in raw):
            raise ValueError("A bounded server-managed secret of at least 32 ASCII characters is required")
        return value


class NativeExecution(Contract):
    workspace_id: Identifier
    app_id: Identifier
    workflow_id: UUID
    native_run_id: UUID
    node_id: Identifier
    node_execution_id: UUID
    invoke_from: Literal["service-api"]
    issued_at: StrictInt = Field(ge=0)
    expires_at: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def short_lived(self) -> "NativeExecution":
        if not 0 < self.expires_at - self.issued_at <= 60:
            raise ValueError("Execution attestation has an invalid lifetime")
        return self


class ActiveExecutionKey(Contract):
    """One currently active published version, resolved from enterprise storage."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always", hide_input_in_errors=True
    )
    key: ExecutionKey = Field(repr=False, exclude=True)
    workflow_id: UUID

    @model_validator(mode="after")
    def valid_key(self) -> "ActiveExecutionKey":
        ExecutionKey.model_validate(self.key.model_dump())
        if not self.workflow_id.int:
            raise ValueError("Published workflow identity required")
        return self


class ActiveExecutionKeyLookup(Protocol):
    def resolve(self, key_id: str, *, workflow_id: UUID) -> ActiveExecutionKey | None:
        """Return only a live activation; do not cache revocation or read model inputs as grants."""
        ...


class ExecutionAuthenticator:
    _keys: dict[str, ExecutionKey]
    _clock: Callable[[], float]

    def __init__(
        self,
        keys: tuple[ExecutionKey, ...],
        *,
        clock: Callable[[], float] = time.time,
        active_keys: ActiveExecutionKeyLookup | None = None,
    ) -> None:
        copied = tuple(ExecutionKey.model_validate(key.model_dump()) for key in keys)
        if len({key.key_id for key in copied}) != len(copied):
            raise ValueError("Duplicate managed execution key")
        self._keys = {key.key_id: key for key in copied}
        self._clock = clock
        self._active_keys = active_keys

    def verify(self, body: bytes, key_id: str, signature: str) -> NativeExecution:
        key = self._keys.get(key_id)
        if len(body) > 8192 or re.fullmatch(r"[0-9a-f]{64}", signature) is None:
            raise Unauthenticated("invalid_execution_attestation")
        if key is None and self._active_keys is not None:
            # The bounded unverified body selects a candidate, never grants access.
            # HMAC, time, scope and persisted run checks still follow resolution.
            if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key_id) is None:
                raise Unauthenticated("invalid_execution_attestation")
            try:
                candidate = NativeExecution.model_validate_json(body)
                resolved = self._active_keys.resolve(key_id, workflow_id=candidate.workflow_id)
                if resolved is None:
                    raise ValueError("No active execution key")
                resolved = ActiveExecutionKey.model_validate(resolved)
                if resolved.workflow_id != candidate.workflow_id or resolved.key.key_id != key_id:
                    raise ValueError("Execution key resolution mismatch")
                key = ExecutionKey.model_validate(resolved.key.model_dump())
            except Exception:
                raise Unauthenticated("invalid_execution_attestation") from None
        if key is None:
            raise Unauthenticated("invalid_execution_attestation")
        expected = hmac.new(key.secret.get_secret_value().encode("ascii"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise Unauthenticated("invalid_execution_attestation")
        try:
            execution = NativeExecution.model_validate_json(body)
        except ValidationError:
            raise Unauthenticated("invalid_execution_attestation") from None
        now = self._clock()
        if execution.issued_at > now + 5 or execution.expires_at <= now:
            raise Unauthenticated("expired_execution_attestation")
        if (execution.workspace_id, execution.app_id) != (
            key.workspace_id,
            key.app_id,
        ) or execution.node_id not in key.node_ids:
            raise AccessDenied("execution_scope_mismatch")
        return execution


class NativeRunLookup(Protocol):
    def find_native_run(self, workspace_id: str, app_id: str, native_run_id: str) -> Run | None: ...


class AssessmentPort(Protocol):
    def assess(self, run: Run) -> BusinessEnvelope: ...


class ExecutionAssociationPending(InvalidState):
    code = "execution_association_pending"


def _verify_run(run: Run, execution: NativeExecution) -> None:
    if (
        run.workspace_id != execution.workspace_id
        or run.spec.app_id != execution.app_id
        or run.spec.workflow_id != execution.workflow_id
        or run.dify_run_id != str(execution.native_run_id)
    ):
        raise AccessDenied("execution_run_mismatch")
    if run.status not in {"dispatched", "uncertain"} or not run.dispatch_nonce:
        raise InvalidState("execution_not_in_flight")


class ManagedExecutionService:
    authenticator: ExecutionAuthenticator
    lookup: NativeRunLookup
    repository: EnterpriseRepository
    capture: InputCaptureService
    assessment: AssessmentPort

    def __init__(
        self,
        authenticator: ExecutionAuthenticator,
        lookup: NativeRunLookup,
        repository: EnterpriseRepository,
        capture: InputCaptureService,
        assessment: AssessmentPort,
    ) -> None:
        self.authenticator, self.lookup, self.repository = authenticator, lookup, repository
        self.capture, self.assessment = capture, assessment

    async def evaluate(self, body: bytes, key_id: str, signature: str) -> BusinessEnvelope:
        execution = await asyncio.to_thread(self.authenticator.verify, body, key_id, signature)
        run = await asyncio.to_thread(
            self.lookup.find_native_run, execution.workspace_id, execution.app_id, str(execution.native_run_id)
        )
        if run is None:
            raise ExecutionAssociationPending()
        _verify_run(run, execution)
        assert run.dispatch_nonce is not None
        await self.capture.capture(run.workspace_id, run.id, run.dispatch_nonce)
        saved = await asyncio.to_thread(self.repository.get_run, run.workspace_id, run.id)
        _verify_run(saved, execution)
        if saved.id != run.id or saved.dispatch_nonce != run.dispatch_nonce or saved.input_snapshot is None:
            raise InvalidState("execution_capture_mismatch")
        envelope = await asyncio.to_thread(self.assessment.assess, saved)
        if (
            envelope.run_id != saved.id
            or envelope.device_id != saved.spec.device_id
            or envelope.specification_revision != saved.spec.specification_revision
            or envelope.result.scenario != saved.spec.scenario
            or envelope.input_snapshot_digest != canonical_hash(saved.input_snapshot)
        ):
            raise InvalidState("execution_result_mismatch")
        return envelope
