from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from toolbox.catalog import default_catalog


class CatalogLoadingTests(unittest.TestCase):
    def test_idle_startup_does_not_load_business_modules_or_heavy_dependencies(self) -> None:
        script = """
import importlib.abc
import sys
import tempfile
from pathlib import Path

attempts = []
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("toolbox.pages", "toolbox_wps_writer_gui", "openpyxl",
                        "numpy", "pandas", "playwright", "win32com", "xlwings", "comtypes"):
            attempts.append(fullname)
            raise AssertionError("Startup imported " + fullname)

sys.meta_path.insert(0, Guard())
from toolbox.app import ToolboxApp
with tempfile.TemporaryDirectory() as folder:
    app = ToolboxApp(workspace=Path(folder))
    errors, pulses = [], []
    app.report_callback_exception = lambda *error: errors.append(error)
    def pulse():
        pulses.append(1)
        app.after(50, pulse)
    try:
        app.after(50, pulse)
        app.after(700, app.quit)
        app.mainloop()
        assert not errors, errors
        assert not attempts, attempts
        assert len(pulses) >= 5, pulses
        assert app.pages == {}, app.pages
        assert app.current_page == "今日概览"
    finally:
        app.destroy()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=15,
        )
        if "no display name" in result.stderr:
            self.skipTest("Tk display unavailable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_pages_open_without_loading_excel_on_the_ui_thread(self) -> None:
        # Separate processes prevent previously imported modules hiding regressions.
        script = """
import importlib.abc
import sys
import tempfile
import threading
from pathlib import Path
from toolbox.app import ToolboxApp
from toolbox.catalog import default_catalog

class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("openpyxl", "numpy", "pandas", "playwright"):
            if threading.current_thread() is threading.main_thread():
                raise AssertionError("UI thread imported " + fullname)

sys.meta_path.insert(0, Guard())
with tempfile.TemporaryDirectory() as folder:
    app = ToolboxApp(workspace=Path(folder))
    try:
        descriptor = next(item for item in default_catalog() if item.name == sys.argv[1])
        app.show_page(descriptor.name)
        app.update_idletasks()
        page = app.pages[descriptor.name]
        assert page.__class__.__name__ == descriptor.page_class
        assert page.winfo_manager() == "grid"
        if descriptor.name == "市场表更新":
            assert page.mapping_text.cget("font") == "TkTextFont"
        assert "openpyxl" not in sys.modules
        if descriptor.page_module != ".pages":
            assert "toolbox.pages" not in sys.modules
        app.show_page("今日概览")
        app.show_page(descriptor.name)
        assert app.pages[descriptor.name] is page
        assert page.winfo_manager() == "grid"
        # Existing imports remain compatible with the direct factory route.
        from toolbox import pages
        assert getattr(pages, descriptor.page_class) is type(page)
    finally:
        app.destroy()
"""
        for descriptor in default_catalog():
            with self.subTest(page=descriptor.name):
                result = subprocess.run(
                    [sys.executable, "-c", script, descriptor.name],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if "no display name" in result.stderr:
                    self.skipTest("Tk display unavailable")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
