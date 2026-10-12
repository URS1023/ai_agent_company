"""Offline checks for explicit quality concession matching and calculation.

中文说明：质检让步（concession）的匹配与计算：显式匹配、Decimal 精度与边界。
隔离方式：Decimal + SimpleNamespace 纯函数断言，不连库、不调用模型。
边界：金额计算必须用 Decimal，避免浮点误差。
"""
from decimal import Decimal
from types import SimpleNamespace

from app.modules.quality.concession_schemas import ConcessionRuleInput
from app.modules.quality.concessions import derive_workflow_evidence, evaluate_rule, inspection_original_weight


def rule(**overrides):
    values = {
        "code": "INCOMING-QC-LOWER-01",
        "name": "下限偏差让步规则",
        "productCodes": ["RAW-A"],
        "metricIds": ["metric-a"],
        "weightUnit": "t",
        "metricUnit": "%",
        "standardMin": "100",
        "standardMax": "120",
        "minDeviation": "1",
        "maxDeviation": "10",
        "deductionMode": "rate",
        "deductionValue": "0.125",
        "roundingMode": "exact",
        "status": "active",
        "description": "业务确认的测试规则",
    }
    values.update(overrides)
    return ConcessionRuleInput.model_validate(values).model_dump(by_alias=True, mode="json")


def evidence(**overrides):
    values = {
        "productCode": "RAW-A",
        "weightUnit": "t",
        "originalWeight": "16",
        "measurements": [{
            "metricId": "metric-a", "value": "108", "unit": "%",
            "standardMin": "100", "standardMax": "120", "sourceField": "quality.value",
        }],
    }
    values.update(overrides)
    return values


def test_exact_settlement_does_not_silently_round_after_28_digits():
    original = "123456789012345678901234567890.123456"
    result = evaluate_rule(rule(standardMin="110", deductionMode="weight", deductionValue="0.000001"),
                          evidence(originalWeight=original, measurements=[{
                              "metricId": "metric-a", "value": "108", "unit": "%",
                              "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
                          }]))
    assert result["status"] == "pending"
    assert result["proposedSettlementWeight"] == "123456789012345678901234567890.123455"
    assert result["proposedDeduction"] == "0.000001"


def test_configured_rate_uses_actual_weight_and_standard_deviation():
    result = evaluate_rule(rule(standardMin="110"), evidence(measurements=[{
        "metricId": "metric-a", "value": "108", "unit": "%",
        "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
    }]))
    assert result["status"] == "pending"
    assert result["deviation"] == "2"
    assert result["proposedDeduction"] == "2.000"
    assert result["proposedSettlementWeight"] == "14.000"


def test_failed_fixed_weight_measurement_calculates_without_hidden_percentage():
    result = evaluate_rule(rule(standardMin="110", deductionMode="weight", deductionValue="1.25"), evidence(measurements=[{
        "metricId": "metric-a", "value": "108", "unit": "%",
        "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
    }]))
    assert result["status"] == "pending"
    assert result["deviation"] == "2"
    assert result["proposedDeduction"] == "1.25"
    assert result["proposedSettlementWeight"] == "14.75"


def test_standard_matching_is_numeric_but_never_converts_units():
    result = evaluate_rule(rule(standardMin="110", standardMax="120"), evidence(measurements=[{
        "metricId": "metric-a", "value": "108", "unit": "%",
        "standardMin": "110.0", "standardMax": "120.00", "sourceField": "quality.value",
    }]))
    assert result["status"] == "pending"
    assert evaluate_rule(rule(), evidence(weightUnit="kg"))["matched"] is False


def test_missing_weight_is_confirmation_only():
    result = evaluate_rule(rule(), evidence(originalWeight=None))
    assert result["status"] == "needs_confirmation"
    assert result["matched"] is True
    assert "原始重量" in result["reason"]
    assert "proposedSettlementWeight" not in result


def test_stored_zero_weight_is_preserved_as_invalid_source_data():
    row = SimpleNamespace(snapshot={"source": {"originalWeight": 0}, "originalWeight": "16"})
    assert inspection_original_weight(row) == "0"
    result = evaluate_rule(rule(), evidence(originalWeight="0"))
    assert result["status"] == "needs_confirmation"
    assert "原始重量" in result["reason"]


