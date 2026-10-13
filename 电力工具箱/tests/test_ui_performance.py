from __future__ import annotations

import gc
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

from toolbox.runtime import TaskRegistry
from toolbox.tasks import TaskSnapshot, TaskState
from toolbox.ui import DashboardPage, TaskCenter


class UiRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.registry = TaskRegistry()

    def tearDown(self) -> None:
        self.root.destroy()
        gc.collect()

    def test_unchanged_tasks_do_not_rebuild_list_and_keep_selection(self) -> None:
        task = TaskSnapshot("task", "导出电量", TaskState.RUNNING)
        self.registry._snapshots[task.task_id] = task
        center = TaskCenter(self.root, self.registry)
        center.listbox.selection_set(0)
        with patch.object(center.listbox, "delete", wraps=center.listbox.delete) as delete:
            for _ in range(5):
                center._refresh()
        delete.assert_not_called()
        self.assertEqual((0,), center.listbox.curselection())
        self.registry._snapshots[task.task_id] = task.transition(TaskState.SUCCEEDED)
        center._refresh()
        self.assertIn("完成", center.listbox.get(0))
        self.assertEqual("disabled", str(center.cancel_button.cget("state")))

    def test_cancellation_permission_change_still_updates_button(self) -> None:
        task = TaskSnapshot("task", "写入数据", TaskState.RUNNING)
        self.registry._snapshots[task.task_id] = task
        center = TaskCenter(self.root, self.registry)
        center.listbox.selection_set(0)
        center._sync_cancel_state()
        self.assertEqual("normal", str(center.cancel_button.cget("state")))
        self.registry._snapshots[task.task_id] = task.transition(TaskState.RUNNING, cancellable=False)
        center._refresh()
        self.assertEqual("disabled", str(center.cancel_button.cget("state")))

    def test_dashboard_only_writes_metrics_when_values_change(self) -> None:
        dashboard = DashboardPage(self.root, self.registry, lambda _name: None)
        writes = []
        for label, variable in dashboard.metric_vars.items():
            variable.trace_add("write", lambda *_args, name=label: writes.append(name))
        for _ in range(5):
            dashboard._refresh()
        self.assertEqual([], writes)
        self.registry._snapshots["task"] = TaskSnapshot("task", "导出", TaskState.RUNNING)
        dashboard._refresh()
        self.assertEqual(["运行中"], writes)
        self.assertEqual("1", dashboard.metric_vars["运行中"].get())

    def test_description_reflow_only_happens_when_width_changes(self) -> None:
        from toolbox.widgets import ToolPage

        page = ToolPage(self.root, registry=self.registry, description="页面说明")
        with patch.object(page.description_label, "configure", wraps=page.description_label.configure) as configure:
            page._resize_description(SimpleNamespace(width=608))
            page._resize_description(SimpleNamespace(width=608))
            configure.assert_called_once_with(wraplength=600)
            page._resize_description(SimpleNamespace(width=508))
            self.assertEqual(2, configure.call_count)
        self.assertEqual(500, int(page.description_label.cget("wraplength")))

        label = ttk.Label(self.root, wraplength=250)
        with patch.object(label, "configure", wraps=label.configure) as configure:
            DashboardPage._resize_detail(SimpleNamespace(width=292), label)
            configure.assert_not_called()
            DashboardPage._resize_detail(SimpleNamespace(width=242), label)
            configure.assert_called_once_with(wraplength=200)

    def test_hidden_page_keeps_inputs_and_finishes_background_task(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp
        from toolbox.widgets import ToolPage

        self.root.destroy()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            app = ToolboxApp(
                workspace=Path(folder),
                page_factories={name: (lambda parent, _paths, registry: ToolPage(parent, registry=registry)) for name in PAGE_NAMES},
                lazy_pages=True,
            )
            app.withdraw()
            self.root = app
            first, second = PAGE_NAMES[:2]
            app.show_page(first)
            page = app.pages[first]
            user_input = tk.StringVar(page, "2026-10-10")
            page.run_in_thread(lambda: release.wait(1), status="后台测试")
            app.show_page(second)
            self.assertEqual("", page.winfo_manager())
            self.assertEqual("grid", app.pages[second].winfo_manager())
            release.set()
            deadline = time.monotonic() + 3
            while page.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.01)
            self.assertFalse(page.busy)
            self.assertEqual("已完成", page.status_var.get())
            app.show_page(first)
            self.assertIs(page, app.pages[first])
            self.assertEqual("grid", page.winfo_manager())
            self.assertEqual("2026-10-10", user_input.get())
            self.assertEqual("", app.pages[second].winfo_manager())

    def test_wps_import_does_not_block_navigation_and_builds_widgets_on_main_thread(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp
        from toolbox.pages import WpsWriterPage

        self.root.destroy()
        release = threading.Event()
        entered = threading.Event()
        build_threads = []

        def load_writer(*_args):
            entered.set()
            release.wait(3)
            return SimpleNamespace(WpsWriterFrame=build_frame)

        def build_frame(parent, *, task_registry):
            build_threads.append(threading.current_thread())
            frame = ttk.Frame(parent)
            frame.task_registry = task_registry
            return frame

        factories = {name: (lambda parent, _paths, _registry: ttk.Frame(parent)) for name in PAGE_NAMES}
        factories["WPS写入工具"] = WpsWriterPage
        app = ToolboxApp(page_factories=factories, lazy_pages=True)
        self.root = app
        with patch("toolbox.pages.load_module", side_effect=load_writer):
            try:
                app.show_page("WPS写入工具")
                page = app.pages["WPS写入工具"]
                self.assertFalse(entered.is_set())
                app.update_idletasks()
                self.assertTrue(page.loading_label.winfo_ismapped())
                self.assertFalse(entered.is_set())
                deadline = time.monotonic() + 1
                while not entered.is_set() and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.005)
                self.assertTrue(entered.is_set())
                self.assertFalse(hasattr(page, "writer_frame"))
                # Process a real Tk callback while the dependency import is held.
                app.after(0, lambda: app.show_page("早报生成"))
                app.update()
                self.assertEqual("早报生成", app.current_page)
                self.assertEqual("", page.winfo_manager())
                self.assertEqual([], build_threads)
            finally:
                release.set()
                page._writer_loader.join(2)
            page._finish_writer_load()
        self.assertEqual([threading.main_thread()], build_threads)
        self.assertIs(page.writer_frame.task_registry, app.registry)
        self.assertEqual("", page.winfo_manager())
        app.show_page("WPS写入工具")
        self.assertIs(page, app.pages["WPS写入工具"])
        self.assertEqual("grid", page.winfo_manager())

    def test_startup_stays_on_dashboard_and_creates_wps_only_on_first_visit(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        created = []

        def factory(parent, _paths, _registry):
            created.append(threading.current_thread())
            return ttk.Frame(parent)

        with patch("toolbox.app.catalog_factories", return_value={name: factory for name in PAGE_NAMES}):
            app = ToolboxApp()
        app.withdraw()
        self.root = app
        # Pump real timers beyond the former 300 ms automatic warmup.
        app.after(450, app.quit)
        app.mainloop()
        self.assertEqual({}, app.pages)
        self.assertEqual([], created)
        self.assertEqual("今日概览", app.current_page)
        self.assertEqual("grid", app.dashboard.winfo_manager())
        app.show_page("WPS写入工具")
        page = app.pages["WPS写入工具"]
        app.show_page("今日概览")
        self.assertEqual("", page.winfo_manager())
        app.show_page("WPS写入工具")
        self.assertIs(page, app.pages["WPS写入工具"])
        self.assertEqual([threading.main_thread()], created)

    def test_wps_import_failure_keeps_recovery_actions_available(self) -> None:
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import ToolPaths

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        with patch("toolbox.pages.load_module", side_effect=ImportError("加载测试失败")):
            page = WpsWriterPage(self.root, paths, self.registry)
            page._start_writer_load()
            page._writer_loader.join(2)
            page._finish_writer_load()
        self.assertFalse(hasattr(page, "writer_frame"))
        self.assertFalse(page.loading_label.winfo_exists())
        self.assertEqual("grid", page.actions.winfo_manager())
        self.assertEqual(2, len(page.actions.winfo_children()))

    def test_destroying_wps_page_during_import_cancels_ui_callback(self) -> None:
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import ToolPaths

        release = threading.Event()
        paths = ToolPaths(Path(__file__).resolve().parents[2])

        def load_writer(*_args):
            release.wait(3)
            return SimpleNamespace(WpsWriterFrame=lambda *_args, **_kwargs: self.fail("Destroyed page rebuilt"))

        with patch("toolbox.pages.load_module", side_effect=load_writer):
            page = WpsWriterPage(self.root, paths, self.registry)
            page._start_writer_load()
            callback = page._writer_load_after_id
            try:
                page.destroy()
            finally:
                release.set()
                page._writer_loader.join(2)
        self.assertIsNone(page._writer_load_after_id)
        self.assertNotIn(callback, self.root.tk.call("after", "info"))
        self.root.update()

    def test_destroying_wps_page_before_first_paint_never_starts_import(self) -> None:
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import ToolPaths

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        with patch("toolbox.pages.load_module") as load:
            page = WpsWriterPage(self.root, paths, self.registry)
            callback = page._writer_load_after_id
            self.assertIsNone(page._writer_loader.ident)
            page.destroy()
            self.assertIsNone(page._writer_load_after_id)
            self.assertNotIn(callback, self.root.tk.call("after", "info"))
            self.root.update()
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
