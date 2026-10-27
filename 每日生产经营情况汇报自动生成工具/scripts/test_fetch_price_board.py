from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[1]
for entry in (str(ROOT), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fetch_price_board as MODULE


def payload(avg, max_values, min_values) -> dict:
    return {"data": {"avgList": avg, "maxList": max_values, "minList": min_values}}


class NumberHelperTests(unittest.TestCase):
    def test_safe_number(self):
        self.assertEqual(MODULE.safe_number("1.5"), 1.5)
        for value in (None, "", "abc"):
            with self.subTest(value=value):
                self.assertIsNone(MODULE.safe_number(value))

    def test_round_or_none_and_average(self):
        self.assertIsNone(MODULE.round_or_none(None))
        self.assertEqual(MODULE.round_or_none(1.23456789), 1.2346)
        self.assertIsNone(MODULE.average([]))
        self.assertEqual(MODULE.average([1.0, 2.0, 6.0]), 3.0)


class PriceTypeTests(unittest.TestCase):
    def test_names_and_codes(self):
        self.assertEqual(MODULE.normalize_price_type("日前"), ("日前", "PRE"))
        self.assertEqual(MODULE.normalize_price_type(" 实时 "), ("实时", "RT"))
        self.assertEqual(MODULE.normalize_price_type("pre"), ("日前", "PRE"))
        self.assertEqual(MODULE.normalize_price_type("rt"), ("实时", "RT"))

    def test_unknown_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持的价格类型"):
            MODULE.normalize_price_type("月内")

    def test_supported_types(self):
        self.assertEqual(list(MODULE.PRICE_DATA_TYPES), ["日前", "实时"])


class PriceSummaryTests(unittest.TestCase):
    def test_summary_values_and_unit(self):
        summary = MODULE.build_price_summary(
            "2026-10-11", "日前", "PRE", payload([100, 200, 300], [500], [10]))
        self.assertEqual(summary["pointCount"], 3)
        self.assertEqual(summary["dailyAvgPrice"], 200.0)
        self.assertEqual(summary["maxPrice"], 500.0)
        self.assertEqual(summary["minPrice"], 10.0)
        self.assertEqual(summary["unit"], "厘/kWh")
        self.assertTrue(summary["hasData"])

    def test_invalid_points_are_dropped(self):
        summary = MODULE.build_price_summary(
            "2026-10-11", "实时", "RT", payload([100, None, "x", 300], ["bad"], []))
        self.assertEqual(summary["pointCount"], 2)
        self.assertEqual(summary["dailyAvgPrice"], 200.0)
        self.assertIsNone(summary["maxPrice"])
        self.assertIsNone(summary["minPrice"])

    def test_empty_payload_marks_no_data(self):
        summary = MODULE.build_price_summary("2026-10-11", "日前", "PRE", {})
        self.assertFalse(summary["hasData"])
        self.assertEqual(summary["pointCount"], 0)
        self.assertIsNone(summary["dailyAvgPrice"])


class OutputPayloadTests(unittest.TestCase):
    def summary(self, type_name: str, data_type: str, avg) -> dict:
        return {"date": "2026-10-11", "typeName": type_name, "dataType": data_type,
                "unit": "厘/kWh", "pointCount": 96, "dailyAvgPrice": avg}

    def test_differences_between_dates(self):
        output = MODULE.build_output_payload(
            "2026-10-11", "2026-10-10",
            [self.summary("日前", "PRE", 200.0), self.summary("实时", "RT", 150.0)],
            [self.summary("日前", "PRE", 190.0)])
        rows = {row["dataType"]: row for row in output["rows"]}
        self.assertEqual(rows["PRE"]["difference"], 10.0)
        self.assertEqual(rows["PRE"]["compareDailyAvgPrice"], 190.0)
        self.assertIsNone(rows["RT"]["difference"])
        self.assertIsNone(rows["RT"]["compareDailyAvgPrice"])
        self.assertEqual(output["compareDate"], "2026-10-10")

    def test_missing_compare_list_is_allowed(self):
        output = MODULE.build_output_payload(
            "2026-10-11", None, [self.summary("日前", "PRE", 200.0)], None)
        self.assertEqual(output["compare"], [])
        self.assertIsNone(output["rows"][0]["difference"])
        self.assertIsNone(output["rows"][0]["compareDate"])

    def test_missing_value_keeps_difference_empty(self):
        output = MODULE.build_output_payload(
            "2026-10-11", "2026-10-10",
            [self.summary("日前", "PRE", None)], [self.summary("日前", "PRE", 190.0)])
        self.assertIsNone(output["rows"][0]["difference"])


if __name__ == "__main__":
    unittest.main()
