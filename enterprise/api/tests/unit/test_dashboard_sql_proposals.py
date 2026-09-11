import json

import pytest
from test_dashboard_storage import stored_record

from enterprise_platform.application.dashboard_sql_proposals import parse_sql_proposals
from enterprise_platform.application.errors import InvalidInput


def payload():
    record = stored_record()
    slot = record.design.slots[0]
    return record.design, {
        "slots": [
            {
                "slot_id": slot.slot_id,
                "sql": "SELECT device AS name, rate AS value FROM measurements",
                "field_map": {column.name: column.name for column in slot.columns},
                "metric_definition": "Qualified count divided by inspected count",
                "time_definition": "Current shift",
            }
        ]
    }


def test_parses_sql_draft_without_changing_template():
    design, value = payload()
    identity = design.identity
    parsed = parse_sql_proposals(json.dumps(value), design)
    assert parsed.slots[0].sql == value["slots"][0]["sql"]
    assert design.identity == identity


@pytest.mark.parametrize(
    "case",
    ["style", "connection", "unknown_slot", "duplicate", "field", "missing_map", "empty", "markdown", "oversized"],
)
def test_rejects_model_output_outside_requested_data_slots(case):
    design, value = payload()
    slot = value["slots"][0]
    if case == "style":
        slot["style"] = {"color": "red"}
    elif case == "connection":
        value["connection"] = {"host": "untrusted"}
    elif case == "unknown_slot":
        slot["slot_id"] = "not-in-template"
    elif case == "duplicate":
        value["slots"].append(slot.copy())
    elif case == "field":
        slot["field_map"]["unknown"] = "value"
    elif case == "missing_map":
        slot["field_map"] = {}
    elif case == "empty":
        value["slots"] = []
    text = json.dumps(value)
    if case == "markdown":
        text = f"```json\n{text}\n```"
    elif case == "oversized":
        text = " " * 262145
    with pytest.raises(InvalidInput, match="dashboard_sql_proposal_invalid"):
        parse_sql_proposals(text, design)


def test_rejects_ambiguous_duplicate_json_keys():
    design, value = payload()
    text = json.dumps(value).replace('"sql":', '"sql": "SELECT hidden FROM other_table", "sql":')
    with pytest.raises(InvalidInput, match="dashboard_sql_proposal_invalid"):
        parse_sql_proposals(text, design)


@pytest.mark.parametrize("field", ["sql", "metric_definition", "time_definition"])
@pytest.mark.parametrize("value", ["   ", 123, None])
def test_rejects_blank_or_non_text_proposal_fields(field, value):
    design, document = payload()
    document["slots"][0][field] = value
    with pytest.raises(InvalidInput, match="dashboard_sql_proposal_invalid"):
        parse_sql_proposals(json.dumps(document), design)
