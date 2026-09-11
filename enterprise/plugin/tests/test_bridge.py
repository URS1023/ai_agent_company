import json

import httpx
import pytest
from test_client import credentials, envelope, metadata


@pytest.mark.parametrize(
    "values,params,app_id",
    [
        (credentials(), {"__enterprise_execution": metadata()}, "app-1"),
        (credentials(), {}, "app-1"),
        ({**credentials(), "__enterprise_execution": metadata()}, {"run_id": "forged"}, "app-1"),
        ({**credentials(), "__enterprise_execution": metadata()}, {}, None),
        ({**credentials(), "__enterprise_execution": {**metadata(), "dispatch_nonce": "forged"}}, {}, "app-1"),
    ],
)
def test_bridge_never_uses_tool_parameters_as_identity(
    values: dict[str, object], params: dict[str, object], app_id: str | None
) -> None:
    from managed_device_plugin.bridge import evaluate_invocation
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginCredentials, PluginFailure

    def factory(config: PluginCredentials) -> EvaluationClient:
        return EvaluationClient(
            config, transport=httpx.MockTransport(lambda request: pytest.fail("identity failure must precede I/O"))
        )

    with pytest.raises(PluginFailure):
        evaluate_invocation(values, session_app_id=app_id, tool_parameters=params, client_factory=factory)


def test_bridge_uses_only_private_metadata_and_whole_envelope() -> None:
    from managed_device_plugin.bridge import evaluate_invocation
    from managed_device_plugin.client import EvaluationClient
    from managed_device_plugin.models import PluginCredentials

    def factory(config: PluginCredentials) -> EvaluationClient:
        return EvaluationClient(
            config, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=envelope()))
        )

    result = evaluate_invocation(
        {**credentials(), "__enterprise_execution": metadata()},
        session_app_id="app-1",
        tool_parameters={},
        client_factory=factory,
    )
    assert json.loads(result) == envelope()
