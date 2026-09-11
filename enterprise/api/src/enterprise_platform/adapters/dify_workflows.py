"""Pinned native workflow execution without retries or latest-version fallbacks.

The caller resolves workspace membership and application credentials before
constructing this adapter. Native Dify remains responsible for its API token,
workflow version, publication and entitlement checks.
"""

import json
import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Annotated, Literal, Self
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, StringConstraints, TypeAdapter, ValidationError

NativeRunId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


def validate_native_run_id(value: str) -> str:
    return TypeAdapter(NativeRunId).validate_python(value)


class DifyRejected(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Dify rejected the request (HTTP {status_code}).")


class DispatchUncertain(Exception):
    """The caller must reconcile rather than blindly resubmit a side effect."""


class DifyProtocolError(DispatchUncertain):
    """A dispatch may have run, but its response did not meet the contract."""


@dataclass(frozen=True)
class PinnedWorkflowTarget:
    """Non-device workflows still require a workspace, application and exact revision."""

    workspace_id: str
    app_id: str
    workflow_id: UUID

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.app_id.strip() or not isinstance(self.workflow_id, UUID):
            raise ValueError("Pinned workflow requires workspace, application and workflow UUID")


@dataclass(frozen=True)
class WorkflowBinding:
    workspace_id: str
    device_id: str
    scenario: Literal["alert", "quality"]
    app_id: str
    workflow_id: UUID
    specification_revision: str

    def __post_init__(self) -> None:
        if not all((self.workspace_id, self.device_id, self.app_id, self.specification_revision)):
            raise ValueError("binding requires workspace, device, application and specification revision")
        if self.scenario not in {"alert", "quality"} or not isinstance(self.workflow_id, UUID):
            raise ValueError("binding requires a supported scenario and a pinned workflow UUID")


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: NativeRunId
    task_id: str | None = None
    workflow_id: UUID
    status: Literal["running", "succeeded", "failed", "stopped", "paused", "partial-succeeded"]
    outputs: dict[str, JsonValue]


class _RunData(BaseModel):
    id: NativeRunId
    workflow_id: UUID
    status: Literal["running", "succeeded", "failed", "stopped", "paused", "partial-succeeded"]
    outputs: dict[str, JsonValue] | None = None


class _BlockingResponse(BaseModel):
    workflow_run_id: NativeRunId
    task_id: str = Field(min_length=1)
    data: _RunData


class WorkflowStarted(BaseModel):
    """Native execution identity, never inferred from workflow inputs."""

    model_config = ConfigDict(frozen=True)
    run_id: NativeRunId
    task_id: NativeRunId
    workflow_id: UUID


@dataclass(frozen=True)
class StreamLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_event_bytes: int = 2 * 1024 * 1024
    max_events: int = 10000
    total_seconds: float = 120.0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0 for value in (self.max_bytes, self.max_event_bytes, self.max_events)
        ):
            raise ValueError("Stream size and count limits must be positive integers")
        if not math.isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("Stream duration must be positive and finite")


class _StartData(BaseModel):
    id: NativeRunId
    workflow_id: UUID


class _StreamEvent(BaseModel):
    event: str = Field(strict=True, min_length=1, max_length=64)
    workflow_run_id: NativeRunId
    task_id: NativeRunId
    data: dict[str, JsonValue]


class _AudioEvent(BaseModel):
    event: Literal["tts_message", "tts_message_end"]
    workflow_run_id: NativeRunId
    task_id: NativeRunId
    audio: str = Field(strict=True)


_PROGRESS_EVENTS = frozenset(
    {
        "node_started",
        "node_finished",
        "node_retry",
        "iteration_started",
        "iteration_next",
        "iteration_completed",
        "loop_started",
        "loop_next",
        "loop_completed",
        "text_chunk",
        "text_replace",
        "reasoning_chunk",
        "agent_log",
        "human_input_required",
        "human_input_form_filled",
        "human_input_form_timeout",
    }
)


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> JsonValue:
    raise ValueError("Non-finite JSON constant")


def _deadline(clock: Callable[[], float], deadline: float) -> None:
    if clock() >= deadline:
        raise DispatchUncertain("Dify stream deadline exceeded; reconcile before submitting again.")


