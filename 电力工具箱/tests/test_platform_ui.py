from __future__ import annotations

import tkinter as tk
import unittest
import gc
from pathlib import Path


class CatalogTests(unittest.TestCase):
    def test_catalog_has_nine_unique_tools_in_business_groups(self) -> None:
        from toolbox.catalog import default_catalog

        catalog = default_catalog()
        self.assertEqual(9, len(catalog))
        self.assertEqual(9, len({item.tool_id for item in catalog}))
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


class ModuleCacheTests(unittest.TestCase):
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
