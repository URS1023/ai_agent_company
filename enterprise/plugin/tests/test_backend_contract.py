"""Optional source-tree contract test; production plugin has no enterprise-package dependency."""

import json

import httpx
import pytest
from pydantic import SecretStr
from test_client import SECRET, credentials, envelope, metadata


def test_actual_backend_authenticator_accepts_exact_client_request_and_response_contract() -> None:
    backend = pytest.importorskip("enterprise_platform.application.managed_execution")
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import parse_credentials

    authenticator = backend.ExecutionAuthenticator(
        (
            backend.ExecutionKey(
                key_id="key-1",
                workspace_id="workspace-1",
                app_id="app-1",
                secret=SecretStr(SECRET),
                node_ids=frozenset({"managed-read"}),
            ),
        ),
        clock=lambda: 1001,
    )
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        execution = authenticator.verify(
            request.content, request.headers["x-enterprise-key-id"], request.headers["x-enterprise-signature"]
        )
        seen.append(execution.model_dump(mode="json", exclude={"issued_at", "expires_at"}))
        response = backend.BusinessEnvelope.model_validate(envelope())
        return httpx.Response(200, json=response.model_dump(mode="json"))

    with EvaluationClient(
        parse_credentials(credentials()), wall_clock=lambda: 1000, transport=httpx.MockTransport(handler)
    ) as client:
        result = client.evaluate(metadata(), session_app_id="app-1")
    assert seen == [metadata()]
    assert json.loads(result) == envelope()
