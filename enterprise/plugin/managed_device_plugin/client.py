"""Exact-byte signed evaluation; only pre-read association-pending is retryable.

This client never dispatches workflows, sends model identity parameters, or looks
up nonces. A monotonic total deadline bounds continuously arriving data and retry
waits; a currently blocked sync transport is additionally bounded by HTTPX timeouts.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx
from pydantic import JsonValue

from .models import BusinessEnvelope, ExecutionMetadata, PluginCredentials, PluginFailure, parse_metadata

MAX_RESPONSE_BYTES = 512 * 1024


@dataclass(frozen=True)
class EvaluationLimits:
    total_seconds: float = 60.0
    association_seconds: float = 5.0
    retry_interval: float = 0.25
    max_attempts: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.total_seconds <= 60 or not 0 < self.association_seconds <= self.total_seconds:
            raise ValueError("Invalid evaluation deadline")
        if not 0 < self.retry_interval <= self.association_seconds or not 1 <= self.max_attempts <= 20:
            raise ValueError("Invalid association retry policy")


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> JsonValue:
    raise ValueError("Non-finite JSON constant")


def _decode(body: bytes) -> JsonValue:
    value: JsonValue = json.loads(
        body.decode("utf-8", errors="strict"), object_pairs_hook=_object, parse_constant=_invalid_constant
    )
    return value


def _contains_secret(value: JsonValue, secret: str) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str) and secret in current:
            return True
        if isinstance(current, dict):
            if any(secret in key for key in current):
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


class EvaluationClient:
    def __init__(
        self,
        credentials: PluginCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        wall_clock: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        limits: EvaluationLimits | None = None,
    ) -> None:
        self.credentials = PluginCredentials.model_validate(credentials.model_dump())
        self.wall_clock, self.clock, self.sleep = wall_clock, clock, sleep
        self.limits = limits or EvaluationLimits()
        self._client = httpx.Client(
            base_url=self.credentials.origin.rstrip("/"),
            follow_redirects=False,
            trust_env=False,
            headers={"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(self.limits.total_seconds, connect=5, pool=5),
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self._client.close()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise PluginFailure("evaluation_deadline_exceeded")
        return remaining

    def _request(self, metadata: ExecutionMetadata, deadline: float) -> tuple[int, bytes]:
        issued = int(self.wall_clock())
        body = json.dumps(
            {**metadata.model_dump(mode="json"), "issued_at": issued, "expires_at": issued + 30},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        secret = self.credentials.secret.get_secret_value().encode("ascii")
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        remaining = self._remaining(deadline)
        with self._client.stream(
            "POST",
            "/enterprise/internal/v1/evaluate",
            content=body,
            headers={"x-enterprise-key-id": self.credentials.key_id, "x-enterprise-signature": signature},
            timeout=httpx.Timeout(remaining, connect=min(5, remaining), pool=min(5, remaining)),
        ) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise PluginFailure("invalid_evaluation_response")
            result = bytearray()
            for chunk in response.iter_bytes():
                self._remaining(deadline)
                if len(result) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise PluginFailure("evaluation_response_too_large")
                result.extend(chunk)
            self._remaining(deadline)
            if response.status_code in {200, 409}:
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise PluginFailure("invalid_evaluation_response")
            return response.status_code, bytes(result)

    def evaluate(self, raw_metadata: object, *, session_app_id: str | None) -> str:
        metadata = parse_metadata(raw_metadata, session_app_id, self.credentials)
        start = self.clock()
        deadline, association_deadline = start + self.limits.total_seconds, start + self.limits.association_seconds
        try:
            for attempt in range(self.limits.max_attempts):
                status, body = self._request(metadata, deadline)
                if status == 200:
                    value = _decode(body)
                    if _contains_secret(value, self.credentials.secret.get_secret_value()):
                        raise PluginFailure("invalid_evaluation_response")
                    result = BusinessEnvelope.model_validate(value)
                    output = result.model_dump_json()
                    if self.credentials.secret.get_secret_value() in output:
                        raise PluginFailure("invalid_evaluation_response")
                    if len(output.encode("utf-8")) > MAX_RESPONSE_BYTES:
                        raise PluginFailure("evaluation_response_too_large")
                    return output
                if status != 409 or _decode(body) != {"code": "execution_association_pending"}:
                    raise PluginFailure("evaluation_rejected")
                remaining = min(association_deadline, deadline) - self.clock()
                if attempt + 1 == self.limits.max_attempts or remaining <= self.limits.retry_interval:
                    raise PluginFailure("execution_association_pending")
                self.sleep(self.limits.retry_interval)
        except (ValueError, RecursionError):
            raise PluginFailure("invalid_evaluation_response") from None
        except httpx.HTTPError:
            raise PluginFailure("evaluation_transport_failed") from None
        raise PluginFailure("execution_association_pending")
