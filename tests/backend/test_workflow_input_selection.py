"""Distinguish missing input references from multi-match paths without losing arrays."""
import time

import pytest

from app.errors import ValidationError
from app.modules.workflows.engine import select_inputs
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.schemas import FlowNode


def resolve(path, output):
    node = FlowNode.model_validate({
        "id": "consumer", "title": "大模型", "kind": "llm", "config": {},
        "inputs": {"input": {"nodeId": "upstream", "path": path}},
    })
    return select_inputs(node, {"upstream": {"status": "completed", "output": output}},
                         ["upstream"], RuleBudget(time.monotonic() + 5))


def test_missing_field_is_not_reported_as_multiple_matches():
    with pytest.raises(ValidationError) as error:
        resolve('$["input_1"]', {})
    assert "未匹配到值" in error.value.message
    assert "多个值" not in error.value.message
    assert error.value.extra == {"input_name": "input", "source_node_id": "upstream", "path": '$["input_1"]'}


def test_wildcard_does_not_silently_choose_first_value():
    with pytest.raises(ValidationError, match="匹配到多个值"):
        resolve("$.data[*]", {"data": [{"value": 1}, {"value": 2}]})


@pytest.mark.parametrize("value", [[], [{"value": 1}, {"value": 2}], None, 0, False, ""])
def test_array_or_falsy_field_is_one_value(value):
    assert resolve("$.data", {"data": value}) == {"input": value}


def test_empty_wildcard_has_no_value_instead_of_a_fake_empty_array():
    with pytest.raises(ValidationError, match="未匹配到值"):
        resolve("$.data[*]", {"data": []})
