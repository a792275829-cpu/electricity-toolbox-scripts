from __future__ import annotations
import importlib

import gc
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
import runpy
import os
import shutil
from contextlib import ExitStack
from datetime import date
from datetime import timedelta
from pathlib import Path
from unittest import mock

def destroy_tk(root: tk.Misc) -> None:
    try:
        root.update_idletasks()
    except tk.TclError:
        pass
    root.destroy()


class ToolboxAppTests(unittest.TestCase):
    """Window lifecycle and per-page log/busy state."""
    def create_root(self) -> tk.Tk:
        try:
            root = tk.Tk()
            root.withdraw()
            return root
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def test_app_creates_pages_and_switches_without_recreating(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        created: dict[str, tk.Frame] = {}

        def make_factory(name: str):
            def factory(parent, _paths, _registry):
                frame = tk.Frame(parent, name=f"page_{len(created)}")
                frame.marker = tk.StringVar(master=frame, value=name)  # type: ignore[attr-defined]
                created[name] = frame
                return frame

            return factory

        factories = {name: make_factory(name) for name in PAGE_NAMES}
        app = ToolboxApp(page_factories=factories)
        app.withdraw()
        self.addCleanup(destroy_tk, app)

        self.assertEqual(list(app.pages), list(PAGE_NAMES))
        first_page = app.pages["导出上网电量"]
        app.show_page("出清结果汇总上传")
        app.show_page("导出上网电量")

        self.assertEqual(app.current_page, "导出上网电量")
        self.assertIs(app.pages["导出上网电量"], first_page)
        self.assertEqual(first_page.marker.get(), "导出上网电量")  # type: ignore[attr-defined]

    def test_tool_page_keeps_its_own_log_and_busy_state(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.widgets import ToolPage

        root = self.create_root()
        self.addCleanup(destroy_tk, root)
        page = ToolPage(root, registry=TaskRegistry())
        page.append_log("第一条日志")
        page.set_busy(True, "运行中")
        root.update()

        self.assertIn("第一条日志", page.log_text.get("1.0", "end"))
        self.assertTrue(page.busy)
        self.assertEqual(page.status_var.get(), "运行中")

    def test_close_keeps_window_open_while_registered_write_thread_runs(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        # Finalize Tk cycles left by earlier GUI tests on the main thread before
        # this test deliberately starts a background thread.
        gc.collect()
        factories = {
            name: (lambda parent, _paths, _registry: tk.Frame(parent))
            for name in PAGE_NAMES
        }
        app = ToolboxApp(page_factories=factories, lazy_pages=True)
        app.withdraw()
        self.addCleanup(destroy_tk, app)
        started = threading.Event()
        release = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(timeout=5)

        thread = threading.Thread(target=worker, name="wps-write", daemon=True)
        app.registry.register_thread(thread)
        thread.start()
        self.assertTrue(started.wait(1))

        with (
            mock.patch("toolbox.app.messagebox.askyesno", return_value=True),
            mock.patch("toolbox.app.messagebox.showwarning") as warning,
        ):
            app._on_close()

        self.assertTrue(app.winfo_exists())
        warning.assert_called_once()
        release.set()
        thread.join(timeout=2)
        gc.collect()


class LauncherTests(unittest.TestCase):
    """Source and bundle launchers resolve the Python entrypoint."""
    def setUp(self) -> None:
        self.workspace = Path(__file__).resolve().parent.parent
        self.script_root = self.workspace.parent

    def test_pythonw_launcher_can_be_loaded_without_starting_mainloop(self) -> None:
        old_cwd = Path.cwd()
        old_sys_path = list(sys.path)
        old_toolbox_modules = {
            name: module for name, module in sys.modules.items()
            if name == "toolbox" or name.startswith("toolbox.")
        }
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                sys.path[:] = [
                    item
                    for item in sys.path
                    if item and Path(item).resolve() != self.workspace.resolve()
                ]
                for name in list(sys.modules):
                    if name == "toolbox" or name.startswith("toolbox."):
                        sys.modules.pop(name)
                namespace = runpy.run_path(
                    str(self.workspace / "toolbox_launcher.pyw"),
                    run_name="toolbox_smoke",
                )
            finally:
                # Other collected tests retain references to these modules.
                # Restore their identities so patches still reach the tested code.
                for name in list(sys.modules):
                    if name == "toolbox" or name.startswith("toolbox."):
                        sys.modules.pop(name)
                sys.modules.update(old_toolbox_modules)
                sys.path[:] = old_sys_path
                os.chdir(old_cwd)
        self.assertIn("main", namespace)

    def test_default_app_workspace_is_script_collection_root(self) -> None:
        from toolbox.app import default_workspace

        workspace = default_workspace()

        self.assertEqual(self.workspace.parent.resolve(), workspace)
        self.assertTrue((workspace / "wps自动" / "wps_excel_to_kdocs_gui.py").is_file())

    def test_toolbox_bundle_contains_python_entrypoint(self) -> None:
        self.assertTrue((self.workspace / "toolbox_launcher.pyw").is_file())
        self.assertTrue((self.workspace / "toolbox" / "app.py").is_file())

    def test_macos_command_launcher_finds_python_entrypoint(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS launcher smoke test")

        source = self.script_root / "00_启动" / "电力工具箱.command"
        self.assertTrue(source.is_file())
        content = source.read_text(encoding="utf-8")
        self.assertIn("TOOLBOX_SMOKE", content)
        self.assertNotIn("C:\\Users\\lllg", content)

        result = subprocess.run(
            [str(source)],
            cwd=self.script_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "TOOLBOX_SMOKE": "1"},
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("TOOLBOX_SCRIPT=", result.stdout)
        self.assertIn("toolbox_launcher.pyw", result.stdout)

    def test_readme_documents_unified_and_legacy_launchers(self) -> None:
        readme = next(self.script_root.glob("*/README_*.txt"))
        content = readme.read_text(encoding="utf-8")

        self.assertIn("00_启动/电力工具箱.command", content)
        self.assertIn("原有", content)
        self.assertIn("保留", content)


if __name__ == "__main__":
    unittest.main()
