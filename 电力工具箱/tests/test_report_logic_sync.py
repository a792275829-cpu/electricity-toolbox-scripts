from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from toolbox import file_rules
from toolbox_shared.clearing_files import clearing_file_date, find_workbook_for_day


class ReportFileMatchingTests(unittest.TestCase):
    def test_complete_date_and_latest_match(self):
        with tempfile.TemporaryDirectory() as folder:
            downloads = Path(folder) / "Downloads"
            downloads.mkdir()
            names = ("出清情况2026.11.1.xlsx", "出清情况2026.11.01.xlsx",
                     "出清情况2026.11.10.xlsx", "~$出清情况2026.11.1.xlsx", "其他2026.11.1.xlsx")
            for index, name in enumerate(names):
                path = downloads / name
                path.touch()
                os.utime(path, (index + 1, index + 1))
            expected = downloads / names[1]
            self.assertEqual(find_workbook_for_day(date(2026, 11, 1), downloads), expected)
            with patch("pathlib.Path.home", return_value=downloads.parent):
                self.assertEqual(file_rules.find_workbook_for_day(date(2026, 11, 1)), expected)
            self.assertIsNone(find_workbook_for_day(date(2026, 11, 2), downloads))

    def test_date_formats_and_invalid_dates(self):
        for value in ("2026.11.01", "2026-11-1", "2026年11月1日", "20261101"):
            self.assertEqual(clearing_file_date(Path(f"出清情况{value}.xlsx")), date(2026, 11, 1))
        for value in ("2026.11.100", "2026.13.1", "2026.11.0"):
            self.assertIsNone(clearing_file_date(Path(f"出清情况{value}.xlsx")))

    def test_toolkit_reuses_shared_matcher(self):
        self.assertIs(file_rules.find_workbook_for_day, find_workbook_for_day)

    def test_standalone_gui_uses_complete_date_matching(self):
        import importlib.util

        source = (Path(__file__).resolve().parents[2]
                  / "每日生产经营情况汇报自动生成工具/scripts/report_gui.py")
        spec = importlib.util.spec_from_file_location("report_gui_sync_test", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            downloads = Path(folder)
            expected = downloads / "出清情况2026.11.01.xlsx"
            expected.touch()
            (downloads / "出清情况2026.11.10.xlsx").touch()
            with patch.object(module, "DOWNLOADS_DIR", downloads):
                self.assertEqual(module.ReportTool.find_workbook_for_day(date(2026, 11, 1)), expected)


if __name__ == "__main__":
    unittest.main()
