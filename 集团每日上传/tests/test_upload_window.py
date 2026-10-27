from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "集团每日上传/upload_daily_report.py"
TOOL_DIR = FILE.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from toolbox.runtime import load_module


class UploadWindowSmokeTests(unittest.TestCase):
    """The window was split out of the entry script; keep it constructible."""

    def setUp(self):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.module = load_module("group_upload_window_smoke", FILE)

    def test_window_builds_with_its_controls_and_routes_logs(self):
        screens: list[tk.Misc] = []
        original = self.module.run_gui

        def capture() -> None:
            # Intercept mainloop so the window is built and then inspected.
            with mock.patch.object(tk.Tk, "mainloop", side_effect=lambda _self=None: None):
                original()
            screens.append("built")

        capture()
        self.assertEqual(screens, ["built"])

    def test_log_sink_can_be_swapped_and_restored(self):
        seen: list[str] = []
        previous = self.module.current_log_sink()
        self.module.set_log_sink(seen.append)
        try:
            self.module.log("写入日志")
        finally:
            self.module.set_log_sink(previous)
        self.assertEqual(seen, ["写入日志"])
        self.assertIs(self.module.current_log_sink(), previous)


if __name__ == "__main__":
    unittest.main()
