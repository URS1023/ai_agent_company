"""Business arithmetic and set semantics, including precision and hostile formulas."""
import time
from decimal import Decimal

import pytest

from app.errors import ValidationError
from app.modules.workflows.expressions import (
    RuleBudget, canonical, evaluate, evaluate_result, formula_expression, set_operation, validate_expression,
)


def value(item):
    return {"value": item}


def operation(name, *args):
    return {"op": name, "args": args}


def run(name, *args):
    expression = {"op": name, "args": [value(item) for item in args]}
    validate_expression(expression)
    return evaluate_result(expression, {}, RuleBudget(time.monotonic() + 5))


@pytest.mark.parametrize("name,args,expected", [
    ("add", ["0.1", "0.2"], "0.3"), ("sub", ["5", "8"], "-3"),
    ("mul", ["123.450", "2"], "246.900"), ("div", ["1", "8"], "0.125"),
    ("gte", [10, 10], True), ("lte", [10, 10], True), ("lt", [9, 10], True), ("gt", [10, 10], False),
    ("eq", [1, 1.0], True), ("ne", ["1", 1], True), ("ne", [False, 0], True),
    ("between", [10, 10, 20], True), ("between", [20, 10, 20], True), ("outside", [9, 10, 20], True),
    ("and", [True, False], False), ("or", [False, True], True), ("not", [False], True),
    ("union", [[1, 2, 1], [2, 3]], [1, 2, 3]), ("intersection", [[1, 2], [2, 3]], [2]),
    ("difference", [[1, 2], [2, 3]], [1]), ("in", ["01", ["01", 1]], True),
    ("not_in", ["1", [1]], True), ("length", [[1, 2]], 2), ("coalesce", [None, 0, 2], 0),
])
def test_operators(name, args, expected):
    assert run(name, *args) == expected


@pytest.mark.parametrize("name,args", [
    ("div", [1, 0]), ("add", [None, 1]), ("add", [False, 1]), ("lt", ["device", 1]),
    ("and", ["false", True]), ("between", [10, 20, 0]), ("union", [{}, []]), ("add", ["1e1001", 0]),
])
def test_invalid_values_do_not_turn_into_zero_or_success(name, args):
    with pytest.raises(ValidationError):
        run(name, *args)


@pytest.mark.parametrize("formula", ["__import__('os').system('whoami')", "a.b", "a[0]", "a**2", "sum(a)", "[x for x in a]", "True", "a // 2"])
def test_python_execution_and_undeclared_operators_rejected(formula):
    with pytest.raises(ValidationError):
        formula_expression(formula)


def test_decimal_set_identity_does_not_round_or_distinguish_signed_zero():
    a, b = Decimal("123456789012345678901234567890.01"), Decimal("123456789012345678901234567890.02")
    assert canonical(a) != canonical(b)
    assert canonical(Decimal("-0.00")) == canonical(0)
    assert canonical(Decimal("1.000")) == canonical(1)


def test_chinese_formula_and_stable_key_sets():
    expression = formula_expression("(温度 - 偏差) * 1.8 + 32")
    assert evaluate_result(expression, {"温度": "30.5", "偏差": "0.5"}, RuleBudget(time.monotonic() + 5)) == "86.00"
    assert set_operation([{"id": "01", "v": 1}, {"id": "02", "v": 2}], [{"id": "02", "v": 9}],
                         "intersection", RuleBudget(time.monotonic() + 5), keys=["id"]) == [{"id": "02", "v": 2}]


def test_missing_and_short_circuit_have_explicit_semantics():
    missing = {"field": "missing"}
    expression = {"op": "or", "args": [{"op": "missing", "args": [missing]}, {"op": "gt", "args": [missing, value(0)]}]}
    validate_expression(expression)
    assert evaluate(expression, {}, RuleBudget(time.monotonic() + 5)) is True
    expression["args"][1] = {"op": "exec", "args": []}
    with pytest.raises(ValidationError):
        validate_expression(expression)
    with pytest.raises(ValidationError):
        evaluate_result(missing, {}, RuleBudget(time.monotonic() + 5))


def test_operation_budget_and_time_are_enforced():
    with pytest.raises(ValidationError):
        evaluate(value(1), {}, RuleBudget(time.monotonic() - 1))
    with pytest.raises(ValidationError):
        set_operation([1, 2], [3], "union", RuleBudget(time.monotonic() + 5, remaining=2))
