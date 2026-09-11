import hashlib
import hmac
import json
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest

SECRET = "s" * 48


def credentials() -> dict[str, object]:
    return {"origin": "https://enterprise.test", "key_id": "key-1", "secret": SECRET, "expected_app_id": "app-1"}


def metadata() -> dict[str, str]:
    return {
        "workspace_id": "workspace-1",
        "app_id": "app-1",
        "workflow_id": str(UUID(int=1)),
        "native_run_id": str(UUID(int=2)),
        "node_id": "managed-read",
        "node_execution_id": str(UUID(int=3)),
        "invoke_from": "service-api",
    }


def envelope() -> dict[str, object]:
    return {
        "run_id": "business-run",
        "device_id": "device-0001",
        "specification_revision": "spec-1",
        "input_snapshot_digest": "a" * 64,
        "result": {
            "scenario": "alert",
            "conclusion": "normal",
            "complete": True,
            "evidence": {"zero": 0, "absent": None},
        },
    }


def test_signed_exact_body_uses_only_private_native_metadata_and_returns_envelope() -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginCredentials

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert {k: v for k, v in body.items() if k not in {"issued_at", "expires_at"}} == metadata()
        assert body["issued_at"] == 1000
        assert body["expires_at"] == 1030
        assert (
            request.headers["x-enterprise-signature"]
            == hmac.new(SECRET.encode(), request.content, hashlib.sha256).hexdigest()
        )
        assert request.headers["x-enterprise-key-id"] == "key-1"
        assert request.url == "https://enterprise.test/enterprise/internal/v1/evaluate"
        assert SECRET.encode() not in request.content
        assert "authorization" not in request.headers
        return httpx.Response(200, json=envelope())

    with EvaluationClient(
        PluginCredentials.model_validate(credentials()), transport=httpx.MockTransport(handler), wall_clock=lambda: 1000
    ) as client:
        result = client.evaluate(metadata(), session_app_id="app-1")
    assert json.loads(result) == envelope()
    assert len(seen) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"invoke_from": "debugger"},
        {"native_run_id": "bad"},
        {"workflow_id": "1"},
        {"node_execution_id": True},
        {"run_id": "forged"},
        {"issued_at": "1000"},
        {"dispatch_nonce": "forged"},
        {"app_id": "other"},
        {"node_id": ""},
    ],
)
def test_invalid_metadata_never_calls_transport(change: dict[str, object]) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginCredentials, PluginFailure

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid execution identity must stop before I/O")

    with EvaluationClient(
        PluginCredentials.model_validate(credentials()), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(PluginFailure):
            client.evaluate({**metadata(), **change}, session_app_id="app-1")


@pytest.mark.parametrize("session_app_id", [None, "", "different-app"])
def test_session_app_is_required_independently_of_metadata(session_app_id: str | None) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginCredentials, PluginFailure

    with EvaluationClient(
        PluginCredentials.model_validate(credentials()),
        transport=httpx.MockTransport(lambda request: pytest.fail("session scope failure must precede I/O")),
    ) as client:
        with pytest.raises(PluginFailure, match="app_mismatch"):
            client.evaluate(metadata(), session_app_id=session_app_id)


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "http://enterprise.test"},
        {"origin": "https://user:pass@enterprise.test"},
        {"origin": "https://enterprise.test/other"},
        {"origin": "https://enterprise.test?x=1"},
        {"origin": "https://enterprise.test#x"},
        {"origin": "file:///tmp/evaluate"},
        {"origin": "https://enterprise.test:0"},
        {"secret": "short"},
        {"secret": "中" * 48},
        {"allow_insecure_http": "true"},
        {"expected_app_id": ""},
        {"key_id": "key\r\nheader"},
    ],
)
def test_invalid_operator_credentials_fail_locally(change: dict[str, object]) -> None:
    from managed_device_plugin.models import PluginFailure, parse_credentials

    with pytest.raises(PluginFailure, match="invalid_plugin_credentials") as error:
        parse_credentials({**credentials(), **change})
    assert SECRET not in str(error.value)


def test_explicit_operator_http_opt_in_supports_private_docker_origin() -> None:
    from managed_device_plugin.models import parse_credentials

    value = parse_credentials({**credentials(), "origin": "http://enterprise-api:8080", "allow_insecure_http": True})
    assert value.origin == "http://enterprise-api:8080"


def test_only_association_pending_retries_and_resigns_identical_native_metadata() -> None:
    from managed_device_plugin.client import EvaluationClient, EvaluationLimits
    from managed_device_plugin.models import parse_credentials

    now = 1000.0
    bodies: list[bytes] = []
    signatures: list[str] = []

    def sleep(duration: float) -> None:
        nonlocal now
        now += duration

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        signatures.append(request.headers["x-enterprise-signature"])
        return (
            httpx.Response(409, json={"code": "execution_association_pending"})
            if len(bodies) == 1
            else httpx.Response(200, json=envelope())
        )

    with EvaluationClient(
        parse_credentials(credentials()),
        transport=httpx.MockTransport(handler),
        clock=lambda: now,
        wall_clock=lambda: now,
        sleep=sleep,
        limits=EvaluationLimits(retry_interval=1),
    ) as client:
        assert json.loads(client.evaluate(metadata(), session_app_id="app-1")) == envelope()
    assert len(bodies) == 2
    assert signatures[0] != signatures[1]
    for body in bodies:
        value = json.loads(body)
        assert 0 < value.pop("expires_at") - value.pop("issued_at") <= 60
        assert value == metadata()


