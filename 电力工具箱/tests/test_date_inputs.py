from __future__ import annotations

import gc
import sys
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from toolbox.page_support import DateInput, DateRangeInput, normalize_date_input, parse_date_input
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
            GuangdongPricePage, MarketTableUpdatePage, OnlineEnergyPage, PowerBenefitUpdatePage,
            ReportPage, MarketReportPage, SpotPriceUpdatePage, TransactionInfoUpdatePage,
            WindForecastPage,
        )

        # 单日页面一张日历；日期范围页面一个双月历控件，不再有两个独立日历框。
        for page_type, single_days, date_ranges in (
            (OnlineEnergyPage, 0, 1), (ReportPage, 1, 0), (MarketTableUpdatePage, 1, 0),
            (GuangdongPricePage, 1, 0), (MarketReportPage, 0, 0), (SpotPriceUpdatePage, 0, 1),
            (TransactionInfoUpdatePage, 0, 1), (PowerBenefitUpdatePage, 0, 1),
            (WindForecastPage, 1, 0),
        ):
            with self.subTest(page=page_type.__name__):
                page = page_type(self.root, ToolPaths(ROOT), TaskRegistry())
                try:
                    def find_calendars(widget, kind):
                        return sum(isinstance(child, kind) + find_calendars(child, kind)
                                   for child in widget.winfo_children())
                    self.assertEqual(find_calendars(page, DateInput), single_days)
                    self.assertEqual(find_calendars(page, DateRangeInput), date_ranges)
                    if page_type is OnlineEnergyPage:
                        form = next(child for child in page.content.winfo_children()
                                    if isinstance(child, ttk.LabelFrame))
                        labels = [child.cget("text") for child in form.winfo_children()
                                  if isinstance(child, ttk.Label)]
                        self.assertIn("导出日期", labels)
                        page.start_date.set("2026-5-3")
                        page.end_date.set("2026-5-6")
                        with patch.object(page, "run_process") as run:
                            page.start_export()
                        command = run.call_args.args[0]
                        self.assertEqual(command[-4:], ["--start-date", "2026-05-03",
                                                        "--end-date", "2026-05-06"])
                    elif page_type is WindForecastPage:
                        page.run_date.set("2026-5-3")
                        with patch.object(page, "run_process") as run:
                            page.start_generate()
                        self.assertEqual(run.call_args.args[0][-1], "2026-05-03")
                    elif page_type is MarketReportPage:
                        # 报告日期只读展示，由 d 日收益表自动识别。
                        form = next(child for child in page.content.winfo_children()
                                    if isinstance(child, ttk.LabelFrame))

                        def descendants(widget):
                            for child in widget.winfo_children():
                                yield child
                                yield from descendants(child)

                        labels = [child.cget("text") for child in descendants(form)
                                  if isinstance(child, ttk.Label)]
                        self.assertIn("报告日期", labels)
                        self.assertIn("d-1 日收益表（必选）", labels)
                        date_entries = [child for child in descendants(form) if isinstance(child, ttk.Entry)
                                        and str(child.cget("textvariable")) == str(page.report_date)]
                        self.assertEqual([str(entry.cget("state")) for entry in date_entries], ["readonly"])
                finally:
                    page.destroy()

    def test_date_range_input_links_and_independent_month_paging(self):
        start = tk.StringVar(master=self.root, value="2026-11-05")
        end = tk.StringVar(master=self.root, value="2026-11-08")
        widget = DateRangeInput(self.root, start_variable=start, end_variable=end)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)

        widget.open_calendar()
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2026, 11, 1), date(2026, 12, 1)))
        widget.change_month("left", 1)
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2026, 12, 1), date(2027, 1, 1)))
        widget.change_month("right", -1)
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2026, 11, 1), date(2026, 12, 1)))

        widget.set_linked(False)
        widget.change_month("left", 2)
        self.assertEqual(widget.left_month, date(2027, 1, 1))
        self.assertEqual(widget.right_month, date(2026, 12, 1))
        widget.change_month("right", 3)
        self.assertEqual(widget.left_month, date(2027, 1, 1))
        self.assertEqual(widget.right_month, date(2027, 3, 1))
        self.assertIn("独立翻页", widget.hint_label.cget("text"))

    def test_date_range_input_picks_start_then_end_and_keeps_range_valid(self):
        start = tk.StringVar(master=self.root, value="2026-11-05")
        end = tk.StringVar(master=self.root, value="2026-11-08")
        widget = DateRangeInput(self.root, start_variable=start, end_variable=end)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)

        widget.open_calendar("start")
        widget.pick(date(2026, 11, 10))
        self.assertEqual((start.get(), end.get()), ("2026-11-10", "2026-11-10"))
        self.assertIsNotNone(widget.popup)
        self.assertEqual(widget.stage, "end")
        self.assertIn("共 1 天", widget.hint_label.cget("text"))

        widget.pick(date(2026, 11, 12))
        self.assertEqual((start.get(), end.get()), ("2026-11-10", "2026-11-12"))
        self.assertIsNone(widget.popup)

        widget.open_calendar("start")
        widget.pick(date(2026, 11, 10))
        widget.pick(date(2026, 11, 1))
        self.assertEqual((start.get(), end.get()), ("2026-11-01", "2026-11-01"))
        self.assertIsNone(widget.popup)
        widget.open_calendar()
        widget.select_today()
        self.assertEqual(start.get(), date.today().isoformat())
        self.assertEqual(end.get(), date.today().isoformat())
        self.assertIsNone(widget.popup)
        self.assertIsNone(self.root.grab_current())

    def make_range(self, start="2026-11-05", end="2026-11-08", **kwargs):
        start_variable = tk.StringVar(master=self.root, value=start)
        end_variable = tk.StringVar(master=self.root, value=end)
        widget = DateRangeInput(self.root, start_variable=start_variable,
                                end_variable=end_variable, **kwargs)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)
        return widget, start_variable, end_variable

    @staticmethod
    def day_buttons(frame, text):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "TButton" and child.cget("text") == text:
                    found.append(child)
                walk(child)

        walk(frame)
        return found

    @staticmethod
    def adjacent_labels(frame, text):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "TLabel" and child.cget("text") == text:
                    found.append(child)
                walk(child)

        walk(frame)
        return found

    def test_same_month_range_completes_on_one_calendar_and_closes(self):
        # 起止都在 11 月时右侧月历只能显示 12 月，旧实现里结束日期无处可选、弹层永不收起。
        widget, start, end = self.make_range()
        widget.open_calendar("start")
        self.root.update()
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2026, 11, 1), date(2026, 12, 1)))

        next(iter(self.day_buttons(widget.left_days_frame, "10"))).invoke()
        self.root.update()
        self.assertEqual((start.get(), end.get()), ("2026-11-10", "2026-11-10"))
        self.assertEqual(widget.stage, "end")
        self.assertIsNotNone(widget.popup)
        self.assertIn("选择结束日期", widget.status_label.cget("text"))

        next(iter(self.day_buttons(widget.left_days_frame, "12"))).invoke()
        self.root.update()
        self.assertEqual((start.get(), end.get()), ("2026-11-10", "2026-11-12"))
        self.assertIsNone(widget.popup)

    def test_end_field_opens_in_the_second_step_and_closes_on_one_pick(self):
        widget, start, end = self.make_range()
        widget._open_from_click("end")
        self.root.update()
        self.assertEqual(widget.stage, "end")
        self.assertIn("2026-11-05", widget.status_label.cget("text"))
        next(iter(self.day_buttons(widget.left_days_frame, "20"))).invoke()
        self.root.update()
        self.assertEqual((start.get(), end.get()), ("2026-11-05", "2026-11-20"))
        self.assertIsNone(widget.popup)

    def test_clicking_the_other_field_switches_the_step_without_rebuilding(self):
        widget, _start, _end = self.make_range()
        widget._open_from_click("start")
        popup = widget.popup
        widget._open_from_click("end")
        self.root.update()
        self.assertIs(widget.popup, popup)
        self.assertEqual(widget.stage, "end")
        widget._open_from_click("start")
        self.root.update()
        self.assertEqual(widget.stage, "start")
        self.assertIs(widget.popup, popup)

    def test_adjacent_month_days_are_clickable(self):
        widget, start, end = self.make_range("2026-11-28", "2026-12-02")
        widget.open_calendar("start")
        self.root.update()
        adjacent = self.adjacent_labels(widget.left_days_frame, "2")
        self.assertEqual(len(adjacent), 1)
        adjacent[0].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(start.get(), "2026-12-02")
        self.assertIsNotNone(widget.popup)
        next(iter(self.day_buttons(widget.right_days_frame, "5"))).invoke()
        self.root.update()
        self.assertEqual((start.get(), end.get()), ("2026-12-02", "2026-12-05"))
        self.assertIsNone(widget.popup)

    def test_confirm_and_today_buttons_always_close_the_popup(self):
        widget, start, end = self.make_range()
        widget.open_calendar("start")
        widget.pick(date(2026, 11, 10))
        self.assertIsNotNone(widget.popup)
        widget.confirm()
        self.assertIsNone(widget.popup)
        self.assertEqual((start.get(), end.get()), ("2026-11-10", "2026-11-10"))

        widget.open_calendar("start")
        widget.select_today()
        self.assertIsNone(widget.popup)
        self.assertEqual((start.get(), end.get()),
                         (date.today().isoformat(), date.today().isoformat()))

    def test_return_and_escape_close_without_touching_the_values(self):
        widget, start, end = self.make_range()
        widget.open_calendar("start")
        self.assertTrue(widget.start_entry.bind("<Return>"))
        self.assertTrue(widget.end_entry.bind("<Return>"))
        # 合成按键事件只送给当前焦点窗口；其它应用抢走焦点时无法验证。
        widget.start_entry.focus_force()
        self.root.update()
        if self.root.focus_get() is widget.start_entry:
            widget.start_entry.event_generate("<Return>")
            self.root.update()
            self.assertIsNone(widget.popup)
        else:
            widget.close_calendar()
        widget.open_calendar("start")
        widget.end_entry.focus_force()
        self.root.update()
        if self.root.focus_get() is widget.end_entry:
            widget.end_entry.event_generate("<Escape>")
            self.root.update()
        else:
            widget.close_calendar()
        self.assertIsNone(widget.popup)
        self.assertEqual((start.get(), end.get()), ("2026-11-05", "2026-11-08"))

    def test_optional_start_stays_empty_when_only_the_end_is_picked(self):
        widget, start, end = self.make_range("", "2026-11-04", start_optional=True)
        widget.open_calendar("end")
        self.assertEqual(widget.stage, "end")
        widget.pick(date(2026, 11, 8))
        self.assertEqual((start.get(), end.get()), ("", "2026-11-08"))
        self.assertIsNone(widget.popup)

    def test_invalid_typed_dates_do_not_block_the_calendar(self):
        widget, start, end = self.make_range("2026-2-29", "abc")
        widget.open_calendar("start")
        self.assertEqual(widget.left_month, date.today().replace(day=1))
        self.assertEqual(widget.stage, "start")
        widget.pick(date(2026, 11, 4))
        widget.pick(date(2026, 11, 6))
        self.assertEqual((start.get(), end.get()), ("2026-11-04", "2026-11-06"))
        self.assertIsNone(widget.popup)

    def test_paging_and_link_toggle_keep_the_selection_completable(self):
        widget, start, end = self.make_range()
        widget.open_calendar("start")
        widget.change_month("left", 2)
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2027, 1, 1), date(2027, 2, 1)))
        widget.pick(date(2027, 1, 15))
        self.assertIsNotNone(widget.popup)
        widget.set_linked(False)
        widget.change_month("right", 2)
        widget.pick(date(2027, 4, 3))
        self.assertEqual((start.get(), end.get()), ("2027-01-15", "2027-04-03"))
        self.assertIsNone(widget.popup)

    def test_reopening_after_a_same_month_pick_shows_the_new_range(self):
        widget, _start, _end = self.make_range()
        widget.open_calendar("start")
        widget.pick(date(2026, 11, 3))
        widget.pick(date(2026, 11, 9))
        widget.open_calendar("end")
        self.assertEqual((widget.left_month, widget.right_month),
                         (date(2026, 11, 1), date(2026, 12, 1)))
        self.assertEqual(widget.stage, "end")
        self.assertIn("2026-11-03", widget.status_label.cget("text"))

    def test_date_range_input_closes_on_escape_outside_click_and_destroy(self):
        from types import SimpleNamespace

        widget = DateRangeInput(
            self.root,
            start_variable=tk.StringVar(master=self.root, value="2026-5-3"),
            end_variable=tk.StringVar(master=self.root, value="2026-5-9"),
        )
        widget.pack()
        self.root.update()

        widget.open_calendar()
        widget._outside_click(SimpleNamespace(x_root=-1, y_root=-1))
        self.assertIsNone(widget.popup)

        widget.open_calendar()
        # 弹层和两个日期框都绑了 Escape：点输入框打开时焦点仍在输入框，真实按键走这条路径。
        self.assertTrue(widget.popup.bind("<Escape>"))
        self.assertTrue(widget.start_entry.bind("<Escape>"))
        self.assertTrue(widget.end_entry.bind("<Escape>"))
        widget.popup.focus_force()
        self.root.update()
        if self.root.focus_get() is not widget.popup:
            # 合成按键事件只会送给当前焦点窗口；其它应用抢走焦点时无法验证。
            self.skipTest("系统焦点不在弹层上，跳过合成 Escape 验证")
        widget.popup.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(widget.popup)

        widget.open_calendar()
        widget.destroy()
        self.assertIsNone(widget.popup)
        self.assertIsNone(self.root.grab_current())

    def test_date_range_input_manual_entry_normalizes_each_field(self):
        start = tk.StringVar(master=self.root, value="")
        end = tk.StringVar(master=self.root, value="")
        widget = DateRangeInput(self.root, start_variable=start, end_variable=end)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)

        start.set("2026年5月3日")
        widget.start_entry.event_generate("<FocusOut>")
        end.set("2026.5.9")
        widget.end_entry.event_generate("<FocusOut>")
        self.assertEqual((start.get(), end.get()), ("2026-05-03", "2026-05-09"))
        start.set("2026-2-29")
        widget.start_entry.event_generate("<FocusOut>")
        self.assertEqual(start.get(), "2026-2-29")
        widget.open_calendar()
        self.assertEqual(widget.left_month, date(2026, 5, 1))
        self.assertEqual(widget.right_month, date(2026, 6, 1))

    def test_date_range_fields_sit_horizontally_on_one_row(self):
        widget = DateRangeInput(
            self.root,
            start_variable=tk.StringVar(master=self.root, value="2026-11-03"),
            end_variable=tk.StringVar(master=self.root, value="2026-11-04"),
        )
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)
        self.assertEqual(int(widget.start_entry.grid_info()["row"]), 0)
        self.assertEqual(int(widget.end_entry.grid_info()["row"]), 0)
        self.assertLess(widget.start_entry.winfo_x(), widget.end_entry.winfo_x())

    def test_optional_start_hint_does_not_ask_for_a_start_date(self):
        widget = DateRangeInput(
            self.root,
            start_variable=tk.StringVar(master=self.root, value=""),
            end_variable=tk.StringVar(master=self.root, value="2026-11-04"),
            start_optional=True,
        )
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)
        widget.open_calendar()
        self.assertIn("开始日期可留空", widget.hint_label.cget("text"))

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
