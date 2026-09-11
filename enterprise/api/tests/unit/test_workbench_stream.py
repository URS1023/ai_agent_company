import asyncio
from importlib import import_module

import pytest


def parse(chunks, **limits):
    async def source():
        for chunk in chunks:
            yield chunk

    async def collect():
        parser = import_module("enterprise_platform.adapters.workbench_stream").read_chat_events
        return [event async for event in parser(source(), **limits)]

    return asyncio.run(collect())


def test_utf8_events_survive_every_byte_boundary_and_crlf():
    data = ': heartbeat\r\ndata: {"event":"message","answer":"设备"}\r\n\r\ndata: {"event":"message_end"}\n\n'.encode()
    assert parse([data[index : index + 1] for index in range(len(data))]) == [
        {"event": "message", "answer": "设备"},
        {"event": "message_end"},
    ]


def test_multiline_data_and_ignored_sse_fields():
    assert parse([b'event: ignored\nid: 4\nretry: 2000\ndata: {"event":\ndata: "ping"}\n\n']) == [{"event": "ping"}]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"event":"message","event":"message_end"}',
        b'{"event":"message","value":NaN}',
        b"[]",
        b"{}",
        b'{"event":1}',
        b"\xff",
    ],
)
def test_invalid_or_ambiguous_json_is_rejected(payload):
    module = import_module("enterprise_platform.adapters.workbench_stream")
    with pytest.raises(module.ChatStreamInvalid):
        parse([b"data: " + payload + b"\n\n"])


def test_truncated_data_is_not_treated_as_a_completed_event():
    module = import_module("enterprise_platform.adapters.workbench_stream")
    with pytest.raises(module.ChatStreamInvalid):
        parse([b'data: {"event":"message_end"}'])


def test_frame_limit_includes_unterminated_lines():
    module = import_module("enterprise_platform.adapters.workbench_stream")
    with pytest.raises(module.ChatStreamInvalid):
        parse([b"data: " + b"x" * 1024], max_frame_bytes=128)


def test_total_limit_includes_heartbeat_bytes():
    module = import_module("enterprise_platform.adapters.workbench_stream")
    with pytest.raises(module.ChatStreamInvalid):
        parse([b": ping\n\n"] * 20, max_total_bytes=64)


def test_pause_message_end_and_unknown_events_are_preserved_without_terminal_inference():
    events = parse(
        [
            b'data: {"event":"workflow_paused"}\n\ndata: {"event":"message_end"}\n\n'
            b'data: {"event":"future_event","data":{}}\n\n'
        ]
    )
    assert [event["event"] for event in events] == ["workflow_paused", "message_end", "future_event"]


def test_bom_and_cr_only_stream_are_supported():
    assert parse([b'\xef\xbb\xbfdata: {"event":"message"}\r', b"\r"]) == [{"event": "message"}]


def test_numeric_overflow_is_rejected():
    module = import_module("enterprise_platform.adapters.workbench_stream")
    with pytest.raises(module.ChatStreamInvalid):
        parse([b'data: {"event":"message","value":1e400}\n\n'])


def test_events_are_yielded_before_source_finishes_and_cancellation_propagates():
    async def scenario():
        async def source():
            yield b'data: {"event":"message","answer":"first"}\n\n'
            raise asyncio.CancelledError()

        parser = import_module("enterprise_platform.adapters.workbench_stream").read_chat_events(source())
        assert await anext(parser) == {"event": "message", "answer": "first"}
        with pytest.raises(asyncio.CancelledError):
            await anext(parser)

    asyncio.run(scenario())
