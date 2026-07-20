from __future__ import annotations

import tkinter as tk
import unittest
import gc
from pathlib import Path


class CatalogTests(unittest.TestCase):
    def test_catalog_has_ten_unique_tools_in_business_groups(self) -> None:
        from toolbox.catalog import default_catalog

        catalog = default_catalog()
        self.assertEqual(10, len(catalog))
        self.assertEqual(10, len({item.tool_id for item in catalog}))
        self.assertEqual(
            {"数据采集", "分析与报告", "上传与写入"},
            {item.category for item in catalog},
        )

    def test_adapter_registry_matches_catalog_and_marks_side_effects(self) -> None:
        from toolbox.adapters import default_adapters
        from toolbox.catalog import default_catalog

        adapters = default_adapters()
        self.assertEqual(
            {item.tool_id for item in default_catalog()},
            set(adapters),
        )
        self.assertFalse(adapters["trade-analysis"].destructive)
        self.assertTrue(adapters["group-upload"].destructive)
        self.assertTrue(adapters["guangdong-price"].retryable_read)


class DiagnosticTests(unittest.TestCase):
    def test_diagnostics_report_missing_tool_files_without_raising(self) -> None:
        import tempfile
        from toolbox.diagnostics import diagnose_environment
        from toolbox.runtime import ToolPaths

        with tempfile.TemporaryDirectory() as directory:
            report = diagnose_environment(ToolPaths(Path(directory)))
        self.assertFalse(report.ok)
        self.assertTrue(any("找不到" in item.detail for item in report.checks))

    def test_diagnostics_distinguish_optional_warning_from_blocking_error(self) -> None:
        from toolbox.diagnostics import DiagnosticCheck, DiagnosticLevel, DiagnosticReport

        report = DiagnosticReport(
            (
                DiagnosticCheck("正常", True, "可用"),
                DiagnosticCheck("可选组件", True, "未安装", level=DiagnosticLevel.WARNING),
                DiagnosticCheck("必要文件", False, "缺失"),
            )
        )

        self.assertEqual(1, len(report.passed))
        self.assertEqual(1, len(report.warnings))
        self.assertEqual(1, len(report.errors))
        self.assertFalse(report.ok)

    def test_path_diagnostics_only_inspect_and_do_not_modify_file(self) -> None:
        import tempfile
        from toolbox.diagnostics import diagnose_path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xlsx"
            path.write_bytes(b"unchanged")
            before = path.stat()
            report = diagnose_path(path)
            after = path.stat()

        self.assertTrue(report.ok)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)


class WorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            gc.collect()

    def test_lazy_app_starts_with_dashboard_and_creates_page_once(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        self.root = None
        created: list[str] = []

        def factory(name: str):
            def build(parent, _paths, _registry):
                created.append(name)
                return tk.Frame(parent)
            return build

        app = ToolboxApp(
            workspace=Path.cwd(),
            page_factories={name: factory(name) for name in PAGE_NAMES},
            lazy_pages=True,
        )
        app.withdraw()
        self.root = app

        self.assertEqual("今日概览", app.current_page)
        self.assertEqual([], created)
        app.show_page("电量汇总")
        app.show_page("电量汇总")
        self.assertEqual(["电量汇总"], created)
        self.assertTrue(hasattr(app, "task_center"))
        self.assertIsNotNone(app.diagnostic_center)
        self.assertEqual("grid", app.diagnostic_center.winfo_manager())
        self.assertNotIn("运行诊断中心", app.nav_buttons)
        self.assertIs(app.task_center.master, app.diagnostic_center.master)
        self.assertLess(
            int(app.task_center.grid_info()["row"]),
            int(app.diagnostic_center.grid_info()["row"]),
        )


class TaskCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            gc.collect()

    def test_refresh_preserves_selected_task_identity_when_history_rolls(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.tasks import TaskSnapshot, TaskState
        from toolbox.ui import TaskCenter

        registry = TaskRegistry()
        task_ids = [f"task-{index}" for index in range(13)]
        registry._snapshots = {
            task_id: TaskSnapshot(
                task_id=task_id,
                name=task_id,
                state=TaskState.SUCCEEDED,
            )
            for task_id in task_ids
        }
        center = TaskCenter(self.root, registry)
        center._refresh()
        selected_id = task_ids[2]
        center.listbox.selection_set(center._task_ids.index(selected_id))

        registry._snapshots["task-13"] = TaskSnapshot(
            task_id="task-13",
            name="task-13",
            state=TaskState.SUCCEEDED,
        )
        center._refresh()

        selected = center.listbox.curselection()
        self.assertTrue(selected)
        self.assertEqual(selected_id, center._task_ids[selected[0]])
        center.destroy()

    def test_cancel_button_is_disabled_for_finished_task(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.tasks import TaskSnapshot, TaskState
        from toolbox.ui import TaskCenter

        registry = TaskRegistry()
        task_id = "finished"
        registry._snapshots[task_id] = TaskSnapshot(
            task_id=task_id,
            name=task_id,
            state=TaskState.SUCCEEDED,
        )
        center = TaskCenter(self.root, registry)
        center._refresh()
        center.listbox.selection_set(center._task_ids.index(task_id))
        center._sync_cancel_state()

        self.assertEqual("disabled", str(center.cancel_button.cget("state")))
        center.destroy()


class ModuleCacheTests(unittest.TestCase):
    def test_load_module_isolates_same_named_sibling_imports(self) -> None:
        import sys
        import tempfile
        from toolbox.runtime import clear_module_cache, load_module

        original_path = list(sys.path)
        previous_helper = sys.modules.get("helper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "helper.py").write_text("VALUE = 'first'\n", encoding="utf-8")
            (second_dir / "helper.py").write_text("VALUE = 'second'\n", encoding="utf-8")
            (first_dir / "main.py").write_text(
                "import helper\nVALUE = helper.VALUE\n", encoding="utf-8"
            )
            (second_dir / "main.py").write_text(
                "import helper\nVALUE = helper.VALUE\n", encoding="utf-8"
            )

            clear_module_cache()
            first = load_module("isolated_first", first_dir / "main.py")
            second = load_module("isolated_second", second_dir / "main.py")

        self.assertEqual("first", first.VALUE)
        self.assertEqual("second", second.VALUE)
        self.assertEqual(original_path, sys.path)
        self.assertIs(previous_helper, sys.modules.get("helper"))

    def test_load_module_reuses_unchanged_file_and_invalidates_on_change(self) -> None:
        import tempfile
        import time
        from toolbox.runtime import clear_module_cache, load_module

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("VALUE = 1\n", encoding="utf-8")
            clear_module_cache()
            first = load_module("cached_sample", path)
            second = load_module("cached_sample", path)
            self.assertIs(first, second)
            time.sleep(0.01)
            path.write_text("VALUE = 2\n", encoding="utf-8")
            third = load_module("cached_sample", path)
            self.assertIsNot(first, third)
            self.assertEqual(2, third.VALUE)


if __name__ == "__main__":
    unittest.main()
