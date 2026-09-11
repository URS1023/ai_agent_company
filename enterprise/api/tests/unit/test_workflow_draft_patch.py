import json
from copy import deepcopy
from importlib.resources import files

import pytest
from pydantic import JsonValue, TypeAdapter

from enterprise_platform.adapters.workflow_draft_patch import prepare_credential_patch
from enterprise_platform.application.contracts import JsonObject
from enterprise_platform.application.errors import InvalidInput

CREDENTIAL = "ef4606a0-f42c-482b-a60e-1352e2e7a7b0"
DRAFT_ID = "ef4606a0-f42c-482b-a60e-1352e2e7a7b1"
HASH = "a" * 64


def draft() -> JsonObject:
    return {
        "id": DRAFT_ID,
        "version": "draft",
        "hash": HASH,
        "graph": {
            "nodes": [
                {"id": "start", "type": "custom", "data": {"type": "start", "title": "保持原样"}},
                {
                    "id": "assessment",
                    "type": "custom",
                    "position": {"x": 51.25, "y": -20},
                    "style": {"width": 321},
                    "data": {
                        "type": "tool",
                        "provider_id": "enterprise/enterprise_device_assessment/enterprise_device",
                        "provider_type": "builtin",
                        "tool_name": "evaluate_device",
                        "title": "Exact assessment",
                        "credential_id": "previous-opaque-value",
                        "tool_parameters": {"untouched": {"value": "x"}},
                    },
                },
                {"id": "unrelated", "type": "custom-note", "data": {"type": "note", "credential_id": "keep-me"}},
            ],
            "edges": [{"id": "edge-1", "source": "start", "target": "assessment", "style": {"stroke": "#123456"}}],
            "viewport": {"x": 12, "y": 13, "zoom": 0.5},
        },
        "features": {"file_upload": {"enabled": True}, "unknown_feature": {"preserve": [False, None, 3]}},
        "environment_variables": [
            {
                "id": "env-original",
                "name": "secret",
                "value_type": "secret",
                "value": "[__HIDDEN__]",
                "description": "unchanged",
            }
        ],
        "conversation_variables": [
            {
                "id": "conversation-original",
                "description": "",
                "name": "state",
                "value_type": "object",
                "value": {"nested": [1, "value"]},
            }
        ],
        "marked_name": "do not send response-only metadata",
    }


def nodes(value: JsonObject) -> list[JsonValue]:
    graph = value["graph"]
    assert isinstance(graph, dict)
    result = graph["nodes"]
    assert isinstance(result, list)
    return result


def assessment(value: JsonObject) -> JsonObject:
    node = nodes(value)[1]
    assert isinstance(node, dict)
    data = node["data"]
    assert isinstance(data, dict)
    return data


def test_changes_only_assessment_credential_and_retains_native_cas_hash():
    original = draft()
    snapshot = deepcopy(original)
    result = prepare_credential_patch(original, credential_id=CREDENTIAL)
    expected = deepcopy(original)
    assessment(expected)["credential_id"] = CREDENTIAL
    assert result == {
        key: expected[key] for key in ("graph", "features", "hash", "environment_variables", "conversation_variables")
    }
    assert original == snapshot
    assert result["hash"] == HASH


def test_result_and_input_do_not_share_any_nested_mutable_snapshots():
    original = draft()
    result = prepare_credential_patch(original, credential_id=CREDENTIAL)
    graph_nodes = result["graph"]["nodes"]
    assert isinstance(graph_nodes, list)
    graph_nodes.clear()
    result["features"].clear()
    result["environment_variables"][0]["value"] = "output mutation"
    result["conversation_variables"][0].clear()
    assert len(nodes(original)) == 3
    assert original == draft()
    original["graph"] = {}
    assert result["graph"] != original["graph"]


