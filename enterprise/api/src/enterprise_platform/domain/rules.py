"""Bounded, deterministic expression and alert evaluation, without dynamic execution.

Expressions use Python-style and/or/not, decimal literals and scalar variables. Calls,
attributes, subscripts, powers and containers are excluded. Numeric work uses 128 digits
and rejects inexact results (including recurring division) instead of silently rounding.
"""

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal

from .measurements import MAX_DIGITS, MAX_EXPONENT, MeasurementError, parse_decimal, require_identifier

type RuleValue = Decimal | int | bool | str | None
type ExpressionValue = Decimal | bool | str | None
MAX_EXPRESSION_LENGTH = 4096
MAX_NODES = 256
MAX_DEPTH = 32
_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.UnaryOp,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


class RuleError(ValueError):
    """A rule is unsupported, lacks valid data or exceeds deterministic numeric limits."""


class MissingVariableError(RuleError):
    variable: str

    def __init__(self, variable: str) -> None:
        self.variable = variable
        super().__init__(f"Missing variable: {variable}")


def _parse(expression: str) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_EXPRESSION_LENGTH:
        raise RuleError("Expression is empty or exceeds its length limit")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as error:
        raise RuleError("Invalid expression syntax") from error
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise RuleError("Expression exceeds its node or depth limit")
        if not isinstance(node, _ALLOWED_NODES):
            raise RuleError(f"Unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise RuleError("Private names are excluded")
        if isinstance(node, ast.Constant):
            _constant_value(node, expression)
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def validate_expression(expression: str, allowed_variables: frozenset[str] | None = None) -> None:
    """Validate bounded syntax and optional variable scope without reading data or evaluating it."""
    tree = _parse(expression)
    if allowed_variables is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in allowed_variables:
                raise RuleError(f"Unknown variable in this scope: {node.id}")


def _scalar(value: object) -> ExpressionValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > 256:
            raise RuleError("String exceeds its length limit")
        return value
    try:
        return parse_decimal(value)
    except MeasurementError as error:
        raise RuleError(str(error)) from error


def _constant_value(node: ast.Constant, expression: str) -> ExpressionValue:
    if node.value is None or isinstance(node.value, (bool, str)):
        return _scalar(node.value)
    if isinstance(node.value, (int, float)):
        text = ast.get_source_segment(expression, node)
        try:
            return parse_decimal(text)
        except MeasurementError as error:
            raise RuleError("Invalid decimal literal") from error
    raise RuleError("Unsupported constant")


def _number(value: ExpressionValue) -> Decimal:
    if not isinstance(value, Decimal):
        raise RuleError("Arithmetic requires numeric values, not null, text or booleans")
    return value


def _boolean(value: ExpressionValue) -> bool:
    if not isinstance(value, bool):
        raise RuleError("Boolean operation requires a predicate")
    return value


def _compare(operator: ast.cmpop, left: ExpressionValue, right: ExpressionValue) -> bool:
    if type(left) is not type(right):
        raise RuleError("Comparison requires matching scalar types")
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    first, second = _number(left), _number(right)
    if isinstance(operator, ast.Lt):
        return first < second
    if isinstance(operator, ast.LtE):
        return first <= second
    if isinstance(operator, ast.Gt):
        return first > second
    if isinstance(operator, ast.GtE):
        return first >= second
    raise RuleError("Unsupported comparison")


def _visit(node: ast.AST, expression: str, variables: Mapping[str, ExpressionValue]) -> ExpressionValue:
    if isinstance(node, ast.Constant):
        return _constant_value(node, expression)
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise MissingVariableError(node.id)
        return variables[node.id]
    if isinstance(node, ast.UnaryOp):
        value = _visit(node.operand, expression, variables)
        if isinstance(node.op, ast.Not):
            return not _boolean(value)
        number = _number(value)
        return -number if isinstance(node.op, ast.USub) else +number
    if isinstance(node, ast.BinOp):
        left = _number(_visit(node.left, expression, variables))
        right = _number(_visit(node.right, expression, variables))
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
    if isinstance(node, ast.BoolOp):
        for operand in node.values:
            value = _boolean(_visit(operand, expression, variables))
            if isinstance(node.op, ast.And) and not value:
                return False
            if isinstance(node.op, ast.Or) and value:
                return True
        return isinstance(node.op, ast.And)
    if isinstance(node, ast.Compare):
        comparison_left = _visit(node.left, expression, variables)
        for operator, operand in zip(node.ops, node.comparators, strict=True):
            comparison_right = _visit(operand, expression, variables)
            if not _compare(operator, comparison_left, comparison_right):
                return False
            comparison_left = comparison_right
        return True
    raise RuleError("Unsupported expression")


def evaluate_expression(expression: str, variables: Mapping[str, RuleValue]) -> ExpressionValue:
    """Return a typed scalar or raise RuleError; this function has no external side effects."""
    tree = _parse(expression)
    values = {name: _scalar(value) for name, value in variables.items()}
    context = Context(
        prec=MAX_DIGITS,
        Emin=-MAX_EXPONENT,
        Emax=MAX_EXPONENT,
        traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
    )
    try:
        with localcontext(context):
            result = _visit(tree.body, expression, values)
            return _scalar(result)
    except (DecimalException, MeasurementError) as error:
        raise RuleError("Arithmetic is undefined, inexact or exceeds numeric limits") from error


def set_operation(
    left: Sequence[str], right: Sequence[str], operation: Literal["union", "intersection", "difference"]
) -> tuple[str, ...]:
    """Compare explicit business keys, de-duplicate, and preserve first appearance order."""
    if isinstance(left, str) or isinstance(right, str) or len(left) + len(right) > 10000:
        raise RuleError("Sets require explicit key collections, with at most 10000 input keys")
    for key in (*left, *right):
        require_identifier(key, "set key")
    first = tuple(dict.fromkeys(left))
    right_keys = set(right)
    if operation == "union":
        return tuple(dict.fromkeys((*first, *right)))
    if operation == "intersection":
        return tuple(key for key in first if key in right_keys)
    if operation == "difference":
        return tuple(key for key in first if key not in right_keys)
    raise RuleError("Unsupported set operation")


@dataclass(frozen=True, slots=True)
class AlertRule:
    rule_id: str
    revision: str
    expression: str
    severity: str
    message: str

    def __post_init__(self) -> None:
        for name in ("rule_id", "revision", "severity"):
            require_identifier(getattr(self, name), name)
        _parse(self.expression)


@dataclass(frozen=True, slots=True)
class AlertFinding:
    rule_id: str
    rule_revision: str
    expression: str
    severity: str
    message: str
    matched: bool | None
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AlertAssessment:
    conclusion: Literal["normal", "issues", "no_data", "incomplete"]
    complete: bool
    findings: tuple[AlertFinding, ...]
    inputs: tuple[tuple[str, RuleValue], ...]


def evaluate_alerts(rules: Sequence[AlertRule], variables: Mapping[str, RuleValue]) -> AlertAssessment:
    """Evaluate one record's rules; known issues survive alongside incomplete evidence."""
    if not rules:
        raise RuleError("At least one alert rule is required")
    if len({rule.rule_id for rule in rules}) != len(rules):
        raise RuleError("Alert rule set contains duplicate rule IDs")
    values = {name: _scalar(value) for name, value in variables.items()}
    if not values or all(value is None for value in values.values()):
        return AlertAssessment("no_data", False, (), tuple(sorted(values.items())))
    findings: list[AlertFinding] = []
    for rule in rules:
        referenced = {node.id for node in ast.walk(_parse(rule.expression)) if isinstance(node, ast.Name)}
        missing = sorted(referenced - values.keys())
        nulls = sorted(name for name in referenced & values.keys() if values[name] is None)
        matched: bool | None = None
        code, error = None, None
        try:
            matched = _boolean(evaluate_expression(rule.expression, values))
        except MissingVariableError as exception:
            code, error = "missing_variable", str(exception)
        except RuleError as exception:
            code, error = "invalid_rule", str(exception)
        # A short-circuit verdict does not prove that all required evidence was supplied.
        if missing or nulls:
            code = "missing_variable" if missing else "missing_value"
            evidence_error = f"Missing variables: {missing}; null values: {nulls}"
            error = f"{evidence_error}; {error}" if error else evidence_error
        findings.append(
            AlertFinding(
                rule.rule_id,
                rule.revision,
                rule.expression,
                rule.severity,
                rule.message,
                matched,
                code,
                error,
            )
        )
    complete = all(item.error_code is None and item.matched is not None for item in findings)
    has_issues = any(item.matched is True for item in findings)
    conclusion: Literal["normal", "issues", "no_data", "incomplete"] = (
        "issues" if has_issues else "normal" if complete else "incomplete"
    )
    return AlertAssessment(conclusion, complete, tuple(findings), tuple(sorted(values.items())))
