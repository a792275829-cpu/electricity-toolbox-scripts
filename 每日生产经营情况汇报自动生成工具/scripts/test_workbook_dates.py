"""工作簿运行日与日期口径的核对测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_red_marked_report import check_workbook_dates, read_workbook_run_date
import generate_red_marked_report as report

PLAN = {
    "actualOnlineEnergyDate": "2026-11-05",
    "dayAheadDate": "2026-11-06",
    "dailyClearingDate": "2026-11-04",
}


def _make_workbook(path: Path, run_date) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "基础信息"
    if run_date is not None:
        sheet["A2"] = run_date
    workbook.save(path)
    workbook.close()
    return path


class WorkbookDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_matching_dates_produce_no_warning(self) -> None:
        warnings = check_workbook_dates(
            PLAN,
            online_workbook=_make_workbook(self.tmp / "d1.xlsx", datetime(2026, 11, 5)),
            day_ahead_workbook=_make_workbook(self.tmp / "d0.xlsx", datetime(2026, 11, 6)),
            daily_clearing_workbook=_make_workbook(self.tmp / "d2.xlsx", datetime(2026, 11, 4)),
        )
        self.assertEqual(warnings, [])

    def test_swapped_workbooks_are_reported(self) -> None:
        # 前后两日的文件选反，是最容易发生也最难靠肉眼发现的错误。
        warnings = check_workbook_dates(
            PLAN,
            online_workbook=_make_workbook(self.tmp / "d0.xlsx", datetime(2026, 11, 6)),
            daily_clearing_workbook=_make_workbook(self.tmp / "d1.xlsx", datetime(2026, 11, 5)),
        )
        self.assertEqual(len(warnings), 2)
        first = warnings[0]
        self.assertEqual(first["expectedDate"], "2026-11-05")
        self.assertEqual(first["actualDate"], "2026-11-06")
        self.assertEqual(first["workbook"], "online_workbook")

    def test_missing_run_date_is_reported_instead_of_passing(self) -> None:
        warnings = check_workbook_dates(
            PLAN,
            day_ahead_workbook=_make_workbook(self.tmp / "blank.xlsx", None),
        )
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["actualDate"], "读不到运行日")

    def test_absent_workbook_is_skipped(self) -> None:
        self.assertEqual(check_workbook_dates(PLAN, online_workbook=None), [])

    def test_filename_date_is_complete_and_supports_zero_padding(self) -> None:
        import os

        selected = _make_workbook(self.tmp / "2026.11.01出清情况.xlsx", datetime(2026, 11, 1))
        other = _make_workbook(self.tmp / "2026.11.10出清情况.xlsx", datetime(2026, 11, 10))
        os.utime(selected, (1000, 1000))
        os.utime(other, (2000, 2000))
        self.assertEqual(report.find_clearing_workbook("2026-11-01", self.tmp), selected)
        selected.unlink()
        with self.assertRaises(FileNotFoundError):
            report.find_clearing_workbook("2026-11-01", self.tmp)

    def test_auto_selected_day_ahead_date_is_checked_before_fetch(self) -> None:
        source = _make_workbook(self.tmp / "2026.11.6出清情况.xlsx", datetime(2026, 11, 5))
        class StopBeforeFetch(Exception):
            pass

        with (
            patch.object(report, "find_clearing_workbook", return_value=source) as finder,
            patch.object(report, "check_workbook_dates", wraps=check_workbook_dates) as check,
            patch.object(report, "log") as logger,
            patch.object(report, "resolve_template_path", side_effect=StopBeforeFetch),
            self.assertRaises(StopBeforeFetch),
        ):
            report.generate("2026-11-06", self.tmp / "template.docx", self.tmp)
        finder.assert_called_once_with("2026-11-06")
        self.assertEqual(check.call_args.kwargs["day_ahead_workbook"], source)
        self.assertTrue(any("实际运行日 2026-11-05" in call.args[0] for call in logger.call_args_list))

    def test_text_date_and_missing_sheet(self) -> None:
        text_path = self.tmp / "text.xlsx"
        workbook = Workbook()
        workbook.active.title = "基础信息"
        workbook.active["A2"] = "2026-11-04"
        workbook.save(text_path)
        workbook.close()
        self.assertEqual(read_workbook_run_date(text_path), "2026-11-04")

        plain_path = self.tmp / "plain.xlsx"
        workbook = Workbook()
        workbook.active.title = "快报"
        workbook.save(plain_path)
        workbook.close()
        self.assertIsNone(read_workbook_run_date(plain_path))


if __name__ == "__main__":
    unittest.main()
