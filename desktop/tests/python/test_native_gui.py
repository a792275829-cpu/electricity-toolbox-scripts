from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]

from native_bridge import NativeService
from toolbox.app import ToolboxApp


class NativeLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.update_requests = []
        cls.app = ToolboxApp(workspace=Path(cls.temp.name),
                             check_updates=lambda: cls.update_requests.append("check"))
        # This lifecycle fixture has no business files; page loading is covered
        # separately by the existing original GUI tests on the full workspace.
        cls.app.after_cancel(cls.app._warmup_after_id)
        cls.app._warmup_after_id = None
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.temp.cleanup()

    def setUp(self):
        self.service = NativeService(self.app)
        self.app.deiconify()
        self.app.update_idletasks()

    def test_running_write_blocks_update_without_hiding_the_window(self):
        release = threading.Event()
        self.app.registry.start_callable(
            "测试写入", lambda token, emit: release.wait(10), cancellable=False
        )
        try:
            with self.assertRaisesRegex(ValueError, "等待"):
                self.service.call("prepare_update", {})
            self.assertNotEqual(self.app.state(), "withdrawn")
            self.assertFalse(self.service.maintenance)
        finally:
            release.set()
            self.app.registry.shutdown(timeout=2)

    def test_failed_account_save_blocks_update(self):
        with (
            patch.object(self.app.credentials_panel, "dirty", True),
            patch.object(self.app.credentials_panel, "save", return_value=False),
        ):
            with self.assertRaises(ValueError):
                self.service.call("prepare_update", {})
        self.assertNotEqual(self.app.state(), "withdrawn")

    def test_download_failure_restores_original_window_and_form_values(self):
        self.app.search_var.set("风力")
        self.service.call("prepare_update", {})
        self.assertEqual(self.app.state(), "withdrawn")
        self.assertFalse(self.service.call("activate", {}))
        self.service.call("abort_update", {})
        self.app.update_idletasks()
        self.assertNotEqual(self.app.state(), "withdrawn")
        self.assertEqual(self.app.search_var.get(), "风力")
        self.app.search_var.set("")
        with self.assertRaises(ValueError):
            self.service.call("finish_update", {})

    def test_native_close_retains_original_cancel_confirmation(self):
        release = threading.Event()
        self.app.registry.start_callable(
            "测试任务", lambda token, emit: release.wait(10)
        )
        try:
            with patch(
                "toolbox.app.messagebox.askyesno", return_value=False
            ) as confirm:
                self.service.call("request_close", {})
                self.app.update_idletasks()
            confirm.assert_called_once()
            self.assertTrue(self.app.winfo_exists())
            self.assertTrue(self.app.registry.has_running_tasks())
        finally:
            release.set()
            self.app.registry.shutdown(timeout=2)

    def test_updater_cannot_run_business_actions_or_read_passwords(self):
        for method in ("start", "credentials.get", "credentials.save", "wps.save"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.service.call(method, {})

    def test_footer_update_button_remains_available_when_auxiliary_panel_is_hidden(self):
        self.update_requests.clear()
        try:
            self.app.toggle_right_rail()
            self.app.update_idletasks()
            self.assertTrue(self.app.update_button.winfo_ismapped())
            self.app.update_button.invoke()
            self.assertEqual(self.update_requests, ["check"])
        finally:
            if not self.app.right_rail_visible:
                self.app.toggle_right_rail()


class NativeProtocolTests(unittest.TestCase):
    def test_actual_gui_starts_and_closes_through_json_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "desktop/python/native_bridge.py"),
                    "--workspace",
                    folder,
                ],
                input='{"id":1,"method":"ping"}\n{"id":2,"method":"request_close"}\n',
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1"},
                timeout=20,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = [json.loads(line) for line in proc.stdout.splitlines()]
        replies = {row["id"]: row for row in rows if "id" in row}
        self.assertEqual(replies[1]["result"]["ui"], "original-tk")
        self.assertTrue(replies[2]["result"])
        self.assertEqual(
            [row["event"]["kind"] for row in rows if "event" in row],
            ["native-ready", "native-closed"],
        )


if __name__ == "__main__":
    unittest.main()
