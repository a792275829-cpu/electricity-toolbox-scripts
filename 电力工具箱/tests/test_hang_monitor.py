from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

from toolbox.hang_monitor import TkHangMonitor


class HangMonitorTests(unittest.TestCase):
    def test_hung_main_thread_is_recorded_and_monitor_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            monitor = TkHangMonitor(Path(directory), timeout=0.3, interval=0.02)
            def block_ui():
                time.sleep(0.4)
            try:
                monitor.start(root)
                root.after(100, block_ui)
                root.after(700, root.quit)
                root.mainloop()
                logs = list(Path(directory).glob("tk-hang-*.log"))
                self.assertEqual(1, len(logs))
                self.assertIn("block_ui", logs[0].read_text(encoding="utf-8"))
                callback = monitor._after_id
                monitor.stop(root)
                monitor._thread.join(1)
                self.assertFalse(monitor._thread.is_alive())
                self.assertNotIn(callback, root.tk.call("after", "info"))
            finally:
                monitor.stop(root)
                root.destroy()

    def test_responsive_main_thread_does_not_create_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            monitor = TkHangMonitor(Path(directory), timeout=0.3, interval=0.02)
            try:
                monitor.start(root)
                root.after(700, root.quit)
                root.mainloop()
                self.assertEqual([], list(Path(directory).iterdir()))
            finally:
                monitor.stop(root)
                monitor._thread.join(1)
                root.destroy()
