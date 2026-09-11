from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_dashboard_sql_trial import setup

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_assessment import assess_sql_trial
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult
from enterprise_platform.application.errors import Conflict
from enterprise_platform.domain.dashboard import ColumnContract, SlotContract
from enterprise_platform.domain.data_sources import SqlRead


def fixture(cells, kind="integer", nullable=False, row_limit=1000):
    parts = setup()
    design = replace(
        parts[1].get.return_value.design,
        slots=(
            SlotContract(
                slot_id="a", columns=(ColumnContract(name="value", kind=kind, nullable=nullable),), row_limit=row_limit
            ),
        ),
    )
    draft = parts[2].get.return_value.model_copy(update={"design_identity": design.identity})
    digest = canonical_hash(draft.model_dump(mode="json"))
    read = SqlRead(source=draft.source, read_id=draft.draft_id, revision=digest, sql=draft.proposals.slots[0].sql)
    now = datetime.now(UTC)
    result = SqlTrialResult(
        workspace_id=draft.workspace_id,
        dashboard_id=draft.dashboard_id,
        draft_id=draft.draft_id,
        slot_id="a",
        dashboard_revision=draft.dashboard_revision,
        design_identity=design.identity,
        source=draft.source,
        draft_hash=digest,
        read_fingerprint=read_fingerprint(read, ()),
        captured_at=now,
        columns=("count",),
        rows=tuple((cell,) for cell in cells),
        row_count=len(cells),
    )
    return SqlTrialEvidence(trial_id="trial-1", actor_id="actor-1", recorded_at=now, result=result), draft, design


def test_exact_integer_sample_is_compatible_but_not_semantically_approved():
    evidence, draft, design = fixture([{"kind": "integer", "value": "18446744073709551616"}])
    before = design.identity
    assessment = assess_sql_trial(evidence, draft, design)
    assert assessment.status == "sample_compatible"
    assert assessment.semantic_status == "not_reviewed"
    assert assessment.unit_status == "not_reviewed"
    assert assessment.issues == ()
    assert design.identity == before


@pytest.mark.parametrize(
    "cell",
    [
        {"kind": "string", "value": "12"},
        {"kind": "boolean", "value": True},
        {"kind": "decimal", "value": "12.0"},
        {"kind": "date", "value": "2026-09-11"},
    ],
)
def test_mismatched_types_are_reported_without_coercion(cell):
    evidence, draft, design = fixture([cell])
    assessment = assess_sql_trial(evidence, draft, design)
    assert assessment.status == "incompatible"
    assert assessment.issues[0].code == "column_type_mismatch"
    assert assessment.issues[0].field == "value"
    assert assessment.issues[0].row_index == 0


def test_nonnullable_null_is_reported_and_nullable_null_does_not_prove_a_type():
    assessment = assess_sql_trial(*fixture([{"kind": "null", "value": None}]))
    assert assessment.issues[0].code == "null_not_allowed"
    assessment = assess_sql_trial(*fixture([{"kind": "null", "value": None}], nullable=True))
    assert assessment.status == "insufficient_samples"


def test_empty_result_does_not_validate_expected_types():
    assessment = assess_sql_trial(*fixture([]))
    assert assessment.status == "insufficient_samples"


def test_slot_row_limit_is_checked_independently_of_query_budget():
    assessment = assess_sql_trial(*fixture([{"kind": "integer", "value": "1"}] * 2, row_limit=1))
    assert assessment.status == "incompatible"
    assert assessment.issues[0].code == "row_limit_exceeded"


def test_issues_are_bounded_and_never_contain_source_values():
    assessment = assess_sql_trial(*fixture([{"kind": "string", "value": "private source value"}] * 120))
    assert len(assessment.issues) == 100
    assert assessment.issues_truncated
    assert "private source value" not in assessment.model_dump_json()


def test_foreign_design_is_rejected_before_assessment():
    evidence, draft, design = fixture([])
    with pytest.raises(Conflict):
        assess_sql_trial(evidence, draft, replace(design, visual_json='{"style":"other"}'))
