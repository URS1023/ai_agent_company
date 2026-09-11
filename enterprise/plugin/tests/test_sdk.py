"""Uses the real installed SDK when available; never substitutes fake SDK modules."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_client import credentials, envelope, metadata

ROOT = Path(__file__).resolve().parents[1]


def load_source(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_sdk_provider_validates_configuration_without_network() -> None:
    pytest.importorskip("dify_plugin")
    from dify_plugin import ToolProvider

    module = load_source("managed_provider", "provider/enterprise_device.py")
    provider = module.EnterpriseDeviceProvider()
    assert isinstance(provider, ToolProvider)
    provider.validate_credentials(credentials())
    with pytest.raises(Exception, match="invalid_plugin_credentials"):
        provider.validate_credentials({**credentials(), "secret": "bad"})


def test_actual_sdk_tool_reads_private_runtime_metadata_and_outputs_text(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("dify_plugin")
    from dify_plugin import Tool
    from dify_plugin.core.runtime import Session
    from dify_plugin.entities.tool import ToolRuntime

    module = load_source("managed_tool", "tools/evaluate_device.py")
    seen = []

    def evaluate(values, *, session_app_id, tool_parameters):
        seen.append((values, session_app_id, tool_parameters))
        return json.dumps(envelope())

    monkeypatch.setattr(module, "evaluate_invocation", evaluate)
    session = Session.empty_session()
    session.app_id = "app-1"
    values = {**credentials(), "__enterprise_execution": metadata()}
    tool = module.EvaluateDeviceTool(
        runtime=ToolRuntime(credentials=values, user_id="end-user", session_id="native-session"), session=session
    )
    assert isinstance(tool, Tool)
    messages = list(tool.invoke({}))
    assert len(messages) == 1
    assert json.loads(messages[0].message.text) == envelope()
    assert seen == [(values, "app-1", {})]


def test_actual_sdk_validates_manifest_provider_and_tool_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("dify_plugin")
    import yaml
    from dify_plugin.core.entities.plugin.setup import PluginConfiguration
    from dify_plugin.entities.tool import ToolProviderConfiguration

    monkeypatch.chdir(ROOT)
    manifest = PluginConfiguration.model_validate(yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8")))
    assert manifest.plugins.tools == ["provider/enterprise_device.yaml"]
    provider = ToolProviderConfiguration.model_validate(
        yaml.safe_load((ROOT / manifest.plugins.tools[0]).read_text(encoding="utf-8"))
    )
    assert provider.identity.name == "enterprise_device"
    assert provider.tools[0].identity.name == "evaluate_device"
    assert provider.tools[0].parameters == []
    assert (ROOT / provider.extra.python.source).is_file()
    assert (ROOT / provider.tools[0].extra.python.source).is_file()
    assert (ROOT / "_assets" / manifest.icon).is_file()
