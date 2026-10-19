from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

import export_online_energy as report_export


class ReportExportReuseTests(unittest.TestCase):
    def test_report_keeps_independent_paths_and_default_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(report_export, "CONFIG_PATH", Path(directory) / "missing.json"):
                config = report_export.load_config()
        self.assertEqual(report_export.OUTPUT_DIR, config.output_dir)
        self.assertEqual(report_export.AUTH_LOCK_PATH, report_export.AuthStateLock().path)
        self.assertEqual(report_export.AUTH_STATE_PATH.parent, report_export.SCRIPT_DIR)
        self.assertEqual(report_export.PROFILE_DIR.parent, report_export.SCRIPT_DIR)

    def test_summary_export_values_survive_reopen(self) -> None:
        rows = [
            {"company": "汕头电厂", "unit": "#1机组", "time": "00:00", "onlineEle": 12.5},
            {"company": "汕头电厂", "unit": "#1机组", "time": "01:00", "onlineEle": 3.25},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = report_export.save_excel("2026-09-24", rows, Path(directory))
            workbook = load_workbook(path, data_only=True)
            try:
                sheet = workbook["汇总"]
                self.assertEqual("汕头 实际上网电量（万千瓦时）", sheet["A1"].value)
                self.assertEqual("1号机", sheet["A2"].value)
                self.assertEqual(12.5, sheet["A3"].value)
                self.assertEqual(3.25, sheet["A4"].value)
                self.assertEqual("General", sheet["A3"].number_format)
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
