from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

from toolbox.runtime import load_module


class MarketTableBatchWriteTests(unittest.TestCase):
    def test_date_range_uses_one_login_and_commits_only_after_all_days(self):
        module = load_module("market_date_range", Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py")
        start, end = date(2026, 10, 8), date(2026, 10, 9)
        earlier = date(2026, 10, 2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.xlsx"
            book = Workbook()
            market = book.active
            market.title = module.MARKET_SHEET_NAME
            cost = book.create_sheet(module.COST_SHEET_NAME)
            for row in range(2, 10):
                market.cell(row, 1, earlier + timedelta(days=row - 2))
                cost.cell(row, 1, market.cell(row, 1).value)
            market["BG1"].number_format = "0.00%"
            market["B9"] = "=1+1"
            market["C9"] = 99
            cost["B7"] = "运行"
            book.save(path)
            book.close()
            original = path.read_bytes()

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(module, "load_config"))
                stack.enter_context(mock.patch.object(module, "AuthStateLock"))
                stack.enter_context(mock.patch.object(module, "sync_playwright"))
                launch = stack.enter_context(mock.patch.object(module, "launch_context"))
                login = stack.enter_context(mock.patch.object(module, "ensure_login"))
                for name in ("realtime_market", "actual_load", "user_settlement",
                             "unit_cost_price", "market_board_online_unit", "spot_clearing_unit_count"):
                    stack.enter_context(mock.patch.object(module, f"fetch_{name}_values", return_value=None))
                settlement = stack.enter_context(mock.patch.object(module, "fetch_generation_settlement_values",
                    return_value=[0.125] * 12))
                stack.enter_context(mock.patch.object(module, "fetch_day_ahead_market_values",
                    side_effect=lambda _, day: list(range(day.day, day.day + 12))))
                load = stack.enter_context(mock.patch.object(module, "fetch_day_ahead_load_values",
                    side_effect=[list(range(20)), []]))
                with self.assertRaisesRegex(ValueError, "20 个"):
                    module.run_update_range(path, start, end, headless=True)
                self.assertEqual(path.read_bytes(), original)

                login.reset_mock()
                launch.reset_mock()
                settlement.reset_mock()
                load.side_effect = [list(range(20)), list(range(20, 40))]
                result = module.run_update_range(
                    path, start, end, headless=True, overwrite=False, create_backup=True
                )
                self.assertEqual(list(result), [start, end])
                login.assert_called_once()
                launch.return_value.close.assert_called_once()
                self.assertEqual([call.args[1] for call in settlement.call_args_list],
                                 [earlier, earlier + timedelta(days=1)])
            [backup] = list(path.parent.glob("market.备份-*.xlsx"))
            self.assertEqual(backup.read_bytes(), original)
            saved = load_workbook(path)
            try:
                market = saved[module.MARKET_SHEET_NAME]
                cost = saved[module.COST_SHEET_NAME]
                self.assertEqual([market[cell].value for cell in ("B8", "B9", "C9", "D9")],
                                 [0, "=1+1", 99, 22])
                self.assertEqual([market[cell].value for cell in ("V7", "AG7", "V8", "AG8")],
                                 [7, 18, 8, 19])
                for cell in ("BG2", "BG3"):
                    self.assertEqual(market[cell].value, 0.125)
                    self.assertEqual(market[cell].number_format, "0.00%")
                self.assertEqual([cost[cell].value for cell in ("B8", "B9")], ["运行", "运行"])
            finally:
                saved.close()

    def test_array_anchor_and_spill_cells_are_protected_with_overwrite(self):
        from openpyxl.worksheet.formula import ArrayFormula
        module = load_module('market_array_protection', Path(__file__).resolve().parents[2] / '市场表更新/update_market_table.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'market.xlsx'
            book = Workbook(); sheet = book.active; sheet.title = module.MARKET_SHEET_NAME
            sheet['A2'] = date(2026, 10, 8)
            sheet['V2'] = ArrayFormula(ref='V2:X2', text='=SEQUENCE(1,3)')
            sheet['W2'] = 2; sheet['W2'].number_format = '0.00%'
            book.save(path); book.close()
            result = module.write_day_ahead_market_values(path, date(2026, 10, 8), list(range(12)), overwrite=True)
            self.assertEqual((result['count'], result['skipped']), (9, 3))
            book = load_workbook(path)
            try:
                sheet = book[module.MARKET_SHEET_NAME]
                self.assertEqual(sheet['V2'].value.ref, 'V2:X2')
                self.assertEqual(sheet['V2'].value.text, '=SEQUENCE(1,3)')
                self.assertEqual(sheet['W2'].value, 2)
                self.assertEqual(sheet['W2'].number_format, '0.00%')
                self.assertIsNone(sheet['X2'].value)
                self.assertEqual(sheet['Y2'].value, 3)
            finally: book.close()


    def test_missing_tail_dates_are_extended_and_read_back(self):
        module = load_module("market_tail_extension", Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.xlsx"
            book = Workbook(); sheet = book.active; sheet.title = module.MARKET_SHEET_NAME
            sheet["A2"] = date(2026, 10, 8); sheet["A2"].number_format = "yyyy/m/d"
            sheet["V2"] = 99; sheet["V2"].number_format = "0.00%"
            sheet["A30"].number_format = "yyyy/m/d"
            book.create_sheet("保留")["A1"] = "=1+1"
            book.save(path); book.close()
            module.write_day_ahead_market_values(path, date(2026, 10, 11), list(range(12)))
            module.write_market_row_values(workbook_path=path, run_date=date(2026,10,11),
                values=list(range(12)), start_column="V", expected_count=12, label="日前市场")
            book = load_workbook(path)
            try:
                sheet = book[module.MARKET_SHEET_NAME]
                self.assertEqual([sheet.cell(r, 1).value.date() for r in (3, 4, 5)],
                                 [date(2026, 10, d) for d in (9, 10, 11)])
                self.assertIsNone(sheet["V3"].value)
                self.assertEqual(sheet["V2"].value, 99)
                self.assertEqual(sheet["V5"].number_format, "0.00%")
                self.assertEqual(tuple(sheet.cell(5, col).value for col in range(22, 34)), tuple(range(12)))
                self.assertEqual(book["保留"]["A1"].value, "=1+1")
                self.assertEqual(sheet["A5"].number_format, "yyyy/m/d")
            finally: book.close()

    def test_overwrite_switch_replaces_existing_cells_and_backup_copies_original(self):
        module = load_module("market_overwrite_test",
                             Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.xlsx"
            book = Workbook(); sheet = book.active; sheet.title = module.MARKET_SHEET_NAME
            sheet["A2"] = date(2026, 10, 8); sheet["V2"] = 99
            book.save(path); book.close()

            skipped = module.write_day_ahead_market_values(path, date(2026, 10, 8), list(range(12)))
            self.assertEqual((skipped["count"], skipped["skipped"]), (11, 1))
            book = load_workbook(path)
            try:
                sheet = book[module.MARKET_SHEET_NAME]
                self.assertEqual(sheet["V2"].value, 99)
                self.assertEqual(sheet["W2"].value, 1)
            finally:
                book.close()

            written = module.write_day_ahead_market_values(
                path, date(2026, 10, 8), list(range(12)), overwrite=True)
            self.assertEqual((written["count"], written["skipped"]), (12, 0))
            book = load_workbook(path)
            try:
                sheet = book[module.MARKET_SHEET_NAME]
                self.assertEqual(tuple(sheet.cell(2, column).value for column in range(22, 34)),
                                 tuple(range(12)))
            finally:
                book.close()

            backup = module.backup_workbook(path)
            self.assertTrue(backup.is_file())
            self.assertIn("备份-", backup.name)
            self.assertEqual(backup.read_bytes(), path.read_bytes())
            second = module.backup_workbook(path)
            self.assertNotEqual(second, backup)

    def test_unsafe_extension_preserves_original(self):
        module = load_module("market_unsafe_extension", Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py")
        for problem in ("footer", "merge", "history", "year", "duplicate", "month"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "market.xlsx"
                book = Workbook(); sheet = book.active; sheet.title = module.MARKET_SHEET_NAME
                sheet["A2"] = date(2026, 10, 8)
                if problem == "footer": sheet["A3"] = "10月合计"
                if problem == "merge": sheet.merge_cells("A3:A4")
                if problem == "duplicate": sheet["A3"] = date(2026, 10, 8)
                if problem == "month": sheet["A1"] = "10月"
                book.save(path); book.close(); before = path.read_bytes()
                target = date(2027, 1, 1) if problem == "year" else date(2026, 10, 7 if problem == "history" else 8 if problem == "duplicate" else 9)
                if problem == "month": target = date(2026,11,1)
                with self.assertRaises(RuntimeError):
                    module.write_day_ahead_market_values(path, target, list(range(12)))
                self.assertEqual(path.read_bytes(), before)

    def test_external_save_during_session_is_preserved(self) -> None:
        module = load_module("market_table_external_save_test",
            Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.xlsx"
            book = Workbook()
            book.active["A1"] = "原值"
            book.save(path)
            book.close()
            session = module.WorkbookWriteSession(path)
            updated = session.get_workbook()
            updated.active["B1"] = 42
            session.record(updated.active["B1"])
            external = load_workbook(path)
            external.active["A1"] = "外部保存"
            external.save(path)
            external.close()
            original = path.read_bytes()
            try:
                with self.assertRaisesRegex(RuntimeError, "工作簿被修改"):
                    session.commit()
            finally:
                updated.close()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_readback_format_mismatch_preserves_original(self) -> None:
        module = load_module(
            "market_table_stream_verification_test",
            Path(__file__).resolve().parents[2] / "市场表更新/update_market_table.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.xlsx"
            workbook = Workbook()
            workbook.active.title = module.MARKET_SHEET_NAME
            workbook.active["V2"] = 0.125
            workbook.active["V2"].number_format = "0.00%"
            workbook.save(path)
            original = path.read_bytes()
            session = module.WorkbookWriteSession(path)
            updated = session.get_workbook()
            updated[module.MARKET_SHEET_NAME]["V2"] = 0.25
            session.record(updated[module.MARKET_SHEET_NAME]["V2"])
            save = updated.save

            def corrupt_format(target):
                save(target)
                changed = load_workbook(target)
                changed[module.MARKET_SHEET_NAME]["V2"].number_format = "0.000"
                changed.save(target)
                changed.close()

            try:
                with mock.patch.object(updated, "save", side_effect=corrupt_format):
                    with self.assertRaisesRegex(RuntimeError, "市场表保存后回读不一致"):
                        session.commit()
            finally:
                updated.close()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".market.*.xlsx")), [])

    def test_numeric_write_ignores_date_format_on_previous_blank_row(self) -> None:
        module = load_module(
            "market_table_numeric_format_test",
            Path(__file__).resolve().parents[2] / "市场表更新" / "update_market_table.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = module.COST_SHEET_NAME
            sheet["A2"] = date(2026, 9, 23)
            sheet["A3"] = date(2026, 9, 24)
            sheet["AA2"].number_format = "[$-409]yyyy/mm/dd;@"
            sheet["AA3"].number_format = "0_ "
            workbook.save(path)

            with module.workbook_write_session(path):
                module.write_market_board_online_unit_values(
                    path, date(2026, 9, 24), [504, 9000]
                )

            saved = load_workbook(path)
            try:
                self.assertEqual(saved[module.COST_SHEET_NAME]["AA3"].value, 504)
                self.assertEqual(saved[module.COST_SHEET_NAME]["AA3"].number_format, "0_ ")
            finally:
                saved.close()

    def test_failed_update_keeps_original_workbook(self) -> None:
        module = load_module(
            "market_table_batch_failure_test",
            Path(__file__).resolve().parents[2] / "市场表更新" / "update_market_table.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = module.MARKET_SHEET_NAME
            sheet["A2"] = date(2026, 7, 2)
            workbook.save(path)
            original_bytes = path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "模拟后续接口失败"):
                with module.workbook_write_session(path):
                    module.write_day_ahead_market_values(
                        path, date(2026, 7, 2), list(range(12))
                    )
                    raise RuntimeError("模拟后续接口失败")

            self.assertEqual(path.read_bytes(), original_bytes)

    def test_one_workbook_load_and_save_preserves_values_and_formats(self) -> None:
        module = load_module(
            "market_table_batch_write_test",
            Path(__file__).resolve().parents[2] / "市场表更新" / "update_market_table.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.xlsx"
            workbook = Workbook()
            market = workbook.active
            market.title = module.MARKET_SHEET_NAME
            cost = workbook.create_sheet(module.COST_SHEET_NAME)
            for sheet in (market, cost):
                sheet["A2"] = date(2026, 7, 1)
                sheet["A3"] = date(2026, 7, 2)
            market["V2"].number_format = "0.00%"
            cost["N2"].number_format = "0.00"
            cost["B2"] = "运行"
            cost["B3"] = "已有"
            workbook.save(path)

            original_load = module.load_workbook_checked
            original_loads = []

            def counted_load(workbook_path, **kwargs):
                if Path(workbook_path).resolve() == path.resolve():
                    original_loads.append(kwargs)
                return original_load(workbook_path, **kwargs)

            with mock.patch.object(module, "load_workbook_checked", side_effect=counted_load):
                with module.workbook_write_session(path):
                    market_summary = module.write_day_ahead_market_values(
                        path, date(2026, 7, 2), list(range(12))
                    )
                    cost_summary = module.write_unit_cost_price_values(
                        path, date(2026, 7, 2), list(range(11))
                    )
                    operation_summary = module.copy_previous_day_unit_operation_mode(
                        path, date(2026, 7, 2)
                    )

            self.assertEqual(len(original_loads), 1)
            self.assertEqual(market_summary["range"], "V3:AG3")
            self.assertEqual(cost_summary["range"], "N3:X3")
            self.assertEqual(operation_summary["skipped"], 1)
            saved = load_workbook(path, data_only=False)
            try:
                self.assertEqual(saved[module.MARKET_SHEET_NAME]["V3"].value, 0)
                self.assertEqual(saved[module.MARKET_SHEET_NAME]["V3"].number_format, "0.00%")
                self.assertEqual(saved[module.COST_SHEET_NAME]["N3"].value, 0)
                self.assertEqual(saved[module.COST_SHEET_NAME]["N3"].number_format, "0.00")
                self.assertEqual(saved[module.COST_SHEET_NAME]["B3"].value, "已有")
                self.assertEqual(saved[module.COST_SHEET_NAME]["C3"].value, None)
            finally:
                saved.close()


if __name__ == "__main__":
    unittest.main()
