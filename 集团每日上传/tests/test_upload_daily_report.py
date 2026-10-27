from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "upload_daily_report.py"
SPEC = importlib.util.spec_from_file_location("group_upload_tests", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"xlsx-placeholder")
    return path


class TempFolderTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()


class ReportNameTests(unittest.TestCase):
    def test_name_uses_compact_date(self):
        self.assertEqual(
            MODULE.daily_report_name("2026-10-11"), "广东电力现货市场监测评估日报（20261011）")

    def test_other_input_formats_are_normalized(self):
        expected = "广东电力现货市场监测评估日报（20260503）"
        for value in ("2026/5/3", "2026.5.3", "2026年5月3日", "20260503"):
            with self.subTest(value=value):
                self.assertEqual(MODULE.daily_report_name(value), expected)

    def test_invalid_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "请输入有效年月日"):
            MODULE.daily_report_name("2026-13-01")


class ExtractDateTests(unittest.TestCase):
    def test_compact_date(self):
        self.assertEqual(
            MODULE.extract_date_from_name(Path("广东-省内数据-20261011.xlsx")), "2026-10-11")

    def test_separated_dates(self):
        for name in ("能销数据 2026-10-11.xlsx", "能销数据2026.10.11.xlsx",
                     "能销数据2026_10_11.xlsx", "能销数据2026年10月11日.xlsx"):
            with self.subTest(name=name):
                self.assertEqual(MODULE.extract_date_from_name(Path(name)), "2026-10-11")

    def test_single_digit_month_and_day(self):
        self.assertEqual(MODULE.extract_date_from_name(Path("能销2026-5-3.xlsx")), "2026-05-03")

    def test_missing_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有识别到日期"):
            MODULE.extract_date_from_name(Path("能销数据.xlsx"))


class ClassifyFileTests(unittest.TestCase):
    def test_province_and_energy_keywords(self):
        self.assertEqual(MODULE.classify_file(Path("广东-省内数据-20261011.xlsx")), "省内")
        self.assertEqual(MODULE.classify_file(Path("能销数据-20261011.xlsx")), "能销")

    def test_unknown_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "无法从文件名判断上传类型"):
            MODULE.classify_file(Path("每日数据.xlsx"))

    def test_ambiguous_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "同时匹配多个上传类型"):
            MODULE.classify_file(Path("省内能销合并.xlsx"))


class ProbeTemplateTests(TempFolderTest):
    def test_closest_days_come_first_and_offsets_alternate(self):
        for offset in (0, -1, 1, -2):
            day = datetime(2026, 10, 11).date() + timedelta(days=offset)
            touch(self.folder, f"广东-省内数据-{day:%Y%m%d}.xlsx")
        found = MODULE.probe_standard_province_templates(self.folder, "2026-10-11", days=3)
        self.assertEqual([path.name for path in found], [
            "广东-省内数据-20261011.xlsx",
            "广东-省内数据-20261010.xlsx",
            "广东-省内数据-20261012.xlsx",
            "广东-省内数据-20261009.xlsx",
        ])

    def test_temp_and_generated_files_are_skipped(self):
        touch(self.folder, "~$广东-省内数据-20261011.xlsx")
        touch(self.folder, "广东-省内数据-20261011自动更新.xlsx")
        self.assertEqual(MODULE.probe_standard_province_templates(self.folder, "2026-10-11"), [])


class FindProvinceTemplateTests(TempFolderTest):
    def test_nearest_date_wins(self):
        touch(self.folder, "广东-省内数据-20261010.xlsx")
        touch(self.folder, "广东-省内数据-20261015.xlsx")
        selected = MODULE.find_province_template(self.folder, "2026-10-11")
        self.assertEqual(selected.path.name, "广东-省内数据-20261010.xlsx")
        self.assertEqual(selected.date, "2026-10-10")
        self.assertTrue(selected.auto_template)

    def test_past_date_wins_a_tie_against_future(self):
        touch(self.folder, "广东-省内数据-20261010.xlsx")
        touch(self.folder, "广东-省内数据-20261012.xlsx")
        selected = MODULE.find_province_template(self.folder, "2026-10-11")
        self.assertEqual(selected.date, "2026-10-10")

    def test_builtin_template_is_used_when_folder_is_empty(self):
        builtin = touch(self.folder, "省内数据模板.xlsx")
        empty = self.folder / "empty"
        empty.mkdir()
        with mock.patch.object(MODULE, "BUILTIN_PROVINCE_TEMPLATE", builtin), \
                mock.patch.object(MODULE, "log") as log:
            selected = MODULE.find_province_template(empty, "2026-10-11")
        self.assertTrue(selected.builtin_template)
        self.assertEqual(selected.date, "2026-10-11")
        self.assertIn("内置省内模板", log.call_args[0][0])

    def test_missing_template_lists_visible_files(self):
        (self.folder / "empty").mkdir()
        touch(self.folder / "empty", "别的表.xlsx")
        with mock.patch.object(MODULE, "BUILTIN_PROVINCE_TEMPLATE", self.folder / "missing.xlsx"):
            with self.assertRaisesRegex(ValueError, "别的表.xlsx"):
                MODULE.find_province_template(self.folder / "empty", "2026-10-11")


