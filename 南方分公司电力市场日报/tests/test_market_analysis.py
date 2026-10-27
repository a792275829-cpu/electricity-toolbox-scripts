from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOL_DIR.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from gd_power_report.market_analysis import (  # noqa: E402
    classify_unit_status, summarize_realtime_units,
)


def hours(day_ahead=10.0, realtime=10.0, actual=10.0) -> list[dict]:
    return [
        {"day_ahead_mwh": day_ahead, "realtime_mwh": realtime, "actual_mwh": actual}
        for _ in range(24)
    ]


class ClassifyTests(unittest.TestCase):
    def test_missing_hourly_data(self):
        status, _ = classify_unit_status(hours()[:23], 10.0, 10.0)
        self.assertEqual(status, "数据不足")
        broken = hours()
        broken[3] = {"day_ahead_mwh": None, "realtime_mwh": 10.0, "actual_mwh": 10.0}
        self.assertEqual(classify_unit_status(broken, 10.0, 10.0)[0], "数据不足")
        self.assertEqual(classify_unit_status(hours(), None, 10.0)[0], "数据不足")
        self.assertEqual(classify_unit_status(hours(), 10.0, None)[0], "数据不足")

    def test_bid_without_generation(self):
        status, _ = classify_unit_status(hours(), 0.05, 0.01)
        self.assertEqual(status, "中标未启机")
        # 实时低于 0.05 就不再判「中标未启机」，落到容差比较。
        self.assertEqual(classify_unit_status(hours(), 0.04, 0.01)[0], "欠发")

    def test_idle_unit(self):
        self.assertEqual(classify_unit_status(hours(), 0.01, 0.01)[0], "未中标/停备")
        self.assertEqual(classify_unit_status(hours(), 0.0, 0.0)[0], "未中标/停备")

    def test_over_and_under_generation_beyond_tolerance(self):
        self.assertEqual(classify_unit_status(hours(), 10.0, 10.5)[0], "超发")
        self.assertEqual(classify_unit_status(hours(), 10.0, 9.5)[0], "欠发")

    def test_within_tolerance_is_tracking(self):
        status, explanation = classify_unit_status(hours(), 10.0, 10.25)
        self.assertEqual(status, "跟踪出力")
        self.assertIn("容差取0.02万千瓦时与实时出清电量3%的较大值", explanation)

    def test_tolerance_boundary_counts_as_tracking(self):
        # 容差 = max(0.02, 10 * 0.03) = 0.3
        self.assertEqual(classify_unit_status(hours(), 10.0, 10.3)[0], "跟踪出力")
        self.assertEqual(classify_unit_status(hours(), 10.0, 10.3001)[0], "超发")

    def test_suspected_fault_needs_two_consecutive_hours(self):
        scenario = []
        for index in range(24):
            if index == 10:
                scenario.append({"day_ahead_mwh": 5.0, "realtime_mwh": 5.0, "actual_mwh": 5.0})
            elif index in (11, 12):
                scenario.append({"day_ahead_mwh": 5.0, "realtime_mwh": 5.0, "actual_mwh": 0.0})
            else:
                scenario.append({"day_ahead_mwh": 5.0, "realtime_mwh": 5.0, "actual_mwh": 5.0})
        status, explanation = classify_unit_status(scenario, 5.0, 5.0)
        self.assertEqual(status, "疑似故障调停")
        self.assertIn("需结合运行记录确认", explanation)

    def test_single_low_hour_does_not_trigger_fault(self):
        scenario = []
        for index in range(24):
            actual = 0.0 if index == 11 else 5.0
            scenario.append({"day_ahead_mwh": 5.0, "realtime_mwh": 5.0, "actual_mwh": actual})
        self.assertNotEqual(classify_unit_status(scenario, 5.0, 5.0)[0], "疑似故障调停")


class SummaryTests(unittest.TestCase):
    def parsed(self, *, actual=10.0, realtime=10.0) -> dict:
        return {"plants": [{"plant": "汕头电厂", "units": [
            {"unit": "1号机", "hours": hours(realtime=realtime, actual=actual)}]}]}

    def test_daily_totals_are_converted_to_ten_thousand_kwh(self):
        rows = summarize_realtime_units(self.parsed(actual=12.0))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["plant"], "汕头电厂")
        self.assertEqual(row["unit"], "1号机")
        self.assertEqual(row["day_ahead"], 24.0)
        self.assertEqual(row["realtime"], 24.0)
        self.assertEqual(row["actual"], 28.8)
        self.assertEqual(row["difference"], 4.8)
        self.assertAlmostEqual(row["difference_rate"], 0.2)
        self.assertEqual(row["tolerance"], 0.72)
        self.assertEqual(row["status"], "超发")

    def test_incomplete_unit_row_stays_data_insufficient(self):
        parsed = {"plants": [{"plant": "汕头电厂", "units": [
            {"unit": "1号机", "hours": hours()[:20]}]}]}
        row = summarize_realtime_units(parsed)[0]
        self.assertIsNone(row["day_ahead"])
        self.assertIsNone(row["realtime"])
        self.assertIsNone(row["difference"])
        self.assertIsNone(row["difference_rate"])
        self.assertEqual(row["status"], "数据不足")

    def test_empty_input_returns_no_rows(self):
        self.assertEqual(summarize_realtime_units({}), [])
        self.assertEqual(summarize_realtime_units({"plants": []}), [])

    def test_zero_realtime_has_no_difference_rate(self):
        row = summarize_realtime_units(self.parsed(actual=0.0, realtime=0.0))[0]
        self.assertIsNone(row["difference_rate"])
        self.assertEqual(row["tolerance"], 0.02)
        self.assertEqual(row["status"], "未中标/停备")


if __name__ == "__main__":
    unittest.main()
