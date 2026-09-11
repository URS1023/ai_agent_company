"""A registered native workflow node, not a general model-controlled data access tool."""

from collections.abc import Generator

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from managed_device_plugin.bridge import evaluate_invocation


class EvaluateDeviceTool(Tool):
    def _invoke(self, tool_parameters: dict[str, object]) -> Generator[ToolInvokeMessage, None, None]:
        result = evaluate_invocation(
            self.runtime.credentials,
            session_app_id=self.session.app_id,
            tool_parameters=tool_parameters,
        )
        yield self.create_text_message(result)
