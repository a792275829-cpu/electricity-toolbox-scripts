from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import ToolPaths, load_module


ROOT = Path(__file__).resolve().parents[2]


class OnlineEnergyOutputTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_online_energy_output", ToolPaths(ROOT).online_energy)

    def test_default_and_empty_config_use_downloads_subfolder(self):
        expected = Path.home() / "Downloads" / "上网电量"
        self.assertEqual(self.module.OUTPUT_DIR, expected)
        for raw in ({}, {"outputDir": ""}, {"outputDir": None}):
            with self.subTest(raw=raw), mock.patch.object(self.module, "read_login_config", return_value=raw):
                self.assertEqual(self.module.load_config().output_dir, expected)

    def test_toolbox_directory_matches_export_config(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = ToolPaths(Path(folder))
            config_path = paths.online_energy_dir / "config.json"
            config_path.parent.mkdir()
            for raw in ({}, {"outputDir": "~/Downloads/自定义电量"}):
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(raw=raw), mock.patch.object(self.module, "read_login_config", return_value=raw):
                    self.assertEqual(paths.online_energy_output_dir, self.module.load_config().output_dir)

    def test_managed_data_root_does_not_redirect_default_downloads(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {
            "TOOLBOX_CODE_ROOT": str(ROOT), "TOOLBOX_DATA_ROOT": folder,
        }):
            self.assertEqual(ToolPaths(ROOT).online_energy_output_dir, Path.home() / "Downloads" / "上网电量")

    def test_export_creates_folder_and_workbook_can_be_read_back(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "Downloads" / "上网电量"
            rows = [{"company": "汕头电厂", "unit": "#1", "time": "00:00", "onlineEle": 12.5}]
            path = self.module.save_excel("2026-10-24", rows, output)
            self.assertEqual(path, output / "上网电量汇总_2026-10-24.xlsx")
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A1"].value, "汕头 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["A2"].value, "1号机")
                self.assertEqual(sheet["A3"].value, 12.5)
                self.assertEqual(sheet["A3"].number_format, "General")
                self.assertEqual(sheet["A4"].value, 0)
            finally:
                workbook.close()

    def test_qingyuan_two_groups_are_appended_with_hourly_values(self):
        import openpyxl

        rows = [
            {"company": company, "unit": unit, "time": f"{hour:02d}:00", "onlineEle": value + hour}
            for company, unit, value in (
                ("清远电厂", "清远#1、#2机", 100.7),
                ("清远电厂", "清远#3、#4机", 0),
                ("汕头电厂", "#1", 10),
            )
            for hour in range(24)
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-10-28", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A1"].value, "汕头 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["A28"].value, "清远 实际上网电量（万千瓦时）")
                self.assertEqual((sheet["A29"].value, sheet["B29"].value), ("1、2号机", "3、4号机"))
                self.assertEqual((sheet["A30"].value, sheet["B30"].value), (100.7, 0))
                self.assertEqual((sheet["A53"].value, sheet["B53"].value), (123.7, 23))
                self.assertEqual(sheet["A30"].number_format, "General")
            finally:
                workbook.close()

    def test_qingyuan_api_uses_online_columns_and_checks_hours(self):
        columns = [{"unitId": unit, "fieldKey": f"{unit}_{metric}", "metricType": metric}
                   for unit in ("a", "b") for metric in ("actualEle", "onlineEle")]
        rows = [{"time": f"{hour:02d}:00", "valueMap": {
            "a_actualEle": 999, "a_onlineEle": hour + 0.7,
            "b_actualEle": 999, "b_onlineEle": 0,
        }} for hour in reversed(range(24))]
        context = mock.Mock()
        with mock.patch.object(self.module, "parse_json_response", return_value={"data": {"columnList": columns, "rowList": rows}}):
            result = self.module.fetch_qingyuan_rows(context, "2026-10-28", "tenant")
            self.assertEqual(result[0]["onlineEle"][0], 0.7)
            self.assertEqual(result[0]["onlineEle"][-1], 23.7)
            self.assertEqual(result[1]["onlineEle"], [0] * 24)
            self.assertEqual(context.request.get.call_args.kwargs["params"], {
                "runDate": "2026-10-28", "endDate": "2026-10-28", "orgId": "tenant"})
            rows[-1]["time"] = rows[0]["time"]
            with self.assertRaisesRegex(ValueError, "重复"):
                self.module.fetch_qingyuan_rows(context, "2026-10-28", "tenant")
