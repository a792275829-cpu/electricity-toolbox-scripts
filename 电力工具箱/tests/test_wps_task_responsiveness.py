from __future__ import annotations

import gc
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import TaskRegistry, load_module


class WpsTaskResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.update()
        self.module = load_module("wps_responsiveness_test", Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py")
        self.registry = TaskRegistry()
        with mock.patch.object(self.module, "load_config", return_value={}):
            self.frame = self.module.WpsWriterFrame(self.root, task_registry=self.registry)
        self.frame.configs = [self.module.DocumentConfig(
            name="Test", kdocs_url="target", local_file="source.xlsx",
            regions=[self.module.RegionRow("Sheet1", "A1", "A1", "Sheet1", "B1", "B1")],
        )]
        self.frame.refresh_tree()
        self.frame.select_config_indexes([0])

    def tearDown(self):
        if self.frame.worker:
            self.frame.worker.join(3)
        self.frame.destroy()
        self.root.update_idletasks()
        self.root.destroy()
        gc.collect()

    def test_slow_source_read_keeps_tk_responsive_and_uses_captured_inputs(self):
        for action in ("preview", "write"):
            with self.subTest(action=action):
                entered, release = threading.Event(), threading.Event()
                read_threads, captured, pulses = [], [], []
                self.frame.cdp_url.set("original-endpoint")

                def read(mappings, **kwargs):
                    read_threads.append(threading.current_thread())
                    captured.append((mappings[0].local_file, kwargs["cdp_url"]))
                    entered.set()
                    release.wait(1)
                    return []

                with (
                    mock.patch.object(self.module, "prepare_mappings", side_effect=read),
                    mock.patch.object(self.frame, "save_current_config"),
                    mock.patch.object(self.frame, "detail_log"),
                    mock.patch.object(self.frame, "write_worker") as write,
                ):
                    try:
                        started = time.monotonic()
                        getattr(self.frame, action)()
                        self.assertLess(time.monotonic() - started, 0.5)
                        self.assertTrue(entered.wait(0.5))
                        self.frame.cdp_url.set("changed-endpoint")
                        self.root.after(0, lambda: pulses.append(True))
                        self.root.update()
                        self.assertEqual([True], pulses)
                        self.assertTrue(self.registry.has_running_tasks())
                        getattr(self.frame, action)()  # Cannot duplicate a running operation.
                    finally:
                        release.set()
                        if self.frame.worker:
                            self.frame.worker.join(2)
                    self.frame.drain_logs()
                    self.assertEqual([("source.xlsx", "original-endpoint")], captured)
                    self.assertNotEqual([threading.main_thread()], read_threads)
                    self.assertFalse(self.registry.has_running_tasks())
                    if action == "write":
                        write.assert_called_once_with([], "managed_cdp", "original-endpoint")
                    else:
                        write.assert_not_called()

    def test_prepare_failure_returns_to_ui_and_next_task_can_start(self):
        with (
            mock.patch.object(self.module, "prepare_mappings", side_effect=ValueError("bad source")),
            mock.patch.object(self.frame, "detail_log"),
            mock.patch.object(self.module.messagebox, "showerror") as error,
        ):
            self.frame.preview()
            self.frame.worker.join(2)
            error.assert_not_called()
            self.frame.drain_logs()
            error.assert_called_once()
            self.assertEqual("normal", str(self.frame.preview_button.cget("state")))
            self.assertFalse(self.registry.has_running_tasks())


if __name__ == "__main__":
    unittest.main()
