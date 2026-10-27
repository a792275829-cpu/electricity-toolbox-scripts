from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "update_spot_prices.py"
SPEC = importlib.util.spec_from_file_location("spot_price_update_tests", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def day_prices(day: date, base: float = 1.0) -> tuple[float, ...]:
    return tuple(base + hour / 100 for hour in range(MODULE.EXPECTED_POINT_COUNT))


class DateRangeTests(unittest.TestCase):
    def test_range_includes_both_ends(self):
        self.assertEqual(
            MODULE.date_range(date(2026, 10, 1), date(2026, 10, 3)),
            (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)),
        )

    def test_single_day_range(self):
        self.assertEqual(MODULE.date_range(date(2026, 2, 28), date(2026, 2, 28)), (date(2026, 2, 28),))

    def test_reversed_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "结束日期不能早于开始日期"):
            MODULE.date_range(date(2026, 10, 3), date(2026, 10, 1))


class TargetSelectionTests(unittest.TestCase):
    def test_default_order_and_workbooks(self):
        targets = MODULE.default_targets(
            Path("/tmp/guihu.xlsx"), Path("/tmp/tuolian.xlsx"), Path("/tmp/haifeng.xlsx"))
        self.assertEqual([target.key for target in targets], ["归湖", "鮀莲", "海风"])
        self.assertEqual(targets[0].workbook, Path("/tmp/guihu.xlsx"))
        self.assertEqual(targets[2].tenant_keywords, ("华能广东汕头海上风电有限责任公司",))

    def test_selected_stations_filter(self):
        targets = MODULE.default_targets(
            Path("/tmp/guihu.xlsx"), Path("/tmp/tuolian.xlsx"), Path("/tmp/haifeng.xlsx"),
            selected_stations=("海风", "归湖"))
        self.assertEqual([target.key for target in targets], ["归湖", "海风"])

    def test_empty_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "至少选择一个更新场站"):
            MODULE.default_targets(Path("/tmp/a.xlsx"), Path("/tmp/b.xlsx"), Path("/tmp/c.xlsx"),
                                   selected_stations=())

    def test_unknown_station_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知场站"):
            MODULE.default_targets(Path("/tmp/a.xlsx"), Path("/tmp/b.xlsx"), Path("/tmp/c.xlsx"),
                                   selected_stations=("归湖", "不存在"))


class NameMatchingTests(unittest.TestCase):
    def setUp(self):
        self.target = MODULE.TargetSpec(
            key="归湖", workbook=Path("/tmp/guihu.xlsx"),
            tenant_keywords=("潮州潮安",), unit_keywords=("华归", "归湖"))

    def test_normalize_name_strips_whitespace_and_punctuation(self):
        self.assertEqual(MODULE.normalize_name("  潮州 （潮安） - 供电 "), "潮州潮安供电")
        self.assertEqual(MODULE.normalize_name(None), "")

    def test_single_tenant_match_ignores_formatting(self):
        companies = [
            {"tenantId": "t1", "name": "潮州 潮安供电公司"},
            {"tenantId": "t2", "name": "汕头金平供电公司"},
        ]
        self.assertEqual(MODULE.resolve_tenant(companies, self.target)["tenantId"], "t1")

    def test_ambiguous_tenant_is_rejected(self):
        companies = [
            {"tenantId": "t1", "name": "潮州潮安供电公司"},
            {"tenantId": "t2", "name": "潮州潮安第二供电公司"},
        ]
        with self.assertRaisesRegex(RuntimeError, "匹配到多个公司"):
            MODULE.resolve_tenant(companies, self.target)

    def test_missing_tenant_lists_visible_companies(self):
        companies = [{"tenantId": "t2", "name": "汕头金平供电公司"}]
        with self.assertRaisesRegex(RuntimeError, "汕头金平供电公司"):
            MODULE.resolve_tenant(companies, self.target)

    def test_incomplete_rows_are_ignored(self):
        companies = [{"tenantId": "", "name": "潮州潮安供电公司"}, {"tenantId": "t2", "name": ""}]
        with self.assertRaisesRegex(RuntimeError, "无"):
            MODULE.resolve_tenant(companies, self.target)