@pytest.mark.parametrize(
    "status,code",
    [
        (409, "other"),
        (409, None),
        (400, "execution_association_pending"),
        (401, "expired_execution_attestation"),
        (403, "execution_scope_mismatch"),
        (404, "not_found"),
        (429, "rate_limit"),
        (500, "error"),
        (503, "error"),
        (504, "error"),
        (302, "redirect"),
    ],
)
def test_other_http_failures_are_never_retried_or_leaked(status: int, code: str | None) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status, json={"code": code, "private": SECRET}, headers={"location": "https://other.test"}
        )

    with EvaluationClient(parse_credentials(credentials()), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PluginFailure) as error:
            client.evaluate(metadata(), session_app_id="app-1")
    assert SECRET not in str(error.value)
    assert len(requests) == 1


def test_pending_retry_count_is_bounded_even_with_a_frozen_test_clock() -> None:
    from managed_device_plugin.client import EvaluationClient, EvaluationLimits
    from managed_device_plugin.models import PluginFailure, parse_credentials

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json={"code": "execution_association_pending"})

    with EvaluationClient(
        parse_credentials(credentials()),
        transport=httpx.MockTransport(handler),
        clock=lambda: 1000,
        sleep=lambda value: None,
        limits=EvaluationLimits(max_attempts=3),
    ) as client:
        with pytest.raises(PluginFailure, match="association_pending"):
            client.evaluate(metadata(), session_app_id="app-1")
    assert len(requests) == 3


def test_handshake_does_not_continue_past_association_deadline() -> None:
    from managed_device_plugin.client import EvaluationClient, EvaluationLimits
    from managed_device_plugin.models import PluginFailure, parse_credentials

    now = 1000.0
    requests: list[httpx.Request] = []

    def sleep(duration: float) -> None:
        nonlocal now
        now += duration

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json={"code": "execution_association_pending"})

    with EvaluationClient(
        parse_credentials(credentials()),
        transport=httpx.MockTransport(handler),
        clock=lambda: now,
        sleep=sleep,
        limits=EvaluationLimits(association_seconds=2, retry_interval=1),
    ) as client:
        with pytest.raises(PluginFailure, match="association_pending"):
            client.evaluate(metadata(), session_app_id="app-1")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError, httpx.DecodingError]
)
def test_network_failures_do_not_retry(failure: type[httpx.HTTPError]) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise failure(SECRET)

    with EvaluationClient(parse_credentials(credentials()), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PluginFailure) as error:
            client.evaluate(metadata(), session_app_id="app-1")
    assert SECRET not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body",
    [b"x" * (512 * 1024 + 1), b"\xff", b"[]", b"{}", b'{"run_id":1,"run_id":2}', b"[" * 20000 + b"0" + b"]" * 20000],
    ids=["oversize", "utf8", "array", "empty", "duplicate", "depth"],
)
def test_invalid_or_oversize_response_is_not_model_text(body: bytes) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    with EvaluationClient(
        parse_credentials(credentials()),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
        ),
    ) as client:
        with pytest.raises(PluginFailure):
            client.evaluate(metadata(), session_app_id="app-1")


@pytest.mark.parametrize(
    "result",
    [
        {"scenario": "alert", "conclusion": "normal", "complete": False},
        {"scenario": "alert", "conclusion": "passed", "complete": True},
        {"scenario": "quality", "conclusion": "review", "complete": 1},
        {"scenario": "alert", "conclusion": "no_data", "complete": True},
        {"scenario": "alert", "conclusion": "normal", "complete": True, "evidence": {"value": SECRET}},
    ],
)
def test_result_schema_and_secret_leak_are_rejected(result: dict[str, object]) -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    with EvaluationClient(
        parse_credentials(credentials()),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={**envelope(), "result": result})),
    ) as client:
        with pytest.raises(PluginFailure):
            client.evaluate(metadata(), session_app_id="app-1")


def test_continuous_response_chunks_do_not_reset_total_deadline() -> None:
    from managed_device_plugin.client import EvaluationClient, EvaluationLimits
    from managed_device_plugin.models import PluginFailure, parse_credentials

    now = 1000.0

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal now
            for _ in range(20):
                now += 1
                yield b" "

    with EvaluationClient(
        parse_credentials(credentials()),
        clock=lambda: now,
        limits=EvaluationLimits(total_seconds=5),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=SlowBody(), headers={"Content-Type": "application/json"})
        ),
    ) as client:
        with pytest.raises(PluginFailure, match="deadline"):
            client.evaluate(metadata(), session_app_id="app-1")
    assert now == 1005


def test_json_escaping_never_hides_a_signing_secret_in_success_text() -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    secret = "s" * 36 + '\\"'
    payload = envelope()
    payload["result"] = {"scenario": "alert", "conclusion": "normal", "complete": True, "evidence": {"value": secret}}
    with EvaluationClient(
        parse_credentials({**credentials(), "secret": secret}),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(PluginFailure):
            client.evaluate(metadata(), session_app_id="app-1")


def test_numeric_signing_secret_is_rejected_when_echoed_as_an_integer() -> None:
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginFailure, parse_credentials

    secret = "1" * 48
    payload = envelope()
    payload["result"] = {
        "scenario": "alert",
        "conclusion": "normal",
        "complete": True,
        "evidence": {"value": int(secret)},
    }
    with EvaluationClient(
        parse_credentials({**credentials(), "secret": secret}),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(PluginFailure, match="invalid_evaluation_response"):
            client.evaluate(metadata(), session_app_id="app-1")
