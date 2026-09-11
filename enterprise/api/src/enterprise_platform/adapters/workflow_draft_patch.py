"""Prepare a native draft CAS body without reconstructing its graph or variables.

The returned hash is the original native graph-only hash, not an attestation of
features, variable values, credential validity, or the patched graph. Publication
must obtain a fresh native hash after a separately confirmed draft synchronization.
"""

import re
from copy import deepcopy
from typing import TypedDict
from uuid import UUID

from pydantic import JsonValue

from enterprise_platform.application.contracts import JsonObject
from enterprise_platform.application.errors import InvalidInput


class CredentialDraftPatch(TypedDict):
    graph: JsonObject
    features: JsonObject
    hash: str
    environment_variables: list[JsonObject]
    conversation_variables: list[JsonObject]


def _canonical_uuid(value: JsonValue) -> None:
    if not isinstance(value, str):
        raise InvalidInput("native_draft_patch_invalid")
    try:
        parsed = UUID(value)
    except ValueError:
        raise InvalidInput("native_draft_patch_invalid") from None
    if not parsed.int or str(parsed) != value:
        raise InvalidInput("native_draft_patch_invalid")


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise InvalidInput("native_draft_patch_invalid")
    return value


def _objects(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise InvalidInput("native_draft_patch_invalid")
    return [_object(item) for item in value]


def _variables(value: JsonValue) -> list[JsonObject]:
    variables = _objects(value)
    for variable in variables:
        if (
            any(not isinstance(variable.get(key), str) for key in ("id", "name", "value_type", "description"))
            or not variable["id"]
            or not variable["value_type"]
            or "value" not in variable
        ):
            raise InvalidInput("native_draft_patch_invalid")
    return deepcopy(variables)


def prepare_credential_patch(draft: JsonObject, *, credential_id: str) -> CredentialDraftPatch:
    """Change only the unique managed assessment node's credential selection.

    Reject incomplete snapshots instead of accepting native POST defaults which
    would clear variables. Preserve masked values and IDs for native handling.
    This pure helper performs no writes or provider/credential readiness checks.
    """
    _canonical_uuid(credential_id)
    _canonical_uuid(draft.get("id"))
    original_hash = draft.get("hash")
    if draft.get("version") != "draft" or not isinstance(original_hash, str):
        raise InvalidInput("native_draft_patch_invalid")
    if re.fullmatch(r"[0-9a-f]{64}", original_hash) is None:
        raise InvalidInput("native_draft_patch_invalid")

    graph = deepcopy(_object(draft.get("graph")))
    features = deepcopy(_object(draft.get("features")))
    environment_variables = _variables(draft.get("environment_variables"))
    conversation_variables = _variables(draft.get("conversation_variables"))
    nodes = _objects(graph.get("nodes"))
    _objects(graph.get("edges"))
    assessment: JsonObject | None = None
    ids: set[str] = set()
    managed_tools = 0
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in ids:
            raise InvalidInput("native_draft_patch_invalid")
        ids.add(node_id)
        data = _object(node.get("data"))
        matches = (
            data.get("type") == "tool"
            and data.get("provider_id") == "enterprise/enterprise_device_assessment/enterprise_device"
            and data.get("provider_type") == "builtin"
            and data.get("tool_name") == "evaluate_device"
        )
        if matches:
            managed_tools += 1
        if node_id == "assessment":
            if not matches:
                raise InvalidInput("native_draft_patch_invalid")
            assessment = data
    if assessment is None or managed_tools != 1:
        raise InvalidInput("native_draft_patch_invalid")
    assessment["credential_id"] = credential_id
    return {
        "graph": graph,
        "features": features,
        "hash": original_hash,
        "environment_variables": environment_variables,
        "conversation_variables": conversation_variables,
    }
