"""验收比较器本身的离线检查，不允许通过显示格式或重复行掩盖错误。"""
import unittest

from live_database_tasks import contains_financial_values, rows_equal


class ComparisonTests(unittest.TestCase):
    def test_money_display_zeroes_are_equivalent(self):
        self.assertTrue(contains_financial_values("3685447.80\n1356992.44", {"contract_amount": "3685447.800000", "paid_amount": "1356992.440000"}))

    def test_wrong_or_missing_values_are_not_accepted(self):
        self.assertFalse(contains_financial_values("3685447.81", {"contract_amount": "3685447.80"}))
        self.assertFalse(contains_financial_values("暂无数据", {"contract_amount": "0"}))

    def test_duplicate_rows_and_nulls_remain_distinct(self):
        self.assertFalse(rows_equal([{"amount": 1}], [{"amount": 1}, {"amount": 1}]))
        self.assertFalse(rows_equal([{"amount": ""}], [{"amount": None}]))
        self.assertTrue(rows_equal([{"amount": "1.00"}, {"amount": None}], [{"amount": None}, {"amount": 1}]))


if __name__ == "__main__":
    unittest.main()
