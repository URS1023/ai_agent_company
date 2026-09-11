"""Conservative admission checks for non-ANALYZE PostgreSQL/MySQL JSON plans.

Estimates are not measured scan counts or execution permission. The caller must
retain read-only credentials, current grants and execution row/byte/time limits.
Unsupported/missing plan shapes fail closed; raw plans never become model context.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Literal

from enterprise_platform.domain.data_sources import DataSourceError


@dataclass(frozen=True, slots=True)
class SqlPlanBudget:
    max_estimated_rows: int
    max_total_cost: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.max_estimated_rows) is not int
            or self.max_estimated_rows <= 0
            or not isinstance(self.max_total_cost, Decimal)
            or not self.max_total_cost.is_finite()
            or self.max_total_cost <= 0
        ):
            raise ValueError("Plan budget must be positive and finite")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 64:
        raise ValueError
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError
    return number


def validate_plan_budget(payload: object, *, dialect: Literal["postgresql", "mysql"], budget: SqlPlanBudget) -> None:
    """Bound plan parsing and reject excessive root cost or summed node row estimates.

    Summed node rows intentionally count intermediate work too; this is a conservative
    heuristic, not a promise about loop counts, physical scans or wall-clock duration.
    """
    try:
        serialized = payload if isinstance(payload, str) else json.dumps(payload, allow_nan=False)
        if len(serialized.encode("utf-8")) > 256 * 1024:
            raise ValueError
        plan: object = json.loads(serialized, object_pairs_hook=_unique)
        if dialect == "postgresql":
            if not isinstance(plan, list) or len(plan) != 1 or not isinstance(plan[0], dict):
                raise ValueError
            root = plan[0].get("Plan")
            if not isinstance(root, dict):
                raise ValueError
            cost = _number(root.get("Total Cost"))
            row_keys = {"Plan Rows"}
        elif dialect == "mysql":
            if not isinstance(plan, dict) or not isinstance(root := plan.get("query_block"), dict):
                raise ValueError
            info = root.get("cost_info")
            if not isinstance(info, dict):
                raise ValueError
            cost = _number(info.get("query_cost"))
            row_keys = {"rows_examined_per_scan", "rows_produced_per_join"}
        else:
            raise ValueError
        pending: list[tuple[object, int]] = [(root, 0)]
        nodes = 0
        rows = Decimal(0)
        estimates = 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if nodes > 4096 or depth > 64:
                raise ValueError
            if isinstance(value, dict):
                if dialect == "postgresql":
                    if "Node Type" in value and "Plan Rows" not in value:
                        raise ValueError
                    if "Plans" in value:
                        children = value["Plans"]
                        if not isinstance(children, list) or any(
                            not isinstance(child, dict) or "Plan Rows" not in child or "Node Type" not in child
                            for child in children
                        ):
                            raise ValueError
                for key, item in value.items():
                    if key in row_keys:
                        rows += _number(item)
                        estimates += 1
                    elif isinstance(item, (dict, list)):
                        pending.append((item, depth + 1))
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        if not estimates:
            raise ValueError
    except (ValueError, TypeError, DecimalException, RecursionError, OverflowError):
        raise DataSourceError("response_invalid") from None
    if cost > budget.max_total_cost or rows > budget.max_estimated_rows:
        raise DataSourceError("query_rejected")
