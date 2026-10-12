"""库存图表不允许跨单位合计或把缺失单位当成相同单位。"""
import pytest

from app.errors import ValidationError
from app.modules.chat_artifacts.chart_renderer import chart_data


def _result(rows, **kwargs):
    """构造单位来自原始查询字段的完整快照。"""
    return {"data": rows, "totalCount": len(rows), "truncated": False, **kwargs}


@pytest.mark.parametrize("kind", ["pie", "bar", "line"])
@pytest.mark.parametrize("units", [("桶", "米"), ("桶", None), (None, None), ("", "桶")])
def test_mixed_or_missing_row_units_cannot_share_one_numeric_axis(kind, units):
    rows = [{"物料": "机械油", "数量": 2, "单位": units[0]}, {"物料": "圆钢", "数量": 120, "单位": units[1]}]
    with pytest.raises(ValidationError, match="单位"):
        chart_data(_result(rows), {"chart_type": kind, "label_column": "物料", "value_column": "数量"})


def test_identical_units_are_preserved_without_aggregating_rows():
    rows = [{"物料": "圆钢甲", "数量": "120.0", "unit_name": "米"}, {"物料": "圆钢乙", "数量": "90.0", "unit_name": "米"}]
    spec = chart_data(_result(rows), {"chart_type": "bar", "label_column": "物料", "value_column": "数量"})
    assert spec["values"] == [120, 90] and spec["unit"] == "米"


def test_column_declared_units_prevent_mixing_metrics():
    result = _result([{"温度": 30, "压力": 29.4}], columns=[
        {"columnName": "温度", "comment": "温度（℃）"}, {"columnName": "压力", "comment": "压力（MPa）"}])
    with pytest.raises(ValidationError, match="单位"):
        chart_data(result, {"chart_type": "bar", "value_columns": ["温度", "压力"]})


def test_column_and_row_units_must_agree():
    result = _result([{"物料": "机械油", "数量": 2, "单位": "桶"}],
                     columns=[{"columnName": "数量", "comment": "库存数量（米）"}])
    with pytest.raises(ValidationError, match="不一致"):
        chart_data(result, {"chart_type": "bar", "label_column": "物料", "value_column": "数量"})


def test_no_unit_metadata_is_reported_as_unknown_and_never_invented():
    result = _result([{"状态": "正常", "数量": 2}, {"状态": "异常", "数量": 3}])
    assert chart_data(result, {"chart_type": "pie", "label_column": "状态", "value_column": "数量"})["unit"] is None


@pytest.mark.parametrize("stock_unit,material_unit", [(None, None), ("桶", None), ("桶", "米")])
def test_inventory_interface_unit_fields_enforce_the_same_contract(stock_unit, material_unit):
    result = _result([{"物料": "机械油", "数量": 2,
                       "库存计量单位": stock_unit, "物料计量单位": material_unit}])
    with pytest.raises(ValidationError, match="单位"):
        chart_data(result, {"chart_type": "bar", "label_column": "物料", "value_column": "数量"})


def test_inventory_interface_known_consistent_unit_is_preserved():
    result = _result([{"物料": "机械油", "数量": 2, "库存计量单位": "桶", "物料计量单位": "桶"}])
    assert chart_data(result, {"chart_type": "bar", "label_column": "物料", "value_column": "数量"})["unit"] == "桶"
