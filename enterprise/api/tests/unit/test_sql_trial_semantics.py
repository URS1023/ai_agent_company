from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_sql_trial_assessment import fixture

from enterprise_platform.application.dashboard_sql_trial_semantics import (
    NumericRule,
    SqlSlotSampleRules,
    check_sample_rules,
)
from enterprise_platform.application.errors import Conflict


def rules(design, **values):
    return SqlSlotSampleRules(design_identity=design.identity, slot_id="a", **values)


def test_metric_card_requires_one_nonnegative_value_without_changing_visuals():
    evidence, draft, design = fixture([{"kind": "integer", "value": "18446744073709551616"}])
    policy = rules(design, min_rows=1, max_rows=1, numeric=(NumericRule(field="value", minimum=Decimal(0)),))
    result = check_sample_rules(evidence, draft, design, policy)
    assert result.status == "sample_constraints_passed"
    assert result.semantic_status == "not_reviewed"
    assert result.issues == ()


@pytest.mark.parametrize(
    "value,valid",
    [
        ("0", True),
        ("1", True),
        ("0.9999999999999999999", True),
        ("1.0000000000000000001", False),
        ("-0.0000000000000000001", False),
    ],
)
def test_ratio_limits_are_exact_and_do_not_round_out_of_range_samples(value, valid):
    evidence, draft, design = fixture([{"kind": "decimal", "value": value}], kind="decimal")
    policy = rules(design, numeric=(NumericRule(field="value", minimum=Decimal(0), maximum=Decimal(1)),))
    result = check_sample_rules(evidence, draft, design, policy)
    assert (result.status == "sample_constraints_passed") is valid
    if not valid:
        assert result.issues[0].code == "numeric_out_of_range"


@pytest.mark.parametrize("size", [0, 2])
def test_card_row_cardinality_is_not_inferred_from_a_successful_query(size):
    evidence, draft, design = fixture([{"kind": "integer", "value": "1"}] * size)
    result = check_sample_rules(evidence, draft, design, rules(design, min_rows=1, max_rows=1))
    assert result.status == "sample_constraints_failed"
    assert result.issues[0].code == "row_count_mismatch"


def test_numeric_rule_rejects_text_instead_of_coercing_it():
    evidence, draft, design = fixture([{"kind": "string", "value": "0.5"}], kind="string")
    result = check_sample_rules(
        evidence, draft, design, rules(design, numeric=(NumericRule(field="value", maximum=Decimal(1)),))
    )
    assert result.issues[0].code == "numeric_value_required"


def test_policy_from_another_design_is_rejected():
    evidence, draft, design = fixture([])
    with pytest.raises(Conflict):
        check_sample_rules(evidence, draft, design, rules(design).model_copy(update={"design_identity": "f" * 64}))


@pytest.mark.parametrize(
    "values",
    [{"minimum": Decimal("NaN")}, {"maximum": Decimal("Infinity")}, {"minimum": Decimal(2), "maximum": Decimal(1)}],
)
def test_invalid_numeric_bounds_never_form_a_policy(values):
    with pytest.raises(ValidationError):
        NumericRule(field="value", **values)


def test_duplicate_field_rules_are_rejected():
    _, _, design = fixture([])
    rule = NumericRule(field="value", minimum=Decimal(0))
    with pytest.raises(ValidationError):
        rules(design, numeric=(rule, rule))


def test_business_rule_errors_are_bounded_without_echoing_values():
    evidence, draft, design = fixture([{"kind": "string", "value": "private value"}] * 120, kind="string")
    result = check_sample_rules(evidence, draft, design, rules(design, numeric=(NumericRule(field="value"),)))
    assert len(result.issues) == 100
    assert result.issues_truncated
    assert "private value" not in result.model_dump_json()


def test_rule_cannot_expand_the_original_template_capacity():
    evidence, draft, design = fixture([{"kind": "integer", "value": "1"}] * 2, row_limit=1)
    result = check_sample_rules(evidence, draft, design, rules(design, max_rows=100))
    assert result.issues[0].code == "row_count_mismatch"
