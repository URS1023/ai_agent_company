from datetime import UTC, datetime

import pytest
from test_dashboard_sql_trial import setup

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence
from enterprise_platform.application.dashboard_sql_trial_preview import preview_sql_draft
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult
from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.domain.data_sources import SqlRead


def fixture():
    parts = setup()
    draft, design = parts[2].get.return_value, parts[1].get.return_value.design
    first = draft.proposals.slots[0]
    draft = draft.model_copy(
        update={
            "proposals": draft.proposals.model_copy(
                update={
                    "slots": (first, first.model_copy(update={"slot_id": "b"})),
                }
            )
        }
    )
    evidence = []
    for index, slot in enumerate(draft.proposals.slots):
        digest = canonical_hash(draft.model_dump(mode="json"))
        read = SqlRead(source=draft.source, read_id=draft.draft_id, revision=digest, sql=slot.sql)
        now = datetime.now(UTC)
        columns = tuple(dict.fromkeys(slot.field_map.values()))
        result = SqlTrialResult(
            workspace_id=draft.workspace_id,
            dashboard_id=draft.dashboard_id,
            draft_id=draft.draft_id,
            slot_id=slot.slot_id,
            dashboard_revision=draft.dashboard_revision,
            design_identity=design.identity,
            source=draft.source,
            draft_hash=digest,
            read_fingerprint=read_fingerprint(read, ()),
            captured_at=now,
            columns=columns,
            rows=(tuple({"kind": "integer", "value": str(index + 1)} for _ in columns),),
            row_count=1,
        )
        evidence.append(SqlTrialEvidence(trial_id=f"trial-{index}", actor_id="actor-1", recorded_at=now, result=result))
    return tuple(evidence), draft, design


def test_multiple_slots_keep_template_order_and_independent_trial_provenance():
    evidence, draft, design = fixture()
    assert len(evidence) == 2
    result = preview_sql_draft(tuple(reversed(evidence)), draft, design)
    assert [item.slot.slot_id for item in result.items] == [slot.slot_id for slot in design.slots]
    assert [item.trial_id for item in result.items] == [item.trial_id for item in evidence]
    assert result.missing_required_slots == ()
    assert result.status == "preview_only"
    assert result.snapshot_status == "independent_trials"


def test_partial_preview_names_missing_required_slots():
    evidence, draft, design = fixture()
    result = preview_sql_draft(evidence[:1], draft, design)
    assert result.missing_required_slots == ("b",)
    assert len(result.items) == 1


@pytest.mark.parametrize("case", ["empty", "same_trial", "same_slot", "too_many"])
def test_invalid_selection_never_becomes_a_combined_preview(case):
    evidence, draft, design = fixture()
    if case == "empty":
        selected = ()
    elif case == "same_trial":
        selected = (evidence[0], evidence[0])
    elif case == "same_slot":
        selected = (evidence[0], evidence[0].model_copy(update={"trial_id": "another"}))
    else:
        selected = (evidence[0],) * 101
    with pytest.raises(InvalidInput):
        preview_sql_draft(selected, draft, design)


def test_mixed_drafts_are_rejected_atomically():
    evidence, draft, design = fixture()
    foreign = evidence[1].model_copy(update={"result": evidence[1].result.model_copy(update={"draft_id": "other"})})
    with pytest.raises(Conflict):
        preview_sql_draft((evidence[0], foreign), draft, design)