def test_missing_value_and_unit_mismatch_never_match():
    missing = evaluate_rule(rule(), evidence(measurements=[{
        "metricId": "metric-a", "value": None, "unit": "%",
        "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
    }]))
    assert missing["status"] == "needs_confirmation"
    mismatch = evaluate_rule(rule(), evidence(weightUnit="kg"))
    assert mismatch["matched"] is False
    assert "单位" in mismatch["reason"]


def test_rounding_is_only_applied_when_configured():
    result = evaluate_rule(rule(deductionValue="0.1", roundingScale=2, roundingMode="half_up"), evidence(
        originalWeight="9.99", measurements=[{
            "metricId": "metric-a", "value": "121", "unit": "%",
            "standardMin": "100", "standardMax": "120", "sourceField": "quality.value",
        }]))
    assert result["status"] == "pending"
    assert Decimal(result["proposedSettlementWeight"]) == Decimal("8.99")


def test_rounding_cannot_eliminate_the_configured_deduction():
    result = evaluate_rule(rule(standardMin="110", deductionValue="0.1", roundingScale=1, roundingMode="half_up"), evidence(
        originalWeight="0.04", measurements=[{
            "metricId": "metric-a", "value": "108", "unit": "%",
            "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
        }]))
    assert result["status"] == "needs_confirmation"
    assert "舍入" in result["reason"]


def test_separate_deviation_tiers_match_only_the_actual_interval():
    low = rule(code="LOW", name="偏差 1 至 2", standardMin="110", minDeviation="1", maxDeviation="2", deductionValue="0.1")
    high = rule(code="HIGH", name="偏差 3 至 5", standardMin="110", minDeviation="3", maxDeviation="5", deductionValue="0.2")
    actual = evidence(measurements=[{
        "metricId": "metric-a", "value": "106", "unit": "%",
        "standardMin": "110", "standardMax": "120", "sourceField": "quality.value",
    }])
    low_result = evaluate_rule(low, actual)
    high_result = evaluate_rule(high, actual)
    assert low_result["status"] == "needs_confirmation" and low_result["matched"] is False
    assert high_result["status"] == "pending" and high_result["proposedDeduction"] == "3.2"


def test_rule_contract_rejects_implicit_or_invalid_rules():
    try:
        ConcessionRuleInput.model_validate({
            "code": "bad", "name": "bad", "metricIds": ["m"], "weightUnit": "t", "metricUnit": "%",
            "deductionMode": "rate", "deductionValue": "0.05", "roundingMode": "exact",
        })
    except ValueError as exc:
        assert "标准" in str(exc)
    else:
        raise AssertionError("rule without an explicit standard must be rejected")


def test_workflow_source_derives_values_from_selected_paths():
    source = {"fields": {
        "originalWeight": "$.outputs['incoming']['weight']",
        "productCode": "$.outputs['incoming']['product']",
        "metric-a.value": "$.outputs['incoming']['quality']['value']",
        "metric-a.unit": "$.outputs['incoming']['quality']['unit']",
        "metric-a.standardMin": "$.outputs['incoming']['quality']['min']",
        "metric-a.standardMax": "$.outputs['incoming']['quality']['max']",
    }}
    result = derive_workflow_evidence({"outputs": {"incoming": {"weight": 16, "product": "RAW-A", "quality": {
        "value": 108, "unit": "%", "min": 110, "max": 120,
    }}}}, source, {"weightUnit": "t", "measurements": []})
    assert result["originalWeight"] == "16"
    assert result["productCode"] == "RAW-A"
    assert result["measurements"] == [{
        "metricId": "metric-a", "value": "108", "unit": "%", "sourceField": "$.outputs['incoming']['quality']['value']",
        "standardMin": "110", "standardMax": "120",
    }]


def test_workflow_null_weight_cannot_fall_back_to_client_value():
    source = {"fields": {"originalWeight": "$.outputs.weight", "metric-a.value": "$.outputs.value",
                          "metric-a.unit": "$.outputs.unit", "metric-a.standardMin": "$.outputs.min",
                          "metric-a.standardMax": "$.outputs.max"}}
    try:
        derive_workflow_evidence({"outputs": {"weight": None, "value": 108, "unit": "%", "min": 110, "max": 120}},
                                 source, {"originalWeight": "16", "measurements": []})
    except ValueError as exc:
        assert "原始重量" in str(exc)
    except Exception as exc:
        # AppError is intentionally used by the production contract.
        assert "原始重量" in str(exc)
    else:
        raise AssertionError("a null persisted weight must not accept client fallback")
