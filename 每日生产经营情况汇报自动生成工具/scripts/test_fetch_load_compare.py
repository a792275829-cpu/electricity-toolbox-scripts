from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[1]
for entry in (str(ROOT), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fetch_load_compare as MODULE

SUBJECTS = {"统调负荷": "1", "B类负荷": "6"}


def period(day_text: str, values, *, total="10", version="2026-10-11 08:00:00") -> dict:
    return {"date": day_text, "loadVal": values, "sum": total, "versionDate": version}


class SubjectMapTests(unittest.TestCase):
    def test_default_map_is_used_without_file(self):
        with mock.patch.object(MODULE, "SUBJECT_MAP_PATH", Path("/nonexistent/subject.json")):
            mapping = MODULE.load_subject_map()
        self.assertEqual(mapping, MODULE.DEFAULT_SUBJECT_MAP)
        self.assertIsNot(mapping, MODULE.DEFAULT_SUBJECT_MAP)

    def test_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text(json.dumps({"自定义负荷": 9}), encoding="utf-8")
            with mock.patch.object(MODULE, "SUBJECT_MAP_PATH", path):
                self.assertEqual(MODULE.load_subject_map(), {"自定义负荷": "9"})


class SubjectNameTests(unittest.TestCase):
    def test_name_and_code_both_work(self):
        with mock.patch.object(MODULE, "load_subject_map", return_value=SUBJECTS):
            self.assertEqual(MODULE.normalize_subject(" 统调负荷 "), ("统调负荷", "1"))
            self.assertEqual(MODULE.normalize_subject("6"), ("B类负荷", "6"))

    def test_empty_and_unknown_are_rejected(self):
        with mock.patch.object(MODULE, "load_subject_map", return_value=SUBJECTS):
            with self.assertRaisesRegex(ValueError, "不能为空"):
                MODULE.normalize_subject("   ")
            with self.assertRaisesRegex(ValueError, "暂不支持的负荷类型"):
                MODULE.normalize_subject("核电负荷")


class SubjectsArgumentTests(unittest.TestCase):
    def test_all_expands_to_every_subject(self):
        with mock.patch.object(MODULE, "load_subject_map", return_value=SUBJECTS):
            self.assertEqual(MODULE.parse_subjects_arg(None, "ALL"), ["统调负荷", "B类负荷"])

    def test_list_and_single_fallbacks(self):
        self.assertEqual(MODULE.parse_subjects_arg(None, " 统调负荷 , ,B类负荷"), ["统调负荷", "B类负荷"])
        self.assertEqual(MODULE.parse_subjects_arg("统调负荷", None), ["统调负荷"])
        self.assertEqual(MODULE.parse_subjects_arg(None, None), ["统调负荷"])

    def test_empty_list_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            MODULE.parse_subjects_arg(None, " , ")


class HelperTests(unittest.TestCase):
    def test_safe_float_defaults_to_zero(self):
        self.assertEqual(MODULE.safe_float("1.5"), 1.5)
        for value in (None, "", "abc"):
            with self.subTest(value=value):
                self.assertEqual(MODULE.safe_float(value), 0.0)

    def test_timestamps_follow_point_count(self):
        self.assertEqual(MODULE.format_timestamp("2026-10-11 00:00:00", 0, 96), "2026/10/11 00:00")
        self.assertEqual(MODULE.format_timestamp("2026-10-11 00:00:00", 1, 96), "2026/10/11 00:15")
        self.assertEqual(MODULE.format_timestamp("2026-10-11 23:00:00", 1, 24), "2026/10/12 00:00")

    def test_filename_and_decimals(self):
        self.assertEqual(MODULE.sanitize_filename("a/b"), "a_b")
        self.assertEqual(MODULE.metric_decimals("总电量(亿kWh)"), 4)
        self.assertEqual(MODULE.metric_decimals("最大值(MW)"), 2)


class SelectLoadTypeTests(unittest.TestCase):
    def test_matching_series_is_returned(self):
        dataset = {"data": {"dataNetLoadDTOList": [{"loadType": "1"}, {"loadType": "6"}]}}
        self.assertEqual(MODULE.select_load_type(dataset, "6"), {"loadType": "6"})

    def test_missing_type_lists_available_codes(self):
        dataset = {"data": {"dataNetLoadDTOList": [{"loadType": "1"}, {"loadType": "6"}]}}
        with self.assertRaisesRegex(RuntimeError, "可用 loadType: 1, 6"):
            MODULE.select_load_type(dataset, "2")


class SummaryTests(unittest.TestCase):
    def test_extremes_average_and_energy(self):
        series = {"forecastPeriodList": [period("2026-10-11 00:00:00", [10, 20, 5], total="35")]}
        summary = MODULE.build_summary(series, "2026-10-11", "2026-10-11", point=96)
        self.assertEqual(summary["maxValue"], 20.0)
        self.assertEqual(summary["maxTime"], "2026/10/11 00:15")
        self.assertEqual(summary["minValue"], 5.0)
        self.assertEqual(summary["minTime"], "2026/10/11 00:30")
        self.assertEqual(summary["avgValue"], 35 / 3)
        self.assertEqual(summary["totalEnergy"], 35.0)
        self.assertEqual((summary["dayCount"], summary["pointCount"]), (1, 3))

    def test_latest_version_date_wins(self):
        series = {"forecastPeriodList": [
            period("2026-10-11 00:00:00", [1], version="2026-10-11 08:00:00"),
            period("2026-10-12 00:00:00", [2], version="2026-10-12 09:00:00"),
        ]}
        summary = MODULE.build_summary(series, "2026-10-11", "2026-10-12", point=96)
        self.assertEqual(summary["versionDate"], "2026-10-12 09:00:00")
        self.assertEqual(summary["dayCount"], 2)

    def test_invalid_entries_are_skipped(self):
        series = {"forecastPeriodList": [
            {"date": "", "loadVal": [1]},
            {"date": "2026-10-11 00:00:00", "loadVal": "not-a-list"},
            period("2026-10-11 00:00:00", [3]),
        ]}
        summary = MODULE.build_summary(series, "2026-10-11", "2026-10-11", point=96)
        self.assertEqual(summary["pointCount"], 1)
        self.assertEqual(summary["maxValue"], 3.0)

    def test_empty_payloads_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "没有返回负荷数据"):
            MODULE.build_summary({}, "2026-10-11", "2026-10-11", point=96)
        with self.assertRaisesRegex(RuntimeError, "没有有效负荷点位数据"):
            MODULE.build_summary({"forecastPeriodList": [period("2026-10-11 00:00:00", [])]},
                                 "2026-10-11", "2026-10-11", point=96)


