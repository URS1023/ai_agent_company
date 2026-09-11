"""Installed chat documentation must retain its runtime shape after debugger registration."""

import pytest
from pydantic import ValidationError

from controllers.console.app import completion as debug_completion
from controllers.console.explore import completion


def test_installed_chat_has_a_distinct_registered_schema() -> None:
    model = completion.ChatMessageExplorePayload

    assert model.__name__ != debug_completion.ChatMessagePayload.__name__
    schema = completion.console_ns.models[model.__name__].__schema__
    assert schema["properties"] == model.model_json_schema()["properties"]
    assert set(schema["properties"]) == {
        "inputs",
        "query",
        "files",
        "conversation_id",
        "parent_message_id",
        "retriever_from",
    }
    assert schema["properties"]["retriever_from"]["default"] == "explore_app"
    assert completion.ChatApi.post.__apidoc__["expect"][0].name == model.__name__


def test_installed_chat_preserves_runtime_defaults() -> None:
    payload = completion.ChatMessageExplorePayload(inputs={}, query="hello")

    assert payload.model_dump(exclude_none=True) == {"inputs": {}, "query": "hello", "retriever_from": "explore_app"}
    assert debug_completion.ChatMessagePayload.model_fields["retriever_from"].default == "dev"


@pytest.mark.parametrize("field", ["conversation_id", "parent_message_id"])
def test_installed_chat_still_rejects_invalid_context_ids(field: str) -> None:
    with pytest.raises(ValidationError):
        completion.ChatMessageExplorePayload.model_validate({"inputs": {}, "query": "hello", field: "invalid"})


def test_installed_chat_still_normalizes_blank_context_ids() -> None:
    payload = completion.ChatMessageExplorePayload(inputs={}, query="hello", conversation_id="", parent_message_id="")

    assert payload.conversation_id is None
    assert payload.parent_message_id is None
