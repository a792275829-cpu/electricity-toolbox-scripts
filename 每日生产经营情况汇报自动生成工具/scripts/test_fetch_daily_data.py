from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[1]
for entry in (str(ROOT), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fetch_daily_data as MODULE


class SubjectTests(unittest.TestCase):
    def test_all_and_empty_select_every_subject(self):
        with mock.patch.object(MODULE, "load_subject_map", return_value={"1": "统调", "2": "B类"}):
            self.assertEqual(MODULE.parse_subjects(None), ["1", "2"])
            self.assertEqual(MODULE.parse_subjects(""), ["1", "2"])
            self.assertEqual(MODULE.parse_subjects(" ALL "), ["1", "2"])

    def test_explicit_list_is_trimmed(self):
        self.assertEqual(MODULE.parse_subjects("1, 2 ,,6"), ["1", "2", "6"])


class ReportDatePlanTests(unittest.TestCase):
    def test_offsets_follow_the_report_contract(self):
        plan = MODULE.build_report_date_plan("2026-10-11")
        self.assertEqual(plan["reportDate"], "2026-10-11")
        self.assertEqual(plan["compareDate"], "2026-10-10")
        self.assertEqual(plan["actualOnlineEnergyDate"], "2026-10-10")
        self.assertEqual(plan["dayAheadDate"], "2026-10-11")
        self.assertEqual(plan["loadAndPriceSelectedDate"], "2026-10-11")
        self.assertEqual(plan["loadAndPriceCompareDate"], "2026-10-10")
        # 日清分数据固定取 D-2。
        self.assertEqual(plan["dailyClearingDate"], "2026-10-09")
        self.assertEqual(plan["rollingForecastStartDate"], "2026-10-12")
        self.assertEqual(plan["rollingForecastEndDate"], "2026-10-16")

    def test_month_and_year_boundaries(self):
        plan = MODULE.build_report_date_plan("2026-03-01")
        self.assertEqual(plan["compareDate"], "2026-02-28")
        self.assertEqual(plan["dailyClearingDate"], "2026-02-27")
        self.assertEqual(plan["rollingForecastEndDate"], "2026-03-06")
        new_year = MODULE.build_report_date_plan("2027-01-01")
        self.assertEqual(new_year["compareDate"], "2026-12-31")
        self.assertEqual(new_year["dailyClearingDate"], "2026-12-30")


class FilenameTests(unittest.TestCase):
    def test_illegal_characters_are_replaced(self):
        self.assertEqual(MODULE.sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_plain_names_are_untouched(self):
        self.assertEqual(MODULE.sanitize_filename("6项指标"), "6项指标")


class ValueHelperTests(unittest.TestCase):
    def test_date_only_keeps_the_day(self):
        self.assertEqual(MODULE.date_only("2026-10-11 00:00:00"), "2026-10-11")
        self.assertEqual(MODULE.date_only(None), "")

    def test_number_or_none(self):
        self.assertEqual(MODULE.number_or_none("1.5"), 1.5)
        self.assertEqual(MODULE.number_or_none(3), 3.0)
        for value in (None, "", "abc"):
            with self.subTest(value=value):
                self.assertIsNone(MODULE.number_or_none(value))


class HourlyAverageTests(unittest.TestCase):
    def test_four_points_per_hour(self):
        values = [4.0] * 96
        self.assertEqual(MODULE.hourly_averages(values), [4.0] * 24)

    def test_incomplete_hour_becomes_none(self):
        values = [4.0] * 96
        values[5] = None
        hours = MODULE.hourly_averages(values)
        self.assertIsNone(hours[1])
        self.assertEqual(hours[0], 4.0)

    def test_short_series_leaves_trailing_hours_empty(self):
        hours = MODULE.hourly_averages([2.0] * 8)
        self.assertEqual(hours[:2], [2.0, 2.0])
        self.assertEqual(hours[2:], [None] * 22)


class ForecastRowTests(unittest.TestCase):
    def item(self, values, **extra):
        return {"date": "2026-10-12 00:00:00", "runDate": "2026-10-11 08:00:00",
                "forecastPower": values, **extra}

    def test_totals_and_points(self):
        row = MODULE.build_forecast_row(self.item([4.0] * 96))
        self.assertEqual(row["forecastDate"], "2026-10-12")
        self.assertEqual(row["runDate"], "2026-10-11")
        self.assertEqual(row["pointCount"], 96)
        self.assertEqual(row["dailyTotal"], 96.0)
        self.assertEqual(row["dailyAverage"], 4.0)
        self.assertEqual(len(row["hours"]), 24)

    def test_modified_values_take_priority(self):
        row = MODULE.build_forecast_row(self.item([1.0] * 96, modifyForecastPower=[8.0] * 96))
        self.assertEqual(row["dailyAverage"], 8.0)

    def test_invalid_points_are_dropped(self):
        values = [4.0] * 96
        values[0] = None
        row = MODULE.build_forecast_row(self.item(values))
        self.assertEqual(row["pointCount"], 95)
        self.assertEqual(row["dailyTotal"], 95.0)
        self.assertIsNone(row["hours"][0])

    def test_empty_payload_is_handled(self):
        row = MODULE.build_forecast_row(self.item([]))
        self.assertIsNone(row["dailyTotal"])
        self.assertIsNone(row["dailyAverage"])
        self.assertEqual(row["hours"], [None] * 24)


class PeriodSummaryTests(unittest.TestCase):
    def test_missing_period_yields_empty_values(self):
        self.assertEqual(MODULE.summarize_period(None),
                         {"max": None, "min": None, "avg": None, "sum": None})

    def test_values_and_extreme_dates_are_kept(self):
        summary = MODULE.summarize_period(
            {"max": "10", "min": 2, "avg": "3.5", "sum": "100", "maxDate": "2026-10-11 19:00"})
        self.assertEqual((summary["max"], summary["min"], summary["avg"], summary["sum"]),
                         (10.0, 2.0, 3.5, 100.0))
        self.assertEqual(summary["maxDate"], "2026-10-11 19:00")
        self.assertIsNone(summary["minDate"])


class DiffTests(unittest.TestCase):
    def test_differences_are_computed(self):
        result = MODULE.diff_summary(
            {"max": 10.0, "min": 1.0, "avg": 5.0, "sum": 100.0},
            {"max": 8.0, "min": 1.0, "avg": 4.0, "sum": 90.0})
        self.assertEqual(result["maxDiff"], 2.0)
        self.assertEqual(result["minDiff"], 0.0)
        self.assertEqual(result["avgDiff"], 1.0)
        self.assertEqual(result["sumDiff"], 10.0)

    def test_missing_values_produce_no_difference(self):
        result = MODULE.diff_summary({"max": None}, {"max": 8.0})
        self.assertIsNone(result["maxDiff"])

    def test_comparison_keeps_dates_and_missing_types(self):
        selected = {"date": "2026-10-11", "byType": {
            "1": {"dayAhead": {"max": 10.0}, "actual": {}},
            "2": {"dayAhead": {}, "actual": {}},
        }}
        compare = {"date": "2026-10-10", "byType": {"1": {"dayAhead": {"max": 9.0}}}}
        result = MODULE.build_load_info_comparison(selected, compare)
        self.assertEqual(result["selectedDate"], "2026-10-11")
        self.assertEqual(result["compareDate"], "2026-10-10")
        self.assertEqual(result["byType"]["1"]["dayAhead"]["maxDiff"], 1.0)
        self.assertIsNone(result["byType"]["2"]["dayAhead"]["maxDiff"])
        self.assertEqual(tuple(result["byType"]), ("1", "2"))


if __name__ == "__main__":
    unittest.main()
