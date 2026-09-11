import json
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr
from test_dify_workflows import binding, client, response_payload

from enterprise_platform.adapters.dify_workflows import (
    DifyProtocolError,
    DifyRejected,
    DifyWorkflowClient,
    DispatchUncertain,
    StreamLimits,
)


def frame(event: str, *, native_id: str = "run-1", task_id: str = "task-1", workflow_id: str | None = None) -> bytes:
    payload = dict(response_payload())
    payload.update(event=event, workflow_run_id=native_id, task_id=task_id)
    payload["data"] = {**payload["data"], "id": native_id}
    if workflow_id is not None:
        payload["data"]["workflow_id"] = workflow_id
    return ("data: " + json.dumps(payload) + "\n\n").encode()


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def test_started_callback_precedes_next_frame_and_final_return() -> None:
    seen: list[str] = []
    requests: list[httpx.Request] = []

    class CheckedStream(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started")
            assert seen == ["run-1"]
            yield frame("workflow_finished")

    stream = CheckedStream(())

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)

    with client(handler) as adapter:
        result = adapter.run_stream(
            binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
        )

    assert result.status == "succeeded"
    assert seen == ["run-1"]
    assert stream.closed
    assert len(requests) == 1
    assert json.loads(requests[0].content)["response_mode"] == "streaming"


@pytest.mark.parametrize(
    "chunks",
    [
        (frame("workflow_finished"),),
        (frame("workflow_started"),),
        (frame("workflow_started"), frame("workflow_started"), frame("workflow_finished")),
        (frame("workflow_started"), frame("workflow_finished", native_id="other")),
        (frame("workflow_started"), frame("workflow_finished", task_id="other")),
        (frame("workflow_started", workflow_id="00000000-0000-0000-0000-000000000001"),),
        (frame("workflow_started"), frame("workflow_finished"), frame("workflow_finished")),
        (b"data: not-json\n\n",),
        (b"data: \xff\n\n",),
        (frame("workflow_started"), b"data: {"),
    ],
)
def test_invalid_or_incomplete_stream_never_returns_a_result(chunks: tuple[bytes, ...]) -> None:
    stream = Chunks(chunks)
    seen: list[str] = []
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
    ) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run_stream(
                binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
            )
    assert len(seen) <= 1
    assert stream.closed


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 302, 500, 503])
def test_stream_http_failure_never_retries_or_invokes_started(code: int) -> None:
    requests: list[httpx.Request] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(code, text="private app-private-key", headers={"location": "https://other.test"})

    with client(handler) as adapter:
        with pytest.raises(DifyRejected if 400 <= code < 500 else DispatchUncertain) as error:
            adapter.run_stream(
                binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
            )
    assert "private" not in str(error.value)
    assert seen == []
    assert len(requests) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "application/json"},
        {
            "Content-Type": "text/event-stream",
            "Content-Encoding": "gzip",
        },
    ],
)
def test_unexpected_content_type_or_encoding_is_not_treated_as_sse(headers: dict[str, str]) -> None:
    with client(lambda request: httpx.Response(200, headers=headers, stream=Chunks(()))) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError, httpx.DecodingError]
)
def test_disconnect_after_started_retains_callback_once_and_redacts_errors(failure: type[httpx.RequestError]) -> None:
    class Disconnected(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started")
            raise failure("private app-private-key")

    stream = Disconnected(())
    seen: list[str] = []
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
    ) as adapter:
        with pytest.raises(DispatchUncertain) as error:
            adapter.run_stream(
                binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
            )
    assert seen == ["run-1"]
    assert "private" not in str(error.value)
    assert stream.closed


def test_callback_failure_aborts_before_next_event_without_reinvocation() -> None:
    class Unread(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started")
            pytest.fail("an unconfirmed association must abort stream consumption")

    calls = 0

    def callback(event: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private database detail")

    stream = Unread(())
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
    ) as adapter:
        with pytest.raises(DispatchUncertain) as error:
            adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=callback)
    assert calls == 1
    assert stream.closed
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "limits,chunks",
    [
        (StreamLimits(max_bytes=20), (b":" + b"a" * 20,)),
        (StreamLimits(max_event_bytes=20), (b"data: " + b"a" * 21,)),
        (StreamLimits(max_events=1), (b"event: ping\n\nevent: ping\n\n",)),
    ],
)
def test_stream_resource_limits_apply_even_to_incomplete_frames_and_pings(
    limits: StreamLimits, chunks: tuple[bytes, ...]
) -> None:
    with DifyWorkflowClient(
        base_url="https://dify.test/v1",
        workspace_id="workspace-1",
        app_id="app-1",
        api_key=SecretStr("private-key"),
        stream_limits=limits,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks(chunks))
        ),
    ) as adapter:
        with pytest.raises(DifyProtocolError, match="limit"):
            adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)