class UnitMatchingTests(unittest.TestCase):
    class Auth:
        @staticmethod
        def unit_display_name(row):
            return str(row.get("name") or "")

        @staticmethod
        def is_renewable_unit(row):
            return bool(row.get("renewable"))

    def setUp(self):
        self.target = MODULE.TargetSpec(
            key="归湖", workbook=Path("/tmp/guihu.xlsx"),
            tenant_keywords=("潮州潮安",), unit_keywords=("华归", "归湖"))
        self.auth = self.Auth()

    def test_preferred_keyword_match(self):
        units = [{"id": "u1", "name": "华归光伏"}, {"id": "u2", "name": "鮀莲风电"}]
        self.assertEqual(MODULE.resolve_unit(units, self.target, self.auth)["id"], "u1")

    def test_ambiguous_unit_match_is_rejected(self):
        units = [{"id": "u1", "name": "归湖一期"}, {"id": "u2", "name": "归湖二期"}]
        with self.assertRaisesRegex(RuntimeError, "匹配到多个场站"):
            MODULE.resolve_unit(units, self.target, self.auth)

    def test_single_renewable_fallback_logs_choice(self):
        units = [{"id": "u1", "name": "某新能源场站", "renewable": True}]
        with mock.patch.object(MODULE, "log") as log:
            selected = MODULE.resolve_unit(units, self.target, self.auth)
        self.assertEqual(selected["id"], "u1")
        self.assertIn("只有一个新能源场站", log.call_args[0][0])

    def test_missing_unit_lists_visible_units(self):
        units = [{"id": "u1", "name": "其他场站"}]
        with self.assertRaisesRegex(RuntimeError, "其他场站"):
            MODULE.resolve_unit(units, self.target, self.auth)


class NumberParsingTests(unittest.TestCase):
    def test_accepts_numeric_strings_and_numbers(self):
        self.assertEqual(MODULE._number("1.5", label="电价"), 1.5)
        self.assertEqual(MODULE._number(2, label="电价"), 2.0)

    def test_rejects_empty_and_placeholder_values(self):
        for value in (None, "", "-"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "为空"):
                MODULE._number(value, label="第3行电价")

    def test_rejects_booleans(self):
        with self.assertRaisesRegex(ValueError, "为空"):
            MODULE._number(True, label="电价")

    def test_rejects_non_numeric_and_non_finite(self):
        with self.assertRaisesRegex(ValueError, "不是有效数值"):
            MODULE._number("abc", label="电价")
        for value in (math.nan, math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "不是有限数值"):
                MODULE._number(value, label="电价")


class PricePayloadTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 1)
        self.values = list(day_prices(self.day))

    def payload(self, *, unit_id="u1", points=None, row_date=None):
        return {"data": {"unitDateList": [{
            "unitId": unit_id,
            "date": (row_date or self.day).isoformat() + " 00:00:00",
            "acPrice": self.values if points is None else points,
        }]}}

    def test_parses_dict_payload(self):
        result = MODULE.parse_price_payload(
            self.payload(), unit_id="u1", expected_dates=(self.day,), label="归湖")
        self.assertEqual(result[self.day], tuple(self.values))

    def test_parses_list_payload(self):
        payload = self.payload()["data"]["unitDateList"]
        result = MODULE.parse_price_payload(
            {"data": payload}, unit_id="u1", expected_dates=(self.day,), label="归湖")
        self.assertEqual(result[self.day], tuple(self.values))

    def test_other_units_are_ignored(self):
        with self.assertRaisesRegex(RuntimeError, "缺少日期"):
            MODULE.parse_price_payload(
                self.payload(unit_id="u2"), unit_id="u1", expected_dates=(self.day,), label="归湖")

    def test_unexpected_dates_are_ignored(self):
        with self.assertRaisesRegex(RuntimeError, "缺少日期"):
            MODULE.parse_price_payload(
                self.payload(), unit_id="u1", expected_dates=(date(2026, 10, 2),), label="归湖")

    def test_point_count_must_be_24(self):
        with self.assertRaisesRegex(RuntimeError, "应有 24 个电价点，实际为 23 个"):
            MODULE.parse_price_payload(
                self.payload(points=self.values[:23]), unit_id="u1",
                expected_dates=(self.day,), label="归湖")

    def test_skip_incomplete_logs_instead_of_raising(self):
        with mock.patch.object(MODULE, "log") as log:
            result = MODULE.parse_price_payload(
                self.payload(points=self.values[:23]), unit_id="u1",
                expected_dates=(self.day,), label="归湖", skip_incomplete=True)
        self.assertEqual(result, {})
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any("应有 24 个电价点" in message for message in messages))

    def test_non_list_payload_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不是数组"):
            MODULE.parse_price_payload(
                {"data": {"unitDateList": "oops"}}, unit_id="u1",
                expected_dates=(self.day,), label="归湖")

    def test_invalid_point_value_is_rejected(self):
        bad = self.values[:5] + ["abc"] + self.values[6:]
        with self.assertRaisesRegex(ValueError, "不是有效数值"):
            MODULE.parse_price_payload(
                self.payload(points=bad), unit_id="u1", expected_dates=(self.day,), label="归湖")


