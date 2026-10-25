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
            app.request_page(second)
            app.request_page(first)
            self.assertTrue(page.busy)
            app.request_page(second)
            self._settle_navigation(app)
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

    def test_repeated_navigation_does_not_keep_idle_page_pollers(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp
        from toolbox.widgets import ToolPage

        self.root.destroy()
        app = ToolboxApp(
            page_factories={name: (lambda parent, _paths, registry: ToolPage(parent, registry=registry))
                            for name in PAGE_NAMES},
            lazy_pages=True,
        )
        self.root = app
        app.withdraw()
        for _ in range(20):
            for name in PAGE_NAMES:
                app.show_page(name)
                app.update()
        app.after(100, app.quit)
        app.mainloop()
        self.assertEqual(len(PAGE_NAMES), len(app.pages))
        self.assertTrue(all(page._poll_after_id is None for page in app.pages.values()))
        # Starting a task on an idle, hidden page must restart its one poller.
        page = app.pages[PAGE_NAMES[0]]
        completed = []
        page.run_in_thread(lambda: 42, status="后台测试", on_success=completed.append)
        deadline = time.monotonic() + 3
        while page.busy and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        self.assertEqual([42], completed)
        self.assertIsNone(page._poll_after_id)
        self.assertEqual("", page.winfo_manager())

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
        self.assertEqual([], build_threads)
        self.assertFalse(hasattr(page, "writer_frame"))
        self.assertIsNone(page._writer_load_after_id)
        self.assertEqual("", page.winfo_manager())
        app.show_page("WPS写入工具")
        deadline = time.monotonic() + 1
        while not hasattr(page, "writer_frame") and time.monotonic() < deadline:
            app.update()
            time.sleep(.005)
        self.assertEqual([threading.main_thread()], build_threads)
        self.assertIs(page.writer_frame.task_registry, app.registry)
        self.assertIs(page, app.pages["WPS写入工具"])
        self.assertEqual("grid", page.winfo_manager())

    def _navigation_app(self):
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
            app = ToolboxApp(page_factories={name: (lambda parent, _paths, _registry: ttk.Frame(parent))
                                            for name in PAGE_NAMES}, lazy_pages=True)
        self.root = app
        return app, list(app.nav_buttons)

    def _settle_navigation(self, app):
        deadline = time.monotonic() + 2
        while app._pending_page is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(.005)
        app.update()
        self.assertIsNone(app._pending_page)

    def test_switching_cancels_obsolete_cold_page_before_its_factory_runs(self):
        app, names = self._navigation_app()
        with patch.dict(app._factories, {names[1]: lambda *_: self.fail("Cancelled module was built")}):
            app.nav_buttons[names[1]].invoke()
            app.update_idletasks()
            self.assertTrue(app._loading_frame.winfo_ismapped())
            for _ in range(50):
                app.nav_buttons[names[1]].invoke()
                app.nav_buttons[names[2]].invoke()
            self._settle_navigation(app)
        self.assertEqual(names[2], app.current_page)
        self.assertNotIn(names[1], app.pages)
        self.assertEqual({names[0], names[2]}, set(app.pages))

    def test_cached_page_returns_immediately_and_cancels_pending_build(self):
        app, names = self._navigation_app()
        original = app.pages[names[0]]
        value = tk.StringVar(original, "2026-10-25")
        app.nav_buttons[names[1]].invoke()
        callback = app._page_load_after_id
        app.nav_buttons[names[0]].invoke()
        self.assertIs(original, app._visible_page)
        self.assertIsNone(app._page_load_after_id)
        self.assertNotIn(callback, app.tk.call("after", "info"))
        self.assertEqual("2026-10-25", value.get())
        self.assertNotIn(names[1], app.pages)

    def test_repeated_click_on_same_loading_page_keeps_one_preparation(self):
        app, names = self._navigation_app()
        app.nav_buttons[names[1]].invoke()
        callback = app._page_load_after_id
        for _ in range(50):
            app.nav_buttons[names[1]].invoke()
            self.assertEqual(callback, app._page_load_after_id)
        self._settle_navigation(app)
        self.assertEqual(names[1], app.current_page)
        self.assertEqual({names[0], names[1]}, set(app.pages))

    def test_cold_load_does_not_steal_focus_after_reentrant_navigation(self):
        app, names = self._navigation_app()

        def slow_factory(parent, _paths, _registry):
            app.after(0, lambda: app.request_page(names[0]))
            app.update()
            return ttk.Frame(parent)

        app._factories[names[1]] = slow_factory
        app.request_page(names[1])
        self._settle_navigation(app)
        self.assertEqual(names[0], app.current_page)
        self.assertIs(app.pages[names[0]], app._visible_page)
        self.assertEqual("", app.pages[names[1]].winfo_manager())

    def test_failed_cold_constructor_is_cleaned_up_and_can_be_retried(self):
        app, names = self._navigation_app()

        def broken(parent, *_args):
            ttk.Frame(parent)
            raise RuntimeError("页面创建测试失败")

        app._factories[names[1]] = broken
        with patch.object(app, "report_callback_exception") as error:
            app.request_page(names[1])
            self._settle_navigation(app)
        error.assert_called_once()
        self.assertNotIn(names[1], app.pages)
        self.assertEqual({app.dashboard, app.pages[names[0]], app._loading_frame},
                         set(app.page_host.winfo_children()))
        app._factories[names[1]] = lambda parent, *_: ttk.Frame(parent)
        app.request_page(names[1])
        self._settle_navigation(app)
        self.assertEqual(names[1], app.current_page)

    def test_close_cancels_pending_page_constructor(self):
        app, names = self._navigation_app()
        app.request_page(names[1])
        callback = app._page_load_after_id
        app.destroy()
        self.assertNotIn(callback, app.tk.call("after", "info"))
        self.assertNotIn(names[1], app.pages)
        self.root = tk.Tk()
        self.root.withdraw()

    def test_failed_obsolete_constructor_does_not_destroy_or_relabel_new_page(self):
        app, names = self._navigation_app()

        def broken(parent, *_args):
            ttk.Frame(parent)
            app.show_page(names[2])
            raise RuntimeError("过时页面创建失败")

        app._factories[names[1]] = broken
        with patch.object(app, "report_callback_exception") as error:
            app.request_page(names[1])
            self._settle_navigation(app)
        error.assert_called_once()
        self.assertEqual(names[2], app.current_page)
        self.assertTrue(app.pages[names[2]].winfo_exists())
        self.assertEqual(f"当前功能：{names[2]}", app.status_var.get())

    def test_leaving_wps_before_import_pauses_loading_and_return_imports_only_once(self):
        from toolbox.pages import WpsWriterPage

        app, names = self._navigation_app()
        app._factories["WPS写入工具"] = WpsWriterPage
        builds = []

        def frame(parent, **_kwargs):
            builds.append(1)
            return ttk.Frame(parent)

        with patch("toolbox.pages.load_module", return_value=SimpleNamespace(WpsWriterFrame=frame)) as load:
            app.show_page("WPS写入工具")
            page = app.pages["WPS写入工具"]
            app.request_page(names[0])
            app.after(100, app.quit)
            app.mainloop()
            load.assert_not_called()
            self.assertIsNone(page._writer_loader.ident)
            self.assertIsNone(page._writer_load_after_id)
            app.request_page("WPS写入工具")
            deadline = time.monotonic() + 2
            while not page._writer_load_done and time.monotonic() < deadline:
                app.update()
                time.sleep(.005)
            self.assertTrue(page._writer_load_done)
            for _ in range(20):
                app.request_page(names[0])
                app.request_page("WPS写入工具")
            load.assert_called_once()
            self.assertEqual([1], builds)
            self.assertIsNone(page._writer_load_after_id)

    def test_startup_opens_first_tool_and_creates_wps_only_on_first_visit(self) -> None:
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
        self.assertEqual(["导出上网电量"], list(app.pages))
        self.assertEqual([threading.main_thread()], created)
        self.assertEqual("导出上网电量", app.current_page)
        self.assertEqual("", app.dashboard.winfo_manager())
        app.show_page("WPS写入工具")
        page = app.pages["WPS写入工具"]
        app.show_page("今日概览")
        self.assertEqual("", page.winfo_manager())
        app.show_page("WPS写入工具")
        self.assertIs(page, app.pages["WPS写入工具"])
        self.assertEqual([threading.main_thread(), threading.main_thread()], created)

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
