from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]
from toolbox.app import PAGE_NAMES, ToolboxApp
from native_bridge import NativeService
from github_update import Release
from update_ui import UpdateController


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ToolboxApp(workspace=Path(self.temp.name), page_factories={
            name: lambda parent, paths, registry: tk.Frame(parent) for name in PAGE_NAMES
        }, check_updates=lambda: self.controller.show())
        self.service = NativeService(self.app, emit=Mock())
        self.updater = Mock(current="0.1.3")
        self.updater.check.return_value = None
        self.controller = UpdateController(self.app, self.service, self.updater)
        self.service.updater = self.controller
        self.release = Release("v0.2.0", "测试更新说明", "https://github.com/test/repo/releases/tag/v0.2.0", {"name": "test.dmg"})

    def tearDown(self):
        self.app.destroy()
        self.temp.cleanup()

    def pump(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_background_result_only_shows_badge_manual_check_opens_notes(self):
        self.controller.checked(self.release, None)
        self.assertEqual(self.app.update_button.winfo_manager(), "pack")
        self.assertIsNone(self.controller.window)
        self.updater.check.return_value = self.release
        self.controller.check(manual=True)
        self.pump(lambda: self.controller.window is not None)
        self.assertEqual(self.controller.window.primary.cget("text"), "下载更新")
        self.assertIs(self.controller.window.controller.app, self.app)

    def test_automatic_errors_are_silent_but_manual_errors_are_visible(self):
        with patch("update_ui.messagebox.showerror") as error:
            self.controller.checked(None, ValueError("离线"))
            error.assert_not_called()
            self.controller.manual = True
            self.controller.checked(None, ValueError("离线"))
            error.assert_called_once()

    def test_dmg_download_cancellation_waits_for_worker_ack(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.controller, "worker") as worker:
            window.perform()
            self.assertTrue(window.downloading)
            self.service.emit.assert_not_called()
            window.dismiss()
            self.assertTrue(window.cancel_download.is_set())
            self.assertTrue(window.winfo_exists())
            from github_update import Cancelled
            worker.call_args.args[1](None, Cancelled("已取消下载"))
            self.assertFalse(window.winfo_exists())

    def test_automatic_install_keeps_task_guard_and_failure_unlocks_inputs(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.app.registry, "has_running_tasks", return_value=True):
            with self.assertRaisesRegex(ValueError, "等待"):
                self.service.call("prepare_update", {})
        self.assertFalse(self.service.maintenance)
        self.service.call("prepare_update", {})
        self.service.call("update_progress", {"stage": "installing", "percent": 100})
        self.assertTrue(window.installing)
        self.assertTrue(self.service.maintenance)
        window.dismiss()
        self.assertTrue(window.winfo_exists())
        self.service.call("update_failed", {"error": "签名校验失败"})
        self.assertFalse(window.installing)
        self.assertFalse(self.service.maintenance)
        self.assertFalse(self.service._locked_widgets)
        self.assertEqual(window.status.get(), "签名校验失败")
        self.assertEqual(window.primary.cget("text"), "重试更新")

    def test_verified_dmg_opens_without_installing_or_locking_tasks(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        self.updater.download.return_value = Path(self.temp.name) / "test.dmg"
        with patch("update_ui.subprocess.run") as run:
            window.perform()
            self.pump(lambda: window.downloaded_path is not None)
            run.assert_called_once_with(["/usr/bin/open", str(self.updater.download.return_value)], check=True)
        self.service.emit.assert_not_called()
        self.assertFalse(window.downloading)
        self.assertFalse(self.service.maintenance)
        self.assertIn("请先退出工具箱", window.status.get())
        self.assertEqual(window.primary.cget("text"), "打开 DMG")

    def test_failed_download_can_be_retried_without_opening(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        self.updater.download.side_effect = ValueError("安装包校验失败")
        with patch("update_ui.subprocess.run") as run:
            window.perform()
            self.pump(lambda: not window.downloading)
            run.assert_not_called()
        self.assertIn("校验失败", window.status.get())
        self.assertEqual(window.primary.cget("text"), "重试下载")


if __name__ == "__main__":
    unittest.main()
