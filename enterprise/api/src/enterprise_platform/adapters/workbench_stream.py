"""Bounded native chat SSE framing, not dispatch, receipt association or completion proof.

Unknown JSON event kinds are preserved for forward-compatible native UI handling.
A clean EOF or message_end does not prove generation completion; the caller must
reconcile task/message identity and persisted state before releasing a branch.
"""

import json
import math
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Never

from pydantic import JsonValue, TypeAdapter

from enterprise_platform.application.contracts import JsonObject
from enterprise_platform.application.errors import DependencyUnavailable

_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ChatStreamInvalid(DependencyUnavailable):
    code = "native_chat_stream_invalid"


def _unique(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> Never:
    raise ValueError("Nonfinite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _event(lines: list[str]) -> JsonObject:
    parsed = json.loads("\n".join(lines), object_pairs_hook=_unique, parse_constant=_constant, parse_float=_float)
    value = _OBJECT.validate_python(parsed, strict=True)
    kind = value.get("event")
    if not isinstance(kind, str) or not kind or len(kind) > 128:
        raise ValueError("Missing native event kind")
    return value


async def read_chat_events(
    source: AsyncIterable[bytes], *, max_frame_bytes: int = 1024 * 1024, max_total_bytes: int = 32 * 1024 * 1024
) -> AsyncGenerator[JsonObject, None]:
    """Yield only blank-line-delimited JSON events; never retry or infer terminal state."""
    if (
        type(max_frame_bytes) is not int
        or type(max_total_bytes) is not int
        or min(max_frame_bytes, max_total_bytes) <= 0
    ):
        raise ValueError("Positive integer stream bounds required")
    line = bytearray()
    data: list[str] = []
    total = frame_size = 0
    after_cr = False
    first_line = True
    try:
        async for chunk in source:
            total += len(chunk)
            if total > max_total_bytes:
                raise ChatStreamInvalid()
            for byte in chunk:
                if after_cr and byte == 10:
                    after_cr = False
                    continue
                after_cr = byte == 13
                frame_size += 1
                if frame_size > max_frame_bytes:
                    raise ChatStreamInvalid()
                if byte not in (10, 13):
                    line.append(byte)
                    continue
                text = line.decode("utf-8")
                line.clear()
                if first_line:
                    text = text.removeprefix("\ufeff")
                    first_line = False
                if not text:
                    if data:
                        yield _event(data)
                    data = []
                    frame_size = 0
                elif not text.startswith(":"):
                    field, _, value = text.partition(":")
                    if field == "data":
                        data.append(value.removeprefix(" "))
        if line or data:
            raise ChatStreamInvalid()
    except (ValueError, RecursionError):
        raise ChatStreamInvalid() from None