@pytest.mark.parametrize("scenario", ["alert", "quality"])
def test_accepts_real_packaged_defaults_without_rebuilding_the_graph(scenario):
    raw = (
        files("enterprise_platform.workflow_templates").joinpath(f"default-{scenario}.yml").read_text(encoding="utf-8")
    )
    asset = TypeAdapter(JsonObject).validate_json(raw)
    workflow = asset["workflow"]
    assert isinstance(workflow, dict)
    workflow.update(id=DRAFT_ID, version="draft", hash=HASH)
    before = deepcopy(workflow)
    result = prepare_credential_patch(workflow, credential_id=CREDENTIAL)
    assessment(before)["credential_id"] = CREDENTIAL
    assert result["graph"] == before["graph"]
    assert workflow["graph"] != result["graph"]
    assert json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "credential",
    ["", "token-secret", "00000000-0000-0000-0000-000000000000", CREDENTIAL.upper(), CREDENTIAL.replace("-", "")],
)
def test_rejects_noncanonical_or_nil_credential_without_input_mutation(credential):
    value = draft()
    with pytest.raises(InvalidInput) as error:
        prepare_credential_patch(value, credential_id=credential)
    assert str(error.value) == "native_draft_patch_invalid"
    assert value == draft()


@pytest.mark.parametrize(
    "key", ["id", "version", "hash", "graph", "features", "environment_variables", "conversation_variables"]
)
def test_rejects_missing_snapshot_fields(key):
    value = draft()
    del value[key]
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("id", "not-a-uuid"),
        ("id", "00000000-0000-0000-0000-000000000000"),
        ("version", "published"),
        ("hash", "A" * 64),
        ("hash", "a" * 63),
        ("hash", None),
        ("graph", []),
        ("features", []),
        ("environment_variables", {}),
        ("conversation_variables", None),
        ("environment_variables", ["bad"]),
        ("conversation_variables", [False]),
    ],
)
def test_rejects_malformed_native_snapshot_fields(key, invalid):
    value = draft()
    value[key] = invalid
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("nodes", None),
        ("nodes", []),
        ("nodes", ["bad"]),
        ("edges", {}),
        ("edges", ["bad"]),
    ],
)
def test_rejects_malformed_graph(key, invalid):
    value = draft()
    graph = value["graph"]
    assert isinstance(graph, dict)
    graph[key] = invalid
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("type", "llm"),
        ("provider_id", "other/provider"),
        ("provider_type", "api"),
        ("tool_name", "other_tool"),
    ],
)
def test_rejects_wrong_assessment_identity(key, invalid):
    value = draft()
    assessment(value)[key] = invalid
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate_assessment", "duplicate_other", "missing_id", "missing_data", "other_assessment"]
)
def test_rejects_missing_or_ambiguous_assessment_topology(mutation):
    value = draft()
    all_nodes = nodes(value)
    target = all_nodes[1]
    assert isinstance(target, dict)
    if mutation == "missing":
        all_nodes.pop(1)
    elif mutation == "duplicate_assessment":
        all_nodes.append(deepcopy(target))
    elif mutation == "duplicate_other":
        all_nodes.append(deepcopy(all_nodes[0]))
    elif mutation == "missing_id":
        del target["id"]
    elif mutation == "missing_data":
        del target["data"]
    else:
        other = deepcopy(target)
        other["id"] = "assessment-copy"
        all_nodes.append(other)
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize("collection", ["environment_variables", "conversation_variables"])
@pytest.mark.parametrize("field", ["id", "name", "value_type", "value", "description"])
def test_rejects_incomplete_variable_snapshots_instead_of_allowing_native_defaults(collection, field):
    value = draft()
    variables = value[collection]
    assert isinstance(variables, list)
    variable = variables[0]
    assert isinstance(variable, dict)
    variable.pop(field, None)
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)


@pytest.mark.parametrize("field", ["id", "name", "value_type", "description"])
def test_rejects_wrong_variable_metadata_types(field):
    value = draft()
    variables = value["environment_variables"]
    assert isinstance(variables, list)
    variable = variables[0]
    assert isinstance(variable, dict)
    variable[field] = 42
    with pytest.raises(InvalidInput):
        prepare_credential_patch(value, credential_id=CREDENTIAL)