def _sse_frames(
    response: httpx.Response, limits: StreamLimits, clock: Callable[[], float], deadline: float
) -> Iterator[str | None]:
    """Parse native LF/CRLF SSE without buffering the complete stream or replacing invalid UTF-8."""
    pending = b""
    data: list[str] = []
    event_name = ""
    total = event_size = events = 0
    for chunk in response.iter_bytes():
        _deadline(clock, deadline)
        total += len(chunk)
        if total > limits.max_bytes:
            raise DifyProtocolError("Dify stream exceeded the configured byte limit.")
        pending += chunk
        while b"\n" in pending:
            raw, pending = pending.split(b"\n", 1)
            event_size += len(raw) + 1
            if event_size > limits.max_event_bytes:
                raise DifyProtocolError("Dify SSE frame exceeded the configured size limit.")
            line = raw.removesuffix(b"\r").decode("utf-8", errors="strict")
            if "\r" in line or "\x00" in line:
                raise DifyProtocolError("Dify returned malformed SSE framing.")
            if not line:
                events += 1
                if events > limits.max_events:
                    raise DifyProtocolError("Dify stream exceeded the configured event limit.")
                _deadline(clock, deadline)
                if data:
                    if event_name not in {"", "message"}:
                        raise DifyProtocolError("Dify returned an unexpected SSE event field.")
                    yield "\n".join(data)
                elif event_name not in {"", "ping"}:
                    raise DifyProtocolError("Dify returned an SSE event without data.")
                else:
                    yield None
                data, event_name, event_size = [], "", 0
            elif line.startswith(":"):
                continue
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif line.startswith("event:") and not event_name:
                event_name = line[6:].removeprefix(" ")
            else:
                raise DifyProtocolError("Dify returned an unsupported SSE field.")
        if event_size + len(pending) > limits.max_event_bytes:
            raise DifyProtocolError("Dify SSE frame exceeded the configured size limit.")
    _deadline(clock, deadline)
    if pending or event_size:
        raise DifyProtocolError("Dify stream ended inside an SSE frame.")


