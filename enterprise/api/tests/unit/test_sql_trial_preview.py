from dataclasses import replace

import pytest
from test_sql_trial_assessment import fixture

from enterprise_platform.application.dashboard_sql_trial_preview import preview_sql_trial
from enterprise_platform.application.errors import Conflict, InvalidInput


@pytest.mark.parametrize(
    "kind,value",
    [
        ("integer", "18446744073709551616"),
        ("decimal", "0.123456789012345678901"),
        ("string", "<img src=x>"),
        ("boolean", False),
    ],
)
def test_preview_maps_only_data_without_changing_the_frozen_design(kind, value):
    evidence, draft, design = fixture([{"kind": kind, "value": value}], kind=kind)
    before = design.identity, design.visual_json, draft.model_dump_json(), evidence.model_dump_json()
    preview = preview_sql_trial(evidence, draft, design)
    assert preview.status == "preview_only"
    assert preview.semantic_status == "not_reviewed"
    assert preview.unit_status == "not_reviewed"
    assert preview.trial_id == evidence.trial_id
    assert preview.design_identity == design.identity
    assert preview.template_id == design.template_id
    assert preview.renderer_build_id == design.renderer_build_id
    assert preview.slot.slot_id == "a"
    assert preview.slot.rows[0]["value"].model_dump(mode="json") == {"kind": kind, "value": value}
    assert set(preview.slot.rows[0]) == {"value"}
    assert before == (design.identity, design.visual_json, draft.model_dump_json(), evidence.model_dump_json())


def test_empty_sample_preview_does_not_claim_type_compatibility():
    evidence, draft, design = fixture([])
    preview = preview_sql_trial(evidence, draft, design)
    assert preview.slot.rows == ()
    assert preview.sample_status == "insufficient_samples"


def test_nullable_null_is_preserved_without_filling_a_zero():
    evidence, draft, design = fixture([{"kind": "null", "value": None}], nullable=True)
    preview = preview_sql_trial(evidence, draft, design)
    assert preview.slot.rows[0]["value"].kind == "null"
    assert preview.sample_status == "insufficient_samples"


def test_incompatible_capture_never_becomes_renderer_data():
    evidence, draft, design = fixture([{"kind": "string", "value": "123"}])
    with pytest.raises(InvalidInput, match="sql_trial_preview_incompatible"):
        preview_sql_trial(evidence, draft, design)


def test_foreign_design_never_gets_trial_values():
    evidence, draft, design = fixture([{"kind": "integer", "value": "1"}])
    with pytest.raises(Conflict):
        preview_sql_trial(evidence, draft, replace(design, design_revision=design.design_revision + 1))
