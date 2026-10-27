from __future__ import annotations

import gc
import sys
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from toolbox.page_support import DateInput, normalize_date_input, parse_date_input
from toolbox.runtime import TaskRegistry, ToolPaths, load_module


ROOT = Path(__file__).resolve().parents[2]
DATE_VARIANTS = (
    "2026-5-3", "2026-05-3", "2026-5-03", "2026-05-03",
    " 2026-5-3\n", "2026/5/3", "2026.05.3", "2026年5月3日", "20260503",
    "２０２６－５－３",
)


class DateParsingTests(unittest.TestCase):
    def test_common_formats_normalize_to_same_day(self):
        for value in DATE_VARIANTS:
            with self.subTest(value=value):
                self.assertEqual(parse_date_input(value), date(2026, 5, 3))
                self.assertEqual(normalize_date_input(value), "2026-05-03")

    def test_calendar_validation_and_unambiguous_year_first_order(self):
        self.assertEqual(parse_date_input("2024-2-29"), date(2024, 2, 29))
        for value in ("", "2026-2-29", "2026-4-31", "2026-0-1", "2026-13-1",
                      "2026-1-0", "26-5-3", "05/03/2026", "2026-5/3",
                      "2026-05-03 12:00:00", "2026-5-003"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_date_input(value)

    def test_backends_keep_their_existing_return_types_and_normalize(self):
        for index, (name, as_date) in enumerate((
            ("上网电量抓取/export_online_energy.py", False),
            ("集团每日上传/upload_daily_report.py", False),
            ("风力预测截图/wind_forecast_screenshot.py", False),
            ("现货价格更新/update_spot_prices.py", True),
            ("交易系统信息更新/update_transaction_info.py", True),
            ("市场表更新/update_market_table.py", False),
            ("每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py", False),
            ("每日生产经营情况汇报自动生成工具/scripts/export_online_energy.py", False),
        )):
            module = load_module(f"test_dates_backend_{index}", ROOT / name)
            for value in DATE_VARIANTS:
                with self.subTest(module=name, value=value):
                    expected = date(2026, 5, 3) if as_date else "2026-05-03"
                    self.assertEqual(module.validate_date(value), expected)
            with self.subTest(module=name), self.assertRaises(ValueError):
                module.validate_date("2026-2-29")

    def test_report_source_offsets_and_range_validation(self):
        from toolbox.pages import GuangdongPricePage, ReportPage

        for value in DATE_VARIANTS:
            self.assertEqual(ReportPage.source_dates(value),
                             (date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 1)))
        self.assertEqual(GuangdongPricePage.validate_date_range("2026-5-3", "2026年5月4日"),
                         (date(2026, 5, 3), date(2026, 5, 4)))
        with self.assertRaises(ValueError):
            GuangdongPricePage.validate_date_range("2026-5-4", "2026-5-3")

    def test_market_report_cli_accepts_unpadded_and_chinese_dates(self):
        from types import SimpleNamespace

        with patch.object(sys, "path", [str(ROOT / "南方分公司电力市场日报"), *sys.path]):
            module = load_module("test_date_market_report_cli", ROOT / "南方分公司电力市场日报/run_market_report.py")
        for value in DATE_VARIANTS:
            args = ["run_market_report.py", value, "--day-report", "d.xlsx",
                    "--previous-report", "d1.xlsx", "--d2-report", "d2.xlsx"]
            with self.subTest(value=value), patch.object(sys, "argv", args), \
                    patch.object(module, "validate_report_workbook", side_effect=lambda path: path), \
                    patch.object(module, "validate_day_ahead_comparison_workbook", side_effect=lambda path: path), \
                    patch.object(module, "infer_report_date", side_effect=(date(2026, 5, 3), date(2026, 5, 1))), \
                    patch.object(module, "infer_day_ahead_comparison_date", return_value=date(2026, 5, 2)), \
                    patch.object(module.AppConfig, "from_workspace", return_value=object()), \
                    patch.object(module, "run_pipeline", return_value=SimpleNamespace(preview_pdf_path="preview.pdf", print_pdf_path="print.pdf")) as run, \
                    patch("builtins.print"):
                self.assertEqual(module.main(), 0)
                self.assertEqual(run.call_args.args[1], date(2026, 5, 3))


class CalendarInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.geometry("1100x700+30+30")
            cls.root.update()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk 不可用：{exc}")

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        cls.root.destroy()

    def tearDown(self):
        self.doCleanups()
        gc.collect()

    def make_input(self, value="2026-5-3"):
        variable = tk.StringVar(master=self.root, value=value)
        widget = DateInput(self.root, textvariable=variable)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)
        return widget, variable

    def test_navigation_selection_and_popup_cleanup(self):
        widget, value = self.make_input("2023-12-31")
        widget.entry.event_generate("<ButtonRelease-1>", x=10, y=10)
        self.assertEqual(widget.displayed_month, date(2023, 12, 1))
        widget.change_month(1)
        self.assertEqual(widget.displayed_month, date(2024, 1, 1))
        widget.change_month(1)
        days = [child for child in widget.days_frame.winfo_children() if isinstance(child, ttk.Button)]
        self.assertEqual(len(days), 29)
        next(child for child in days if child.cget("text") == "29").invoke()
        self.assertEqual(value.get(), "2024-02-29")
        self.assertIsNone(widget.popup)
        self.assertIsNone(self.root.grab_current())
        widget.open_calendar()
        widget.change_month(-12)
        self.assertEqual(widget.displayed_month, date(2023, 2, 1))
        widget.change_month(12)
        widget._open_from_key()
        # Synthetic key events require focus even when another macOS app is active.
        widget.popup.focus_force()
        self.root.update()
        widget.popup.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(widget.popup)

    def test_empty_optional_date_and_today_button(self):
        widget, value = self.make_input("")
        widget.open_calendar()
        self.assertEqual(value.get(), "")
        self.assertEqual(widget.displayed_month, date.today().replace(day=1))
        outer = widget.popup.winfo_children()[0]
        next(child for child in outer.winfo_children() if isinstance(child, ttk.Button)).invoke()
        self.assertEqual(value.get(), date.today().isoformat())
        self.assertIsNone(widget.popup)

    def test_manual_input_preserves_invalid_values_and_normalizes_valid_values(self):
        widget, value = self.make_input()
        for text in DATE_VARIANTS:
            value.set(text)
            widget.entry.event_generate("<FocusOut>")
            self.assertEqual(value.get(), "2026-05-03")
        value.set("2026-2-29")
        widget.entry.event_generate("<FocusOut>")
        self.assertEqual(value.get(), "2026-2-29")
        widget.open_calendar()
        self.assertEqual(widget.displayed_month, date.today().replace(day=1))

    def test_outside_click_destroy_and_date_limits(self):
        from types import SimpleNamespace

        widget, value = self.make_input()
        widget.open_calendar()
        widget._outside_click(SimpleNamespace(x_root=-1, y_root=-1))
        self.assertIsNone(widget.popup)
        for text in ("0001-1-1", "9999-12-31"):
            value.set(text)
            widget.open_calendar()
            widget.change_month(-12 if text.startswith("0001") else 12)
            self.assertEqual(widget.displayed_month.year, int(text[:4]))
            widget.close_calendar()
            self.assertIsNone(widget.popup)
        widget.open_calendar()
        widget.destroy()
        self.assertIsNone(self.root.grab_current())

    def test_click_inside_entry_opens_calendar_without_an_external_button(self):
        widget, value = self.make_input()
        self.assertFalse(any(isinstance(child, ttk.Button) for child in widget.winfo_children()))
        previous_binding = self.root.bind("<ButtonPress-1>")
        widget.entry.event_generate("<ButtonPress-1>", x=10, y=10)
        widget.entry.event_generate("<ButtonRelease-1>", x=10, y=10)
        self.root.update()
        popup = widget.popup
        self.assertIsNotNone(popup)
        self.assertTrue(popup.overrideredirect())
        original_height = popup.winfo_height()
        widget.change_month(-3)
        self.root.update_idletasks()
        self.assertEqual(popup.winfo_height(), original_height)
        widget.entry.event_generate("<ButtonPress-1>", x=10, y=10)
        widget.entry.event_generate("<ButtonRelease-1>", x=10, y=10)
        self.assertIs(widget.popup, popup)
        widget.entry.delete(0, "end")
        widget.entry.insert(0, "2026-5-4")
        self.assertEqual(value.get(), "2026-5-4")
        self.root.event_generate("<ButtonPress-1>", x=1000, y=650)
        self.assertIsNone(widget.popup)
        self.assertEqual(self.root.bind("<ButtonPress-1>"), previous_binding)

    def test_every_date_page_uses_calendar_and_commands_receive_iso_date(self):
        from toolbox.pages import (
            GuangdongPricePage, MarketTableUpdatePage, OnlineEnergyPage, ReportPage,
            MarketReportPage, SpotPriceUpdatePage, TransactionInfoUpdatePage, WindForecastPage,
        )

        for page_type, expected_count in (
            (OnlineEnergyPage, 1), (ReportPage, 1), (MarketTableUpdatePage, 1),
            (GuangdongPricePage, 1), (MarketReportPage, 1), (SpotPriceUpdatePage, 2),
            (TransactionInfoUpdatePage, 2), (WindForecastPage, 1),
        ):
            with self.subTest(page=page_type.__name__):
                page = page_type(self.root, ToolPaths(ROOT), TaskRegistry())
                try:
                    def count_calendars(widget):
                        return sum(isinstance(child, DateInput) + count_calendars(child)
                                   for child in widget.winfo_children())
                    self.assertEqual(count_calendars(page), expected_count)
                    if page_type in (OnlineEnergyPage, WindForecastPage):
                        if page_type is OnlineEnergyPage:
                            form = next(child for child in page.content.winfo_children()
                                        if isinstance(child, ttk.LabelFrame))
                            labels = [child.cget("text") for child in form.winfo_children()
                                      if isinstance(child, ttk.Label)]
                            self.assertIn("导出日期", labels)
                        page.run_date.set("2026-5-3")
                        with patch.object(page, "run_process") as run:
                            (page.start_export if page_type is OnlineEnergyPage else page.start_generate)()
                        self.assertEqual(run.call_args.args[0][-1], "2026-05-03")
                finally:
                    page.destroy()

    def test_standalone_report_gui_also_uses_calendar(self):
        module = load_module("test_date_standalone_gui", ROOT / "每日生产经营情况汇报自动生成工具/scripts/report_gui.py")
        app = module.ReportTool()
        try:
            def find_calendar(widget):
                for child in widget.winfo_children():
                    if isinstance(child, DateInput):
                        return child
                    result = find_calendar(child)
                    if result is not None:
                        return result
            widget = find_calendar(app)
            self.assertIsNotNone(widget)
            widget.select_date(date(2026, 5, 3))
            self.assertEqual(app.report_date.get(), "2026-05-03")
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
