"""Metadata contracts; real cross-process AX acceptance is a separate probe."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from toolbox_shared.accessibility import describe


class AccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Tk's macOS AX backend belongs to the first/main interpreter, matching
        # the application's one-root lifecycle. Toplevels share that interpreter.
        cls.root = tk.Tk()
        cls.root.geometry("1000x700")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def tearDown(self):
        self.root.update_idletasks()
        for child in self.root.winfo_children():
            child.destroy()
        self.root.update()

    def page(self):
        from toolbox.pages import OnlineEnergyPage
        from toolbox.runtime import TaskRegistry, ToolPaths
        page = OnlineEnergyPage(self.root, ToolPaths(Path(__file__).resolve().parents[2]), TaskRegistry())
        page.pack(fill="both", expand=True)
        self.root.update()
        if not page.export_button._accessibility_element.supported:
            self.skipTest("Tk native accessibility commands are unavailable")
        return page

    def get(self, widget, field):
        return str(widget.tk.call(f"::tk::accessible::get_acc_{field}", widget))

    def test_old_runtime_does_not_install_variable_traces_or_events(self):
        variable = tk.StringVar(self.root, value="2026-11-10")
        entry = ttk.Entry(self.root, textvariable=variable)
        before = variable.trace_info()
        element = describe(entry, name="日期", role="Entry", variable=variable)
        if element.supported:
            self.skipTest("Requires the existing Tk 8.6 runtime")
        self.assertIsNone(element.pending)
        self.assertEqual(variable.trace_info(), before)
        variable.set("2026-11-11")
        self.root.update()
        self.assertEqual(entry.get(), "2026-11-11")

    def test_export_page_exposes_six_distinct_controls(self):
        page = self.page()
        controls = [page.date_range.start_entry, page.date_range.end_entry,
                    page.export_button, page.output_button, page.cancel_button,
                    page.status_label]
        names = [self.get(w, "name") for w in controls]
        self.assertEqual(names, ["导出开始日期", "导出结束日期", "开始导出",
                                 "打开输出目录", "取消任务", "导出任务状态"])
        self.assertEqual([self.get(w, "role") for w in controls],
                         ["Entry", "Entry", "Button", "Button", "Button", "Label"])

    def test_paste_and_programmatic_date_changes_update_metadata(self):
        page = self.page()
        page.date_range.start_entry.delete(0, "end")
        page.date_range.start_entry.insert(0, "2026-11-10")
        page.end_date.set("2026-11-12")
        self.root.update()
        self.assertEqual(self.get(page.date_range.start_entry, "value"), "2026-11-10")
        self.assertEqual(self.get(page.date_range.end_entry, "value"), "2026-11-12")

    def test_busy_state_and_status_follow_task_lifecycle(self):
        page = self.page()
        page.set_busy(True, "正在导出测试日期")
        self.root.update()
        self.assertEqual(self.get(page.export_button, "state"), "disabled")
        self.assertEqual(self.get(page.cancel_button, "state"), "normal")
        self.assertEqual(self.get(page.status_label, "value"), "正在导出测试日期")
        page.set_busy(False, "已完成")
        self.root.update()
        self.assertEqual(self.get(page.export_button, "state"), "normal")
        self.assertEqual(self.get(page.cancel_button, "state"), "disabled")
        self.assertEqual(self.get(page.status_label, "value"), "已完成")

    def test_remap_keeps_field_semantics(self):
        page = self.page()
        page.pack_forget()
        self.root.update()
        page.pack(fill="both", expand=True)
        self.root.update()
        self.assertEqual(self.get(page.date_range.start_entry, "name"), "导出开始日期")

    def test_button_command_preserves_existing_date_validation(self):
        page = self.page()
        page.start_date.set("bad-date")
        with patch("toolbox.pages.messagebox.showerror") as error, \
                patch.object(page, "run_process") as run:
            action = self.root.tk.call("::tk::accessible::get_acc_action", page.export_button)
            self.root.tk.call("eval", action)
        error.assert_called_once()
        run.assert_not_called()

    def test_destroy_cleans_up_variable_trace(self):
        variable = tk.StringVar(self.root, value="2026-11-10")
        entry = ttk.Entry(self.root, textvariable=variable)
        entry.pack()
        before = variable.trace_info()
        element = describe(entry, name="日期", role="Entry", variable=variable)
        if not element.supported:
            self.skipTest("Tk native accessibility commands are unavailable")
        self.root.update()
        self.assertEqual(len(variable.trace_info()), len(before) + 1)
        entry.destroy()
        variable.set("2026-11-11")
        self.root.update()
        self.assertEqual(variable.trace_info(), before)
