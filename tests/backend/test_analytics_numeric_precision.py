"""验证返回样本的预测、合计与格式化不会受默认十进制精度影响。"""
from decimal import Decimal, Inexact, ROUND_DOWN, getcontext, localcontext
from fractions import Fraction
from itertools import permutations

import pytest

from app.modules.analytics.statistics import _decimal_text, describe, enhanced_analysis


COLUMNS = [
    {"columnName": "day", "dataType": "date", "comment": "采样日"},
    {"columnName": "amount", "dataType": "decimal", "comment": "金额"},
]


def observations(values):
    """纯内存时间序列，不连接数据库或模型。"""
    return [{"day": f"2026-01-{index + 1:02d}", "amount": str(value)}
            for index, value in enumerate(values)]


@pytest.mark.parametrize("values, expected", [
    ([1, 2, 3, 4], "5"),
    ([4, 3, 2, 1], "0"),
    ([7, 7, 7, 7], "7"),
    (["0.1", "0.2", "0.3", "0.4"], "0.5"),
])
def test_projection_uses_next_observation_from_same_fitted_line(values, expected):
    data = observations(values)
    statistics, *_ = describe(data, COLUMNS, truncated=False)
    result = enhanced_analysis(data, COLUMNS, statistics, truncated=False)
    projection = result["metrics"][0]["projection"]
    assert projection["value"] == expected
    assert projection["nextTime"] == "2026-01-05T00:00:00"
    assert projection["fitR2"] == "1"


@pytest.mark.parametrize("values, expected", [
    (["123456789012345678901234567890"], "123456789012345678901234567890"),
    (["123456789012345678901234567890", "-123456789012345678901234567890", "0.1"], "0.1"),
    (["12345678901234567890.12345678901234567890", "0.00000000000000000001"],
     "12345678901234567890.12345678901234567891"),
    (["9999999999999999999999999999999999999999999999999999999999999999999", "1"],
     "10000000000000000000000000000000000000000000000000000000000000000000"),
])
def test_sum_retains_full_source_precision_including_cancellation(values, expected):
    data = observations(values)
    statistics, *_ = describe(data, COLUMNS, truncated=False)
    assert Decimal(statistics["columns"]["amount"]["sum"]) == Decimal(expected)
    assert [row["amount"] for row in data] == values


@pytest.mark.parametrize("value, expected", [
    ("123456789012345678901234567890.123400", "123456789012345678901234567890.1234"),
    ("1000.0000", "1000"),
    ("0.00000000000000000000000000000000000000012300", "0.000000000000000000000000000000000000000123"),
    ("-0.000", "0"),
])
def test_decimal_text_only_formats_without_rounding(value, expected):
    with localcontext() as ambient:
        ambient.prec = 6
        assert _decimal_text(Decimal(value)) == expected
        assert getcontext().prec == 6


def test_statistics_ignore_ambient_precision_and_preserve_exact_median():
    values = ["123456789012345678901234567890.1", "123456789012345678901234567890.2"]
    with localcontext() as ambient:
        ambient.prec = 6
        statistics, *_ = describe(observations(values), COLUMNS, truncated=False)
        item = statistics["columns"]["amount"]
        assert Decimal(item["sum"]) == Decimal("246913578024691357802469135780.3")
        assert Decimal(item["mean"]) == Decimal("123456789012345678901234567890.15")
        assert Decimal(item["median"]) == Decimal("123456789012345678901234567890.15")
        assert getcontext().prec == 6


def test_large_values_keep_small_time_series_changes():
    values = ["123456789012345678901234567890." + str(index) for index in range(1, 5)]
    data = observations(values)
    statistics, *_ = describe(data, COLUMNS, truncated=False)
    result = enhanced_analysis(data, COLUMNS, statistics, truncated=False)
    metric = result["metrics"][0]
    assert metric["trend"]["absoluteChange"] == "0.3"
    assert metric["projection"]["value"] == "123456789012345678901234567890.5"
    assert metric["projection"]["fitR2"] == "1"


def test_recurring_average_states_decimal_rounding_policy():
    statistics, *_ = describe(observations([1, 0, 0]), COLUMNS, truncated=False)
    item = statistics["columns"]["amount"]
    assert Decimal(item["sum"]) == Decimal(1)
    assert item["arithmetic"]["sumExact"] is True
    assert item["arithmetic"]["divisionRounding"] == "ROUND_HALF_EVEN"
    assert item["arithmetic"]["precision"] >= 34


def test_cancellation_sum_is_order_independent_and_matches_exact_rationals():
    values = ["123456789012345678901234567890.123456789", "0.000000001",
              "-123456789012345678901234567890.123456789"]
    expected = sum(map(Fraction, values))
    for ordering in permutations(values):
        statistics, *_ = describe(observations(ordering), COLUMNS, truncated=False)
        assert Fraction(statistics["columns"]["amount"]["sum"]) == expected


def test_iqr_preserves_small_outliers_on_large_common_baseline():
    values = ["123456789012345678901234567890." + str(index) for index in range(1, 8)]
    values += ["123456789012345678901234567892.0"]
    with localcontext() as ambient:
        ambient.prec = 6
        ambient.rounding = ROUND_DOWN
        ambient.traps[Inexact] = True
        statistics, *_ = describe(observations(values), COLUMNS, truncated=False)
        item = statistics["columns"]["amount"]
        assert item["outlierCount"] == 1
        assert Decimal(item["median"]) == Decimal("123456789012345678901234567890.45")
        assert Decimal(item["outlierUpperBound"]) == Decimal("123456789012345678901234567891.25")
        assert getcontext().prec == 6
        assert getcontext().rounding == ROUND_DOWN
        assert getcontext().traps[Inexact] is True


@pytest.mark.parametrize("value", ["1e999999999", "1e-999999999", "9" * 5000])
def test_extreme_precision_retains_rows_and_reports_statistics_unavailable(value):
    data = observations([value, "1", "2", "3"])
    statistics, insights, _, hints = describe(data, COLUMNS, truncated=False)
    item = statistics["columns"]["amount"]
    assert item["aggregationBlocked"] == "decimal_precision_budget"
    assert "sum" not in item and "mean" not in item
    assert any(insight["type"] == "precision" for insight in insights)
    assert all(hint["chartType"] == "table" for hint in hints)
    assert enhanced_analysis(data, COLUMNS, statistics, truncated=False)["metrics"] == []
    assert data[0]["amount"] == value


def test_extreme_decimal_text_does_not_expand_exponent():
    assert _decimal_text(Decimal("1e999999999")) == "1E+999999999"
    assert _decimal_text(Decimal("1e-999999999")) == "1E-999999999"