def test_continuous_heartbeats_do_not_extend_overall_deadline() -> None:
    now = 0.0
    seen: list[str] = []

    class Heartbeats(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal now
            yield frame("workflow_started")
            for _ in range(5):
                now += 1
                yield b"event: ping\n\n"
            pytest.fail("deadline must end a continuously active stream")

    with DifyWorkflowClient(
        base_url="https://dify.test/v1",
        workspace_id="workspace-1",
        app_id="app-1",
        api_key=SecretStr("private-key"),
        stream_limits=StreamLimits(total_seconds=2),
        clock=lambda: now,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Heartbeats(()))
        ),
    ) as adapter:
        with pytest.raises(DispatchUncertain, match="deadline"):
            adapter.run_stream(
                binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
            )
    assert seen == ["run-1"]
    assert now == 2


def test_arbitrary_chunk_boundaries_crlf_multiline_json_comments_and_native_ping() -> None:
    started = frame("workflow_started").replace(b', "task_id"', b',\ndata: "task_id"')
    progress = frame("text_chunk").replace(b'"outputs":', '"label":"设备", "outputs":'.encode())
    wire = b": comment\n\nevent: ping\n\n" + started + progress + frame("workflow_finished")
    wire = wire.replace(b"\n", b"\r\n")
    seen: list[str] = []
    stream = Chunks(tuple(wire[i : i + 1] for i in range(len(wire))))
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=utf-8"}, stream=stream)
    ) as adapter:
        result = adapter.run_stream(
            binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
        )
    assert result.status == "succeeded"
    assert seen == ["run-1"]


@pytest.mark.parametrize(
    "bad_frame",
    [
        frame("workflow_started").replace(
            b'"workflow_run_id": "run-1",', b'"workflow_run_id":"wrong","workflow_run_id":"run-1",'
        ),
        frame("workflow_started").replace(b'"id": "run-1"', b'"id": "wrong"'),
        frame("workflow_started").replace(b'"task_id": "task-1"', b'"task_id": 1'),
        frame("workflow_started").replace(b'"event": "workflow_started"', b'"event": []'),
        frame("workflow_started").replace(b'"outputs":', b'"value":NaN,"outputs":'),
        frame("workflow_started").replace(b'"workflow_run_id": "run-1"', b'"workflow_run_id": "bad/id"'),
        b"event: error\n\n",
        b"not an SSE field\n\n",
        b"data: {}\rgarbage\n\n",
    ],
)
def test_malformed_start_never_reaches_association(bad_frame: bytes) -> None:
    seen: list[str] = []
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks((bad_frame,)))
    ) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run_stream(
                binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
            )
    assert seen == []


@pytest.mark.parametrize("status", ["failed", "stopped", "partial-succeeded"])
def test_stream_execution_failure_drops_business_outputs(status: str) -> None:
    terminal = frame("workflow_finished").replace(b'"status": "succeeded"', f'"status": "{status}"'.encode())
    with client(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Chunks((frame("workflow_started"), terminal))
        )
    ) as adapter:
        result = adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)
    assert result.status == status
    assert result.outputs == {}


def test_native_pause_shape_preserves_run_identity_without_business_outputs() -> None:
    paused = {
        "event": "workflow_paused",
        "workflow_run_id": "run-1",
        "task_id": "task-1",
        "data": {"workflow_run_id": "run-1", "status": "paused", "outputs": {"result": "not final"}},
    }
    terminal = ("data: " + json.dumps(paused) + "\n\n").encode()
    with client(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Chunks((frame("workflow_started"), terminal))
        )
    ) as adapter:
        result = adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)
    assert result.status == "paused"
    assert result.run_id == "run-1"
    assert result.outputs == {}


def test_excessive_json_nesting_is_a_protocol_error_not_a_raw_parser_crash() -> None:
    wire = b"data: " + b"[" * 20000 + b"0" + b"]" * 20000 + b"\n\n"
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=wire)
    ) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)


@pytest.mark.parametrize("after_finished", [False, True])
def test_native_audio_is_bounded_transport_data_not_a_second_terminal_result(after_finished: bool) -> None:
    audio = {"event": "tts_message", "workflow_run_id": "run-1", "task_id": "task-1", "audio": "YXVkaW8="}
    audio_end = {**audio, "event": "tts_message_end", "audio": ""}
    encoded = tuple(("data: " + json.dumps(value) + "\n\n").encode() for value in (audio, audio_end))
    chunks = (frame("workflow_started"),)
    chunks += (frame("workflow_finished"), *encoded) if after_finished else (*encoded, frame("workflow_finished"))
    seen: list[str] = []
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks(chunks))
    ) as adapter:
        result = adapter.run_stream(
            binding(), actor_id="actor-1", inputs={}, on_started=lambda event: seen.append(event.run_id)
        )
    assert result.status == "succeeded"
    assert seen == ["run-1"]


@pytest.mark.parametrize("field,value", [("task_id", "wrong"), ("workflow_run_id", "wrong"), ("audio", 123)])
def test_native_audio_does_not_bypass_protocol_or_execution_identity(field: str, value: str | int) -> None:
    audio = {"event": "tts_message", "workflow_run_id": "run-1", "task_id": "task-1", "audio": ""}
    wire = ("data: " + json.dumps({**audio, field: value}) + "\n\n").encode()
    chunks = (frame("workflow_started"), frame("workflow_finished"), wire)
    with client(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks(chunks))
    ) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run_stream(binding(), actor_id="actor-1", inputs={}, on_started=lambda event: None)
