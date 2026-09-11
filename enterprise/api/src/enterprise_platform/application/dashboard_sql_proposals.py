"""Untrusted model drafts, not approved queries or verified metric definitions.

Parsing grants no execution permission. Source policy, SQL validation, result
metadata and business semantics must be checked before approving a query revision.
"""

import json
from typing import Annotated

from pydantic import Field, JsonValue, StringConstraints

from enterprise_platform.domain.dashboard import DesignSnapshot

from .contracts import Contract
from .errors import InvalidInput

type Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=240, pattern=r"^\S(?:.*\S)?$")]
type Explanation = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000)]


class SqlSlotProposal(Contract):
    slot_id: Name
    sql: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=20000)]
    field_map: dict[Name, Name] = Field(min_length=1, max_length=100)
    metric_definition: Explanation
    time_definition: Explanation


class SqlProposals(Contract):
    slots: tuple[SqlSlotProposal, ...] = Field(min_length=1, max_length=100)


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate proposal key")
        result[key] = value
    return result


def parse_sql_proposals(text: str, design: DesignSnapshot) -> SqlProposals:
    """Accept data-only drafts for existing slots; never edit the design snapshot."""
    code = "dashboard_sql_proposal_invalid"
    try:
        if len(text.encode("utf-8")) > 262144:
            raise InvalidInput(code)
        json.loads(text, object_pairs_hook=_unique_keys)
        proposals = SqlProposals.model_validate_json(text)
    except (ValueError, RecursionError) as exc:
        raise InvalidInput(code) from exc

    contracts = {slot.slot_id: slot for slot in design.slots}
    seen: set[str] = set()
    for proposal in proposals.slots:
        slot = contracts.get(proposal.slot_id)
        if slot is None or proposal.slot_id in seen:
            raise InvalidInput(code)
        seen.add(proposal.slot_id)
        fields = {column.name for column in slot.columns}
        required = {column.name for column in slot.columns if column.required}
        if not required.issubset(proposal.field_map) or not set(proposal.field_map).issubset(fields):
            raise InvalidInput(code)
    return proposals