class ComparisonTests(unittest.TestCase):
    def summary(self, *, max_value, max_time, avg=10.0, total=100.0) -> dict:
        return {"startDate": "2026-10-11", "endDate": "2026-10-11", "versionDate": "v",
                "dayCount": 1, "pointCount": 96, "maxValue": max_value, "maxTime": max_time,
                "minValue": 1.0, "minTime": "t-min", "avgValue": avg, "totalEnergy": total}

    def test_difference_and_year_on_year(self):
        result = MODULE.build_comparison(
            self.summary(max_value=110.0, max_time="new"),
            self.summary(max_value=100.0, max_time="old"))
        peak = result["最大值(MW)"]
        self.assertEqual(peak["difference"], 10.0)
        self.assertEqual(peak["yoyPercent"], 10.0)
        self.assertEqual(peak["selectedTime"], "new")
        self.assertEqual(peak["compareTime"], "old")
        self.assertNotIn("selectedTime", result["平均值(MW)"])

    def test_zero_compare_yields_no_percentage(self):
        result = MODULE.build_comparison(
            self.summary(max_value=10.0, max_time="new", total=5.0),
            self.summary(max_value=0.0, max_time="old", total=0.0))
        self.assertIsNone(result["最大值(MW)"]["yoyPercent"])
        self.assertEqual(result["最大值(MW)"]["difference"], 10.0)

    def test_subject_and_output_payloads(self):
        payload = MODULE.build_subject_payload(
            "统调负荷", "1", self.summary(max_value=110.0, max_time="new"),
            self.summary(max_value=100.0, max_time="old"))
        self.assertEqual(payload["subject"], "统调负荷")
        self.assertEqual(payload["selectedRange"]["pointCount"], 96)
        wrapped = MODULE.build_output_payload([payload])
        self.assertEqual(wrapped["source"], "api")
        self.assertEqual(wrapped["subjects"], [payload])
        with self.assertRaisesRegex(RuntimeError, "没有可输出的负荷指标"):
            MODULE.build_output_payload([])


if __name__ == "__main__":
    unittest.main()
