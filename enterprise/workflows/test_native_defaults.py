"""Check distributed DSL nodes with real native schemas, without importing an app.

Use the native API dependency environment; these checks do not install plugins,
publish workflows, configure allowlists or contact a database/service.
"""

import json
from pathlib import Path

import pytest
import yaml
from graphon.entities.graph_config import NodeConfigDictAdapter
from graphon.nodes.end.entities import EndNodeData
from graphon.nodes.start.entities import StartNodeData
from graphon.nodes.tool.entities import ToolNodeData

ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("scenario", ["alert", "quality"])
def test_native_nodes_parse_distributed_yaml(scenario: str) -> None:
    text = (ROOT / f"default-{scenario}.yml").read_text(encoding="utf-8")
    dsl = yaml.safe_load(text)
    assert dsl == json.loads(text)
    nodes = dsl["workflow"]["graph"]["nodes"]

    for node in nodes:
        NodeConfigDictAdapter.validate_python(node)
    start = StartNodeData.model_validate(nodes[0]["data"])
    tool = ToolNodeData.model_validate(nodes[1]["data"])
    end = EndNodeData.model_validate(nodes[2]["data"])

    assert not start.variables
    assert (
        tool.provider_id == "enterprise/enterprise_device_assessment/enterprise_device"
    )
    assert tool.tool_name == "evaluate_device"
    assert tool.credential_id is None
    assert not tool.tool_parameters
    assert not tool.tool_configurations
    assert end.outputs[0].variable == "result"
    assert end.outputs[0].value_selector == [nodes[1]["id"], "text"]


def test_native_plugin_registration_matches_default_workflow() -> None:
    plugin = ROOT.parent / "plugin"
    manifest = yaml.safe_load((plugin / "manifest.yaml").read_text(encoding="utf-8"))
    provider = yaml.safe_load(
        (plugin / "provider/enterprise_device.yaml").read_text(encoding="utf-8")
    )
    tool = yaml.safe_load(
        (plugin / "tools/evaluate_device.yaml").read_text(encoding="utf-8")
    )
    dsl = yaml.safe_load((ROOT / "default-alert.yml").read_text(encoding="utf-8"))
    node = ToolNodeData.model_validate(dsl["workflow"]["graph"]["nodes"][1]["data"])

    assert (
        node.provider_id
        == f"{manifest['author']}/{manifest['name']}/{provider['identity']['name']}"
    )
    assert node.tool_name == tool["identity"]["name"]
    assert tool["parameters"] == []
