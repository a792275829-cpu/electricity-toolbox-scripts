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

    def test_activate_restores_hidden_main_window_without_loading_business_pages(self):
        self.app.withdraw()
        self.assertTrue(self.service.call("activate", {}))
        self.app.update_idletasks()
        self.assertEqual(self.app.state(), "normal")
        self.assertEqual(self.app.current_page, "今日概览")
        self.assertEqual(self.app.pages, {})

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
        self.assertNotEqual(self.app.state(), "withdrawn")
        self.assertTrue(self.service._locked_widgets)
        self.assertFalse(self.service.call("activate", {}))
        self.service.call("abort_update", {})
        self.app.update_idletasks()
        self.assertNotEqual(self.app.state(), "withdrawn")
        self.assertEqual(self.app.search_var.get(), "风力")
        self.app.search_var.set("")
        with self.assertRaises(ValueError):
            self.service.call("finish_update", {})

    def test_update_progress_is_visible_in_footer_and_failure_restores_input(self):
        tags = self.app.search_entry.bindtags()
        self.service.call("prepare_update", {})
        try:
            self.service.call("update_status", {"phase": "downloading", "progress": 42})
            self.app.update_idletasks()
            self.assertNotEqual(self.app.state(), "withdrawn")
            self.assertTrue(self.app.update_progress.winfo_ismapped())
            self.assertEqual(self.app.update_status_var.get(), "正在下载 42%")
            self.assertTrue(self.app.update_button.instate(["disabled"]))
            self.assertNotEqual(self.app.search_entry.bindtags(), tags)
            with patch("native_bridge.messagebox.showinfo") as notice:
                self.assertFalse(self.service.call("request_close", {}))
                notice.assert_called_once()
        finally:
            self.service.call("abort_update", {})
            self.service.call("update_status", {"phase": "idle"})
        self.assertEqual(self.app.search_entry.bindtags(), tags)
        self.assertTrue(self.app.update_button.instate(["!disabled"]))
        self.assertFalse(self.app.update_progress.winfo_ismapped())

    def test_update_dialogs_are_concise_and_use_original_window(self):
        with patch("native_bridge.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(self.service.call("update_prompt", {"version": "0.1.3"}))
            ask.assert_called_once_with("检查更新", "发现新版本 0.1.3，是否开始更新？", parent=self.app)
        with patch("native_bridge.messagebox.showinfo") as info:
            self.service.call("update_message", {"message": "当前已是最新版本"})
            info.assert_called_once_with("检查更新", "当前已是最新版本", parent=self.app)

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
