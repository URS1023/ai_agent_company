"""Validate operator configuration locally; credential validation never evaluates a device."""

from dify_plugin import ToolProvider

from managed_device_plugin.models import parse_credentials


class EnterpriseDeviceProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, object]) -> None:
        parse_credentials(credentials)
