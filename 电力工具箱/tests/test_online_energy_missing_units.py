from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import openpyxl
from toolbox.runtime import ToolPaths, load_module

ROOT = Path(__file__).resolve().parents[2]


class MissingUnitTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("online_missing_units", ToolPaths(ROOT).online_energy)

    def collect(self, records):
        m = self.module
        self.logger = Mock()
        with patch.object(m, "switch_company"), patch.object(m.time, "sleep"), patch.object(m, "log", self.logger), patch.object(
            m, "get_units", return_value=[{"id": str(i), "unitName": f"海门#{i}机组"} for i in range(1, 6)]
        ), patch.object(m, "fetch_fire_rows", return_value=records):
            return m.collect_date_rows("2026-11-08", Mock(), [{"tenantId": "test", "name": "海门电厂"}], m.Config())

    def test_missing_new_unit_preserves_other_values_and_blank_column(self):
        records = [{"unitId": str(i), "runDate": "2026-11-08", "onlineEle": [i] * 24} for i in range(1, 5)]
        rows = self.collect(records)
        self.assertEqual(len(rows), 120)
        self.assertTrue(any("海门#5机组" in call.args[0] and "未返回" in call.args[0] for call in self.logger.call_args_list))
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-11-08", rows, Path(folder))
            book = openpyxl.load_workbook(path)
            try:
                sheet = book["汇总"]
                self.assertEqual(sheet["E2"].value, "5号机")
                self.assertEqual([sheet.cell(3, col).value for col in range(1, 6)], [1, 2, 3, 4, None])
                self.assertTrue(all(sheet.cell(row, 5).value is None for row in range(3, 27)))
                self.assertEqual(sheet["E3"].number_format, "General")
            finally:
                book.close()

    def test_unknown_duplicate_and_wrong_date_still_stop(self):
        record = {"unitId": "1", "runDate": "2026-11-08", "onlineEle": [0] * 24}
        for records in ([{**record, "unitId": "unknown"}], [record, record], [{**record, "runDate": "2026-11-07"}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.collect(records)

    def test_empty_record_arrays_are_unreported_but_partial_arrays_rejected(self):
        for values in (None, []):
            rows = self.collect([{"unitId": "1", "runDate": "2026-11-08", "onlineEle": values}])
            self.assertEqual(len(rows), 120)
            self.assertTrue(all(row["onlineEle"] is None for row in rows))
        with self.assertRaises(ValueError):
            self.collect([{"unitId": "1", "runDate": "2026-11-08", "onlineEle": [1, 2]}])