class PrepareUploadFilesTests(TempFolderTest):
    def test_two_files_are_ordered_province_first(self):
        energy = touch(self.folder, "能销数据-20261012.xlsx")
        province = touch(self.folder, "广东-省内数据-20261011.xlsx")
        run_date, uploads = MODULE.prepare_upload_files([energy, province])
        self.assertEqual(run_date, "2026-10-12")
        self.assertEqual([item.kind for item in uploads], ["省内", "能销"])
        self.assertEqual([item.date for item in uploads], ["2026-10-11", "2026-10-12"])

    def test_single_energy_file_auto_picks_province_sibling(self):
        energy = touch(self.folder, "能销数据-20261012.xlsx")
        touch(self.folder, "广东-省内数据-20261011.xlsx")
        run_date, uploads = MODULE.prepare_upload_files([energy])
        self.assertEqual(run_date, "2026-10-12")
        self.assertEqual([item.kind for item in uploads], ["省内", "能销"])

    def test_single_province_file_is_rejected(self):
        province = touch(self.folder, "广东-省内数据-20261011.xlsx")
        with self.assertRaisesRegex(ValueError, "必须选择能销数据文件"):
            MODULE.prepare_upload_files([province])

    def test_file_count_limits(self):
        paths = [touch(self.folder, f"能销数据-2026101{index}.xlsx") for index in range(3)]
        with self.assertRaisesRegex(ValueError, "请选择一个能销数据文件"):
            MODULE.prepare_upload_files(paths)
        with self.assertRaisesRegex(ValueError, "请选择一个能销数据文件"):
            MODULE.prepare_upload_files([])

    def test_duplicate_types_are_rejected(self):
        first = touch(self.folder, "能销数据-20261011.xlsx")
        second = touch(self.folder, "能销数据-20261012.xlsx")
        with self.assertRaisesRegex(ValueError, "同一类型文件只能选一个"):
            MODULE.prepare_upload_files([first, second])

    def test_missing_file_is_reported(self):
        with self.assertRaisesRegex(FileNotFoundError, "文件不存在"):
            MODULE.prepare_upload_files([self.folder / "不存在.xlsx"])

    def test_builtin_template_takes_the_energy_date(self):
        builtin = touch(self.folder, "省内数据模板.xlsx")
        energy = touch(self.folder, "能销数据-20261012.xlsx")
        with mock.patch.object(MODULE, "BUILTIN_PROVINCE_TEMPLATE", builtin):
            run_date, uploads = MODULE.prepare_upload_files([builtin, energy])
        self.assertEqual(run_date, "2026-10-12")
        province = next(item for item in uploads if item.kind == "省内")
        self.assertTrue(province.builtin_template)
        self.assertEqual(province.date, "2026-10-12")


class LockHelperTests(unittest.TestCase):
    def test_iso_timestamps_are_parsed(self):
        self.assertIsInstance(MODULE.parse_lock_timestamp("2026-10-11T12:00:00Z"), float)
        self.assertIsNone(MODULE.parse_lock_timestamp("not-a-time"))
        self.assertIsNone(MODULE.parse_lock_timestamp(1234))

    def test_process_liveness(self):
        self.assertTrue(MODULE.process_is_alive(os.getpid()))
        self.assertFalse(MODULE.process_is_alive(0))
        self.assertFalse(MODULE.process_is_alive(-1))


class DayReportTenantTests(unittest.TestCase):
    def test_exact_name_is_preferred(self):
        tenants = [
            {"tenantId": "t1", "name": "广东分公司下属"},
            {"tenantId": "t2", "name": "华能广东分公司"},
        ]
        self.assertEqual(MODULE.select_day_report_tenant(tenants)["tenantId"], "t2")

    def test_substring_fallback(self):
        tenants = [{"tenantId": "t1", "name": "中国华能广东分公司（本部）"}]
        self.assertEqual(MODULE.select_day_report_tenant(tenants)["tenantId"], "t1")

    def test_missing_tenant_or_id_returns_none(self):
        self.assertIsNone(MODULE.select_day_report_tenant([]))
        self.assertIsNone(MODULE.select_day_report_tenant([{"tenantId": "", "name": "广东分公司"}]))
        self.assertIsNone(MODULE.select_day_report_tenant([{"tenantId": "t1", "name": "其他公司"}]))


if __name__ == "__main__":
    unittest.main()