class ValueComparisonTests(unittest.TestCase):
    def test_equal_values_match(self):
        self.assertTrue(MODULE.values_match([1, 2.5], (1.0, 2.5)))

    def test_length_and_type_mismatch(self):
        self.assertFalse(MODULE.values_match([1.0], (1.0, 2.0)))
        self.assertFalse(MODULE.values_match([True], (1.0,)))
        self.assertFalse(MODULE.values_match([None], (1.0,)))

    def test_small_difference_is_rejected(self):
        self.assertFalse(MODULE.values_match([1.01], (1.0,)))


class SheetPlanningTests(unittest.TestCase):
    def make_workbook(self, *, sheet_name=MODULE.DAY_AHEAD_SHEET, day=date(2026, 10, 1), values=None):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        sheet.append(["日期"] + [f"{hour:02d}" for hour in range(MODULE.EXPECTED_POINT_COUNT)])
        sheet.append([datetime(day.year, day.month, day.day), *(values or day_prices(day))])
        return workbook, day

    def test_matching_values_are_skipped(self):
        workbook, day = self.make_workbook()
        expectations, updated, skipped, rows = MODULE.plan_price_sheet(
            workbook, MODULE.DAY_AHEAD_SHEET, {day: day_prices(day)}, overwrite=True)
        self.assertEqual((expectations, updated, rows), ([], [], {}))
        self.assertEqual(skipped, [day])

    def test_existing_data_is_kept_without_overwrite(self):
        workbook, day = self.make_workbook()
        with mock.patch.object(MODULE, "log") as log:
            expectations, updated, skipped, _ = MODULE.plan_price_sheet(
                workbook, MODULE.DAY_AHEAD_SHEET, {day: day_prices(day, base=9.0)}, overwrite=False)
        self.assertEqual((expectations, updated), ([], []))
        self.assertEqual(skipped, [day])
        self.assertIn("已有数据", log.call_args[0][0])

    def test_overwrite_plans_every_cell_of_the_row(self):
        workbook, day = self.make_workbook()
        expected = day_prices(day, base=9.0)
        expectations, updated, skipped, rows = MODULE.plan_price_sheet(
            workbook, MODULE.DAY_AHEAD_SHEET, {day: expected}, overwrite=True)
        self.assertEqual((updated, skipped), ([day], []))
        self.assertEqual(len(expectations), 1)
        self.assertEqual(expectations[0].row, 2)
        self.assertEqual(expectations[0].values, expected)
        self.assertEqual(len(expectations[0].number_formats), MODULE.EXPECTED_POINT_COUNT)
        self.assertEqual(rows, {2: expected})

    def test_unknown_sheet_is_rejected(self):
        workbook, day = self.make_workbook()
        with self.assertRaisesRegex(RuntimeError, "未找到工作表"):
            MODULE.plan_price_sheet(workbook, MODULE.REAL_TIME_SHEET, {day: day_prices(day)}, overwrite=True)

    def test_missing_date_is_never_guessed(self):
        workbook, _ = self.make_workbook()
        other = date(2026, 10, 5)
        with self.assertRaisesRegex(RuntimeError, "不会猜测或新增行"):
            MODULE.plan_price_sheet(
                workbook, MODULE.DAY_AHEAD_SHEET, {other: day_prices(other)}, overwrite=True)


class SheetXmlHelperTests(unittest.TestCase):
    def test_column_name_limits(self):
        self.assertEqual(MODULE._column_name(1), "A")
        self.assertEqual(MODULE._column_name(26), "Z")
        for invalid in (0, 27):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "不支持的列号"):
                MODULE._column_name(invalid)

    def test_cell_pattern_matches_only_the_exact_reference(self):
        pattern = MODULE._cell_pattern("B5")
        self.assertIsNotNone(pattern.search('<c r="B5" s="3"/>'))
        self.assertIsNotNone(pattern.search('<c r="B5" s="3"><v>1</v></c>'))
        self.assertIsNone(pattern.search('<c r="B55" s="3"/>'))

    def test_numeric_cell_xml_keeps_precision_and_style(self):
        self.assertEqual(
            MODULE._numeric_cell_xml("C7", 1.5, None), '<c r="C7"><v>1.5</v></c>')
        self.assertEqual(
            MODULE._numeric_cell_xml("C7", 0.1 + 0.2, "4"), '<c r="C7" s="4"><v>0.3</v></c>')

    def test_style_is_read_from_cell_xml(self):
        self.assertEqual(MODULE._style_from_cell_xml('<c r="B5" s="12"><v>1</v></c>'), "12")
        self.assertIsNone(MODULE._style_from_cell_xml('<c r="B5"><v>1</v></c>'))


if __name__ == "__main__":
    unittest.main()
