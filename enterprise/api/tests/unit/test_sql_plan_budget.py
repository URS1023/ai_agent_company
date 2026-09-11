import json
from decimal import Decimal

import pytest

from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget, validate_plan_budget
from enterprise_platform.domain.data_sources import DataSourceError


def postgres(rows=10, cost=2):
    return [{"Plan": {"Node Type": "Seq Scan", "Plan Rows": rows, "Total Cost": cost}}]


def mysql(rows=10, cost="2"):
    return {"query_block": {"cost_info": {"query_cost": cost}, "table": {"rows_examined_per_scan": rows}}}


@pytest.mark.parametrize(
    "dialect,plan", [("postgresql", postgres()), ("mysql", mysql()), ("mysql", json.dumps(mysql()))]
)
def test_valid_estimates_are_accepted(dialect, plan):
    validate_plan_budget(plan, dialect=dialect, budget=SqlPlanBudget(10, Decimal("2")))


@pytest.mark.parametrize("dialect,factory", [("postgresql", postgres), ("mysql", mysql)])
@pytest.mark.parametrize("rows,cost", [(11, "2"), (10, "2.01")])
def test_over_budget_plan_is_rejected(dialect, factory, rows, cost):
    with pytest.raises(DataSourceError, match="query_rejected"):
        validate_plan_budget(factory(rows, cost), dialect=dialect, budget=SqlPlanBudget(10, Decimal("2")))


def test_nested_plan_rows_are_not_hidden_by_small_aggregate_output():
    plan = postgres(1, 2)
    plan[0]["Plan"]["Plans"] = [{"Node Type": "Seq Scan", "Plan Rows": 1000, "Total Cost": 1}]
    with pytest.raises(DataSourceError, match="query_rejected"):
        validate_plan_budget(plan, dialect="postgresql", budget=SqlPlanBudget(100, Decimal("5")))


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity", {}, None])
def test_invalid_numeric_estimates_fail_closed(value):
    with pytest.raises(DataSourceError, match="response_invalid"):
        validate_plan_budget(postgres(value), dialect="postgresql", budget=SqlPlanBudget(100, Decimal("5")))


@pytest.mark.parametrize("plan", [{}, [], "not JSON", '{"query_block":{},"query_block":{}}', postgres() * 2])
def test_missing_or_ambiguous_plans_fail_closed(plan):
    with pytest.raises(DataSourceError, match="response_invalid"):
        validate_plan_budget(plan, dialect="postgresql", budget=SqlPlanBudget(100, Decimal("5")))


@pytest.mark.parametrize("rows,cost", [(0, "2"), (True, "2"), (100, "0"), (100, "NaN")])
def test_budget_requires_positive_finite_server_values(rows, cost):
    with pytest.raises(ValueError):
        SqlPlanBudget(rows, Decimal(cost))


@pytest.mark.parametrize("case", ["bytes", "depth", "nodes", "overflow", "missing_child"])
def test_oversized_or_incomplete_nested_plan_fails_closed(case):
    plan = postgres()
    if case == "bytes":
        plan[0]["Plan"]["extra"] = "x" * (256 * 1024)
    elif case == "depth":
        child = {}
        plan[0]["Plan"]["extra"] = child
        for _ in range(70):
            child["next"] = {}
            child = child["next"]
    elif case == "nodes":
        plan[0]["Plan"]["extra"] = [{}] * 4097
    elif case == "overflow":
        plan[0]["Plan"]["Plan Rows"] = "1e999999999"
    else:
        plan[0]["Plan"]["Plans"] = [{"Node Type": "Seq Scan", "Total Cost": 1}]
    with pytest.raises(DataSourceError, match="response_invalid"):
        validate_plan_budget(plan, dialect="postgresql", budget=SqlPlanBudget(100, Decimal("5")))


def test_nested_mysql_tables_count_towards_budget():
    plan = mysql(1)
    plan["query_block"]["nested_loop"] = [{"table": {"rows_examined_per_scan": 1000}}]
    with pytest.raises(DataSourceError, match="query_rejected"):
        validate_plan_budget(plan, dialect="mysql", budget=SqlPlanBudget(100, Decimal("5")))
