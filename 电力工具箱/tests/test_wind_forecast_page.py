from __future__ import annotations

import tkinter as tk
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from toolbox.runtime import TaskRegistry, ToolPaths
from toolbox.wind_forecast_page import WindForecastPage
from toolbox.tasks import TaskState


class WindForecastPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk 不可用：{exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_command_and_paths(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        paths = ToolPaths(workspace)
        page = WindForecastPage(self.root, paths, TaskRegistry())
        command = page.build_generate_command("2026-08-21")
        self.assertEqual(command[-2:], [str(paths.wind_forecast), "2026-08-21"])
        self.assertEqual(paths.wind_forecast_output_dir, workspace / "风力预测截图" / "输出")
        page.destroy()

    def test_first_navigation_creates_only_wind_page_and_reuses_it(self) -> None:
        # A fresh process catches heavy imports and unwanted page creation.
        result = subprocess.run(
            [sys.executable, "-c", """
import sys
from toolbox.app import ToolboxApp
app = ToolboxApp()
try:
    assert app.current_page == "导出上网电量"
    assert set(app.pages) == {"导出上网电量"}
    app.show_page("风力预测截图")
    app.update_idletasks()
    page = app.pages["风力预测截图"]
    assert page.__class__.__name__ == "WindForecastPage"
    assert page.winfo_manager() == "grid"
    # The default export page imports page classes without constructing them.
    assert set(app.pages) == {"导出上网电量", "风力预测截图"}
    for name in ("openpyxl", "numpy", "playwright"):
        assert name not in sys.modules, name
    page.run_date.set("2026-08-21")
    app.show_page("今日概览")
    app.show_page("风力预测截图")
    assert app.pages["风力预测截图"] is page
    assert page.run_date.get() == "2026-08-21"
finally:
    app.destroy()
"""],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_api_result_shows_reason_in_information_dialog(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        paths = ToolPaths(workspace)
        registry = TaskRegistry()
        page = WindForecastPage(self.root, paths, registry)
        self.addCleanup(page.destroy)
        page.run_date.set("2026-08-21")
        # Exercise the real subprocess, log delivery, task completion and dialog.
        script = f"""
import importlib.util, sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location("wind_test_cli", {str(paths.wind_forecast)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.load_config = lambda: module.Config()
auth = SimpleNamespace(switch_company=lambda *args: None,
                       request_json_with_retry=lambda *args, **kwargs: {{"data": []}})
module.generate_screenshot = lambda day, config: module.fetch_forecast_rows(None, day, config, auth)
sys.argv = ["wind_test_cli", "2026-08-21"]
raise SystemExit(module.main())
"""
        with (
            patch.object(page, "build_generate_command", return_value=[sys.executable, "-c", script]),
            patch("toolbox.wind_forecast_page.messagebox.showinfo") as info,
            patch("toolbox.widgets.messagebox.showerror") as error,
        ):
            page.start_generate()
            task_id = page.current_task_id
            deadline = time.monotonic() + 5
            while page.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
            self.assertFalse(page.busy)
            self.assertEqual(registry.snapshot(task_id).state, TaskState.SUCCEEDED)
            self.assertEqual(page.status_var.get(), "无数据")
            info.assert_called_once()
            self.assertEqual(info.call_args.args[0], "无数据")
            self.assertIn("运行日期 2026-08-21", info.call_args.args[1])
            self.assertIn("原因：接口未返回风力预测记录", info.call_args.args[1])
            error.assert_not_called()
            self.assertNotIn("截图已生成", info.call_args.args[1])
            # A later successful run must not retain the previous no-data result.
            with patch.object(page, "run_process"):
                page.start_generate()
            self.assertIsNone(page._no_data_reason)
            page._generation_finished(0)
            self.assertEqual(info.call_args.args[0], "完成")


if __name__ == "__main__":
    unittest.main()
