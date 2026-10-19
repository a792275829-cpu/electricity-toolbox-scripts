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
        self.release = Release("v0.2.0", "测试更新说明", "https://github.com/test/repo/releases/tag/v0.2.0", {"name": "test.pkg"})

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
        self.assertEqual(self.controller.window.primary.cget("text"), "下载并更新")
        self.assertIs(self.controller.window.controller.app, self.app)

    def test_automatic_errors_are_silent_but_manual_errors_are_visible(self):
        with patch("update_ui.messagebox.showerror") as error:
            self.controller.checked(None, ValueError("离线"))
            error.assert_not_called()
            self.controller.manual = True
            self.controller.checked(None, ValueError("离线"))
            error.assert_called_once()

    def test_one_click_starts_native_signed_update_and_cancel_waits_for_ack(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        window.perform()
        self.service.emit.assert_called_once_with({"event": {"kind": "begin_update", "version": "v0.2.0"}})
        self.assertTrue(window.downloading)
        window.dismiss()
        self.assertTrue(window.winfo_exists())
        self.assertEqual(self.service.emit.call_args.args[0]["event"]["kind"], "cancel_update")
        self.service.call("update_cancelled", {})
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

    def test_direct_source_start_does_not_install_or_request_privileges(self):
        self.service.emit = None
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        window.perform()
        self.assertFalse(window.downloading)
        self.assertIn("源码启动模式", window.status.get())


if __name__ == "__main__":
    unittest.main()
