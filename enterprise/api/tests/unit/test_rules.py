from decimal import Decimal, localcontext

import pytest


def test_arithmetic_and_decimal_literals_are_not_binary_floats() -> None:
    from enterprise_platform.domain.rules import evaluate_expression

    assert evaluate_expression("(a + 0.2) * 3 / 0.3", {"a": Decimal("0.1")}) == Decimal("3")
    assert evaluate_expression("85.0000000000000001 > 85", {}) is True


def test_rules_are_independent_of_callers_decimal_context() -> None:
    from enterprise_platform.domain.rules import evaluate_expression

    with localcontext() as context:
        context.prec = 2
        assert evaluate_expression("value + 0.00001", {"value": Decimal("85.00001")}) == Decimal("85.00002")


def test_boolean_range_and_identifier_comparisons() -> None:
    from enterprise_platform.domain.rules import evaluate_expression

    assert evaluate_expression("10 <= x < 20 and (enabled or x == 11)", {"x": Decimal(11), "enabled": False})
    assert evaluate_expression("sample == '0001'", {"sample": "0001"}) is True
    assert evaluate_expression("not enabled", {"enabled": False}) is True
    assert evaluate_expression("True or absent > 0", {}) is True


@pytest.mark.parametrize(
    "expression",
    [
        "abs(x)",
        "x.real",
        "x[0]",
        "2 ** 1000000",
        "[x for x in values]",
        "lambda: 1",
        "{'x': 1}",
        "1 << 20",
        "x if True else 0",
        "__import__('os')",
        "True or abs(x)",
    ],
)
def test_unsupported_ast_is_rejected_before_evaluation(expression: str) -> None:
    from enterprise_platform.domain.rules import RuleError, evaluate_expression

    with pytest.raises(RuleError):
        evaluate_expression(expression, {})


@pytest.mark.parametrize("expression", ["x / 0", "None + 1", "True + 1", "x and True", "'a' < 1", "1/3"])
def test_invalid_arithmetic_and_inexact_division_are_explicit_errors(expression: str) -> None:
    from enterprise_platform.domain.rules import RuleError, evaluate_expression

    with pytest.raises(RuleError):
        evaluate_expression(expression, {"x": Decimal(1)})


def test_missing_variable_reports_its_name() -> None:
    from enterprise_platform.domain.rules import MissingVariableError, evaluate_expression

    with pytest.raises(MissingVariableError) as error:
        evaluate_expression("temperature >= 85", {})
    assert error.value.variable == "temperature"


@pytest.mark.parametrize("expression", ["x " * 2200, "+" * 40 + "1", " + ".join(["1"] * 100)])
def test_expression_resources_are_bounded(expression: str) -> None:
    from enterprise_platform.domain.rules import RuleError, evaluate_expression

    with pytest.raises(RuleError):
        evaluate_expression(expression, {})


def test_set_operations_preserve_keys_deduplication_and_order() -> None:
    from enterprise_platform.domain.rules import set_operation

    left = ("001", "002", "001", "003")
    right = ("003", "004", "003")
    assert set_operation(left, right, "union") == ("001", "002", "003", "004")
    assert set_operation(left, right, "intersection") == ("003",)
    assert set_operation(left, right, "difference") == ("001", "002")


def test_alert_retains_known_issues_and_missing_data_together() -> None:
    from enterprise_platform.domain.rules import AlertRule, evaluate_alerts

    rules = (
        AlertRule("temperature-rule", "v1", "temperature >= 85", "high", "temperature high"),
        AlertRule("pressure-rule", "v1", "pressure > 10", "medium", "pressure high"),
    )
    result = evaluate_alerts(rules, {"temperature": Decimal("85.0000000000000001")})

    assert result.conclusion == "issues"
    assert result.complete is False
    assert result.findings[0].matched is True
    assert result.findings[1].matched is None
    assert result.findings[1].error_code == "missing_variable"
    assert result.findings[0].rule_revision == "v1"


def test_no_data_and_invalid_predicate_are_not_normal_alert_results() -> None:
    from enterprise_platform.domain.rules import AlertRule, evaluate_alerts

    rule = AlertRule("r", "v1", "value + 1", "low", "not a predicate")
    assert evaluate_alerts((rule,), {}).conclusion == "no_data"
    result = evaluate_alerts((rule,), {"value": Decimal(1)})
    assert result.conclusion == "incomplete"
    assert result.findings[0].error_code == "invalid_rule"


def test_normal_alert_is_distinct_from_a_matched_rule() -> None:
    from enterprise_platform.domain.rules import AlertRule, evaluate_alerts

    rule = AlertRule("r", "v1", "temperature >= 85", "high", "hot")
    result = evaluate_alerts((rule,), {"temperature": Decimal(84)})
    assert result.conclusion == "normal"
    assert result.complete is True
    assert result.findings[0].matched is False


def test_validation_can_restrict_names_without_evaluating_a_rule() -> None:
    from enterprise_platform.domain.rules import RuleError, validate_expression

    validate_expression("value / 0", frozenset({"value"}))
    with pytest.raises(RuleError, match="variable"):
        validate_expression("value + external", frozenset({"value"}))


def test_set_input_is_not_an_accidental_string_or_unbounded_batch() -> None:
    from enterprise_platform.domain.rules import RuleError, set_operation

    with pytest.raises(RuleError):
        set_operation("001", ("001",), "union")
    with pytest.raises(RuleError):
        set_operation(tuple(str(i) for i in range(10001)), (), "union")


def test_duplicate_rule_ids_are_not_counted_twice() -> None:
    from enterprise_platform.domain.rules import AlertRule, RuleError, evaluate_alerts

    rule = AlertRule("r", "v1", "value > 0", "high", "positive")
    with pytest.raises(RuleError, match="duplicate"):
        evaluate_alerts((rule, rule), {"value": Decimal(1)})


def test_alert_evidence_rejects_mutable_or_untyped_values() -> None:
    from enterprise_platform.domain.rules import AlertRule, RuleError, evaluate_alerts

    rule = AlertRule("r", "v1", "value > 0", "high", "positive")
    with pytest.raises(RuleError):
        evaluate_alerts((rule,), {"value": {"nested": 1}})


@pytest.mark.parametrize(
    ("expression", "temperature", "expected_conclusion", "matched"),
    [
        ("temperature > 80 or vibration > 5", "90", "issues", True),
        ("temperature < 0 and vibration > 5", "20", "incomplete", False),
    ],
)
def test_short_circuit_verdict_does_not_hide_missing_evidence(
    expression: str,
    temperature: str,
    expected_conclusion: str,
    matched: bool,
) -> None:
    from enterprise_platform.domain.rules import AlertRule, evaluate_alerts

    rule = AlertRule("r", "v1", expression, "high", "compound rule")
    result = evaluate_alerts((rule,), {"temperature": Decimal(temperature)})
    assert result.conclusion == expected_conclusion
    assert result.complete is False
    assert result.findings[0].matched is matched
    assert result.findings[0].error_code == "missing_variable"


def test_short_circuit_with_null_evidence_is_not_complete() -> None:
    from enterprise_platform.domain.rules import AlertRule, evaluate_alerts

    rule = AlertRule("r", "v1", "temperature > 80 or vibration > 5", "high", "compound rule")
    result = evaluate_alerts((rule,), {"temperature": Decimal(90), "vibration": None})
    assert result.conclusion == "issues"
    assert result.complete is False
    assert result.findings[0].matched is True
    assert result.findings[0].error_code == "missing_value"
