"""SDK-independent invocation boundary: private credentials are the sole identity source."""

from collections.abc import Callable, Mapping

from .client import EvaluationClient
from .models import PluginCredentials, PluginFailure, parse_credentials


def evaluate_invocation(
    credentials: Mapping[str, object],
    *,
    session_app_id: str | None,
    tool_parameters: Mapping[str, object],
    client_factory: Callable[[PluginCredentials], EvaluationClient] = EvaluationClient,
) -> str:
    if tool_parameters:
        raise PluginFailure("unexpected_tool_parameters")
    metadata = credentials.get("__enterprise_execution")
    config = parse_credentials({key: value for key, value in credentials.items() if key != "__enterprise_execution"})
    with client_factory(config) as client:
        return client.evaluate(metadata, session_app_id=session_app_id)
