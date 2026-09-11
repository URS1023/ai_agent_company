from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_dashboard_sql_drafts import draft

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence, validate_trial_evidence
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult
from enterprise_platform.application.errors import Conflict
from enterprise_platform.domain.data_sources import SqlRead


def evidence():
    saved = draft()
    return SqlTrialEvidence(
        trial_id="trial-1",
        actor_id="runner",
        recorded_at=saved.created_at + timedelta(seconds=2),
        result=SqlTrialResult(
            workspace_id=saved.workspace_id,
            dashboard_id=saved.dashboard_id,
            draft_id=saved.draft_id,
            slot_id="count",
            dashboard_revision=saved.dashboard_revision,
            design_identity=saved.design_identity,
            source=saved.source,
            draft_hash=canonical_hash(saved.model_dump(mode="json")),
            read_fingerprint=read_fingerprint(
                SqlRead(
                    source=saved.source,
                    read_id=saved.draft_id,
                    revision=canonical_hash(saved.model_dump(mode="json")),
                    sql=saved.proposals.slots[0].sql,
                ),
                (),
            ),
            captured_at=saved.created_at + timedelta(seconds=1),
            columns=("count",),
            rows=(({"kind": "integer", "value": "18446744073709551616"},),),
            row_count=1,
        ),
    )


def test_trial_evidence_preserves_result_without_implying_approval():
    item = evidence()
    validate_trial_evidence(item, draft())
    assert SqlTrialEvidence.model_validate_json(item.model_dump_json()) == item
    assert item.result.status == "trial_only"
    assert item.result.semantic_status == "not_reviewed"
    assert item.actor_id != draft().actor_id


def test_evidence_recording_cannot_precede_capture():
    item = evidence()
    with pytest.raises(ValidationError):
        SqlTrialEvidence.model_validate(
            {**item.model_dump(), "recorded_at": item.result.captured_at - timedelta(seconds=1)}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "other"},
        {"dashboard_id": "other"},
        {"draft_id": "other"},
        {"dashboard_revision": 4},
        {"design_identity": "e" * 64},
        {"draft_hash": "e" * 64},
        {"read_fingerprint": "e" * 64},
        {"slot_id": "other"},
        {"columns": ("wrong",)},
    ],
)
def test_evidence_rejects_mismatched_draft_context(change):
    item = evidence()
    item = item.model_copy(update={"result": item.result.model_copy(update=change)})
    with pytest.raises(Conflict):
        validate_trial_evidence(item, draft())


def test_evidence_rejects_capture_older_than_draft():
    item = evidence()
    item = item.model_copy(
        update={"result": item.result.model_copy(update={"captured_at": draft().created_at - timedelta(seconds=1)})}
    )
    with pytest.raises(Conflict):
        validate_trial_evidence(item, draft())