class DifyWorkflowClient:
    def __init__(
        self,
        *,
        base_url: str,
        workspace_id: str,
        app_id: str,
        api_key: SecretStr,
        transport: httpx.BaseTransport | None = None,
        stream_limits: StreamLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("base_url must be a server-configured HTTP endpoint without embedded credentials")
        if url.query or url.fragment or not url.path.rstrip("/").endswith("/v1"):
            raise ValueError("base_url must end in /v1 without a query or fragment")
        if not workspace_id or not app_id or not api_key.get_secret_value():
            raise ValueError("workspace, application and API credential are required")
        self._workspace_id = workspace_id
        self._app_id = app_id
        self._stream_limits, self._clock = stream_limits or StreamLimits(), clock
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=httpx.Timeout(120.0, connect=5.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _check_binding(self, binding: WorkflowBinding | PinnedWorkflowTarget) -> None:
        if binding.workspace_id != self._workspace_id or binding.app_id != self._app_id:
            raise ValueError("binding does not match the adapter's workspace and application")

    def _request(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> bytes:
        try:
            with self._client.stream(method, path, json=payload) as response:
                if 400 <= response.status_code < 500:
                    raise DifyRejected(response.status_code)
                if response.status_code != 200:
                    raise DispatchUncertain("Dify dispatch is unresolved; reconcile before submitting again.")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise DifyProtocolError("Dify response exceeded the configured size limit.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.DecodingError:
            raise DifyProtocolError("Dify response decoding failed.") from None
        except httpx.RequestError:
            raise DispatchUncertain("Dify transport interrupted; reconcile before submitting again.") from None

    @staticmethod
    def _result(
        data: _RunData, binding: WorkflowBinding | PinnedWorkflowTarget, task_id: str | None = None
    ) -> WorkflowResult:
        if data.workflow_id != binding.workflow_id:
            raise DifyProtocolError("Dify returned a different workflow revision.")
        return WorkflowResult(
            run_id=data.id,
            task_id=task_id,
            workflow_id=data.workflow_id,
            status=data.status,
            # Execution errors or partial executions never masquerade as final business decisions.
            outputs=(data.outputs or {}) if data.status == "succeeded" else {},
        )

    def run(
        self, binding: WorkflowBinding | PinnedWorkflowTarget, *, actor_id: str, inputs: dict[str, JsonValue]
    ) -> WorkflowResult:
        self._check_binding(binding)
        if not actor_id.strip():
            raise ValueError("actor_id is required and must come from the authenticated application service")
        body = self._request(
            "POST",
            f"workflows/{binding.workflow_id}/run",
            {"inputs": inputs, "user": actor_id, "response_mode": "blocking"},
        )
        try:
            response = _BlockingResponse.model_validate_json(body)
        except ValidationError:
            raise DifyProtocolError("Dify returned an invalid workflow response.") from None
        if response.workflow_run_id != response.data.id:
            raise DifyProtocolError("Dify returned conflicting execution identities.")
        return self._result(response.data, binding, response.task_id)

    def get_run(self, binding: WorkflowBinding, run_id: str) -> WorkflowResult:
        self._check_binding(binding)
        validate_native_run_id(run_id)
        body = self._request("GET", f"workflows/run/{quote(run_id, safe='')}")
        try:
            data = _RunData.model_validate_json(body)
        except ValidationError:
            raise DifyProtocolError("Dify returned invalid execution details.") from None
        if data.id != run_id:
            raise DifyProtocolError("Dify returned a different execution identity.")
        return self._result(data, binding)

    def run_stream(
        self,
        binding: WorkflowBinding,
        *,
        actor_id: str,
        inputs: dict[str, JsonValue],
        on_started: Callable[[WorkflowStarted], None],
    ) -> WorkflowResult:
        """Associate once on native started, then return once after a complete, consistent stream.

        A failed association aborts consumption. No retry, polling or model-input
        identity fallback is performed. The monotonic deadline includes heartbeats;
        a currently blocked sync read is additionally bounded by HTTPX's I/O timeout.
        """
        self._check_binding(binding)
        if not actor_id.strip():
            raise ValueError("actor_id is required and must come from the authenticated application service")
        deadline = self._clock() + self._stream_limits.total_seconds
        started: WorkflowStarted | None = None
        final: WorkflowResult | None = None
        try:
            with self._client.stream(
                "POST",
                f"workflows/{binding.workflow_id}/run",
                json={"inputs": inputs, "user": actor_id, "response_mode": "streaming"},
                headers={"Accept": "text/event-stream", "Accept-Encoding": "identity"},
                timeout=httpx.Timeout(min(120.0, self._stream_limits.total_seconds), connect=5.0, pool=5.0),
            ) as response:
                if 400 <= response.status_code < 500:
                    raise DifyRejected(response.status_code)
                if response.status_code != 200:
                    raise DispatchUncertain("Dify dispatch is unresolved; reconcile before submitting again.")
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                    raise DifyProtocolError("Dify returned a non-SSE stream response.")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise DifyProtocolError("Dify returned unsupported stream content encoding.")
                for text in _sse_frames(response, self._stream_limits, self._clock, deadline):
                    if text is None:
                        continue
                    value: JsonValue = json.loads(
                        text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
                    )
                    # Native TTS can drain after workflow_finished; it is not another business result.
                    kind = value.get("event") if isinstance(value, dict) else None
                    if isinstance(kind, str) and kind in {"tts_message", "tts_message_end"}:
                        audio = _AudioEvent.model_validate(value)
                        if (
                            started is None
                            or audio.workflow_run_id != started.run_id
                            or audio.task_id != started.task_id
                        ):
                            raise DifyProtocolError("Dify returned an unassociated or conflicting audio event.")
                        continue
                    event = _StreamEvent.model_validate(value)
                    if final is not None:
                        raise DifyProtocolError("Dify emitted data after its terminal event.")
                    if event.event == "workflow_started":
                        data = _StartData.model_validate(event.data)
                        if (
                            started is not None
                            or data.id != event.workflow_run_id
                            or data.workflow_id != binding.workflow_id
                        ):
                            raise DifyProtocolError("Dify returned conflicting start identities.")
                        started = WorkflowStarted(run_id=data.id, workflow_id=data.workflow_id, task_id=event.task_id)
                        try:
                            on_started(started)
                        except Exception:
                            raise DispatchUncertain("Dify start association was not confirmed.") from None
                    elif started is None or event.workflow_run_id != started.run_id or event.task_id != started.task_id:
                        raise DifyProtocolError("Dify returned an unassociated or conflicting execution event.")
                    elif event.event == "workflow_finished":
                        finished = _RunData.model_validate(event.data)
                        if finished.id != started.run_id or finished.status in {"running", "paused"}:
                            raise DifyProtocolError("Dify returned an invalid terminal identity or status.")
                        final = self._result(finished, binding, started.task_id)
                    elif event.event == "workflow_paused":
                        if event.data.get("workflow_run_id") != started.run_id or event.data.get("status") != "paused":
                            raise DifyProtocolError("Dify returned an invalid pause event.")
                        final = WorkflowResult(
                            run_id=started.run_id,
                            task_id=started.task_id,
                            workflow_id=started.workflow_id,
                            status="paused",
                            outputs={},
                        )
                    elif event.event not in _PROGRESS_EVENTS:
                        raise DifyProtocolError("Dify returned an unsupported or error execution event.")
        except (ValueError, RecursionError, httpx.DecodingError):
            raise DifyProtocolError("Dify returned an invalid stream response.") from None
        except httpx.RequestError:
            raise DispatchUncertain("Dify transport interrupted; reconcile before submitting again.") from None
        if final is None:
            raise DifyProtocolError("Dify stream ended without a terminal event.")
        return final
