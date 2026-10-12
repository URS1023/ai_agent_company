"""Boundary tests for the Python node, independent of the business database."""
import copy
import unittest
from datetime import datetime

from device_assessment import amount_check, inspection, main, maintenance, next_date, repair


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 2, 12)
        self.spec = {"deviceKey": "device-A", "deviceName": "柴油发电机", "asOf": "2026-09-02 12:00:00"}
        self.plan = {"planId": "plan-A", "planName": "柴油巡检", "cycleCode": 2, "cycleLabel": "4小时",
                     "scene": "inspection", "items": [{"itemId": "voltage", "name": "柴油发电机",
                     "indicators": "蓄电池电压", "content": "21V以上", "numericRule": {"min": 21, "unit": "V"}}]}
        self.record = {"deviceKey": "device-A", "planName": "柴油巡检", "taskStatus": 1,
                       "recordedAt": "2026-09-02 10:00:00", "lastCompleted": "2026-09-02 10:00:00",
                       "name": "柴油发电机", "indicators": "蓄电池电压", "content": "21V以上",
                       "result": 1, "actual": None, "itemRecordId": "actual-A"}

    def assess(self, **changes):
        return inspection(self.spec, self.plan, [{**self.record, **changes}], self.now)[1]

    def test_copied_threshold_is_not_a_measurement(self):
        self.assertEqual(self.assess()["status"], "pending")
        self.assertEqual(self.assess(actual="21V以上")["status"], "pending")

    def test_numeric_boundary_and_units(self):
        self.assertEqual(self.assess(actual="21V")["status"], "normal")
        self.assertEqual(self.assess(actual="20.99V")["status"], "warning")
        self.assertEqual(self.assess(actual="22A")["status"], "pending")
        self.assertEqual(self.assess(actual="22")["status"], "pending")

    def test_explicit_abnormal_not_overridden_by_number(self):
        self.assertEqual(self.assess(actual="22V", result=2)["status"], "warning")

    def test_device_plan_and_indicator_are_required_for_matching(self):
        for key, value in (("deviceKey", "device-B"), ("planName", "别的计划"), ("indicators", "电流")):
            self.assertEqual(self.assess(**{key: value})["status"], "pending")
            self.assertEqual(self.assess(**{key: value})["sourceRecordId"], "")

    def test_duplicate_items_are_not_chosen_arbitrarily(self):
        rows = inspection(self.spec, self.plan, [self.record, self.record], self.now)
        self.assertEqual(rows[1]["status"], "pending")

    def test_ambiguous_plan_name_is_not_chosen_arbitrarily(self):
        self.plan["ambiguousName"] = True
        self.assertTrue(all(r["status"] == "pending" for r in inspection(self.spec, self.plan, [self.record], self.now)))

    def test_requirement_version_mismatch(self):
        self.assertEqual(self.assess(actual="22V", content="24V以上")["status"], "pending")

    def test_missing_standard_and_qualitative_manual_confirmation(self):
        self.plan["items"][0].pop("numericRule")
        self.plan["items"][0]["content"] = "无渗漏"
        self.assertEqual(self.assess(content="无渗漏")["basis"], "原系统人工巡检结果")
        self.plan["items"][0]["content"] = ""
        self.assertEqual(self.assess(content="")["status"], "pending")

    def test_qualitative_requirement_can_be_stored_in_indicator(self):
        self.plan["items"][0].pop("numericRule")
        self.plan["items"][0].update(content="", indicators="无", requirementText="无")
        row = self.assess(content="", indicators="无")
        self.assertEqual(row["status"], "normal")
        self.assertEqual(row["requirement"], "无")

    def test_numeric_monitoring_without_limit_cannot_pass(self):
        self.plan["items"][0].pop("numericRule")
        self.plan["items"][0].update(content="数值", manualReason="缺少上下限")
        self.assertEqual(self.assess(content="数值", actual="25V")["status"], "pending")

    def test_blank_template_content_is_not_a_standard_version(self):
        self.plan["items"][0].pop("numericRule")
        self.plan["items"][0].update(content="", indicators="实时温度", manualReason="缺少温度上下限")
        row = self.assess(content="25", indicators="实时温度")
        self.assertEqual(row["recordedContent"], "25")
        self.assertEqual(row["reason"], "缺少温度上下限")
        self.assertEqual(row["actual"], "")

    def test_cycle_boundary_and_calendar_month(self):
        self.record["lastCompleted"] = "2026-09-02 08:00:00"
        self.assertEqual(inspection(self.spec, self.plan, [self.record], self.now)[0]["status"], "normal")
        self.record["lastCompleted"] = "2026-09-02 07:59:59"
        self.assertEqual(inspection(self.spec, self.plan, [self.record], self.now)[0]["status"], "warning")
        self.assertEqual(next_date(datetime(2024, 1, 31), 7), datetime(2024, 2, 29))
        self.assertEqual(next_date(datetime(2024, 2, 29), 10), datetime(2025, 2, 28))

    def test_missing_completion_does_not_invent_overdue(self):
        self.record["lastCompleted"] = None
        self.assertEqual(inspection(self.spec, self.plan, [self.record], self.now)[0]["status"], "pending")

    def test_amount_conversion_and_ambiguous_amount(self):
        self.assertTrue(amount_check("20g", "0.02-0.04Kg"))
        self.assertFalse(amount_check("41g", "0.02-0.04Kg"))
        self.assertIsNone(amount_check("20g", "以实际为准"))
        self.assertIsNone(amount_check("0.02-0.04Kg", "0.02-0.04Kg"))

    def test_missing_maintenance_is_one_pending_per_actual_point(self):
        plan = {"planId": "p", "planName": "润滑", "items": [
            {"itemId": "one", "point": "轴承", "days": 7, "lubricant": "3#锂基脂", "amount": "0.1-0.2KG"},
            {"itemId": "two", "point": "链条", "days": 30, "lubricant": "机械油", "amount": "20g"}]}
        rows = maintenance(self.spec, plan, [], self.now)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "pending" and r["alertCount"] == 0 for r in rows))
        self.assertEqual([r["cycleDays"] for r in rows], [7, 30])

    def test_completed_late_repairs_are_not_current_overdue(self):
        record = {"deviceKey": "device-A", "taskId": "r", "taskStatus": 3,
                  "deadline": "2026-08-01 12:00:00", "completedAt": "2026-08-02 12:00:00"}
        row = repair(self.spec, [record], self.now)[0]
        self.assertEqual(row["status"], "pending")
        self.assertTrue(row["historicalLate"])
        self.assertEqual(row["alertCount"], 0)
        self.assertEqual(repair(self.spec, [{**record, "acceptance": 1}], self.now)[0]["status"], "normal")
        self.assertEqual(repair(self.spec, [{**record, "taskStatus": 2}], self.now)[0]["status"], "warning")
        self.assertEqual(repair(self.spec, [{**record, "acceptance": 2}], self.now)[0]["status"], "warning")

    def test_public_device_selection_and_invalid_date(self):
        spec = {"asOf": self.spec["asOf"], "deviceKey": "device-B", "devices": [{**self.spec, "plans": [self.plan]}]}
        with self.assertRaises(ValueError):
            main(spec, inspectionRecords=[self.record])
        spec["deviceKey"] = ""
        spec["asOf"] = "not-a-date"
        with self.assertRaises(ValueError):
            main(spec)

    def test_live_standard_overrides_canvas_snapshot(self):
        spec = {"asOf": self.spec["asOf"], "devices": [{**self.spec, "plans": [self.plan]}]}
        live = {"deviceKey": "device-A", "planId": "plan-A", "planName": "柴油巡检", "planType": 1,
                "cycleCode": 2, "sameNameCount": 1, "itemId": "voltage", "name": "柴油发电机",
                "indicators": "蓄电池电压", "content": "22V以上"}
        data = main(spec, inspectionRecords=[{**self.record, "actual": "21V", "content": "22V以上"}], requirementRecords=[live])["data"]
        self.assertEqual(data[1]["status"], "warning")
        self.assertEqual(data[1]["lowerBound"], "22")

    def test_removed_plan_does_not_reuse_old_requirements(self):
        spec = {"asOf": self.spec["asOf"], "devices": [{**self.spec, "plans": [self.plan]}]}
        rows = main(spec, inspectionRecords=[self.record], requirementRecords=[])["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["itemId"], "scope")
        self.assertEqual(rows[0]["status"], "pending")

    def test_other_device_live_requirements_are_not_used(self):
        spec = {"asOf": self.spec["asOf"], "devices": [{**self.spec, "plans": [self.plan]}]}
        rows = main(spec, requirementRecords=[{"deviceKey": "device-B", "planId": "plan-A"}])["data"]
        self.assertEqual(rows[0]["itemId"], "scope")

    def test_truncated_source_results_stop_evaluation(self):
        with self.assertRaisesRegex(ValueError, "截断"):
            main({}, inspectionTruncated=True)


if __name__ == "__main__":
    unittest.main()
