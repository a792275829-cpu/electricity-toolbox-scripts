from __future__ import annotations

import gc
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
from toolbox_shared.credentials import CredentialStore
from toolbox_shared.qingneng_login import LoginError
from toolbox.ui.credentials_panel import CredentialsPanel


class StartupAccountTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.store = CredentialStore(self.workspace)
        self.saved = self.store.save("fixture", "fixture-secret")

    def tearDown(self):
        self.root.destroy()
        self.temp.cleanup()
        gc.collect()

    def pump_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(condition(), "账号状态未在预期时间内更新")

    def test_startup_is_async_checks_saved_pair_once_and_does_not_write_config(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        ui_tick = []
        threads = []
        before = self.store.path.read_bytes()

        def verify(*_args):
            threads.append(threading.current_thread())
            entered.set()
            release.wait(3)
            finished.set()

        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=verify) as check:
            panel = CredentialsPanel(self.root, self.workspace)
            try:
                self.assertFalse(entered.is_set())
                self.pump_until(entered.is_set)
                self.assertEqual(panel.status_kind, "pending")
                self.root.after(0, lambda: ui_tick.append(True))
                self.pump_until(lambda: bool(ui_tick))
                self.assertIsNot(threads[0], threading.main_thread())
                panel.retry_saved()
                check.assert_called_once_with("fixture", "fixture-secret")
                release.set()
                self.pump_until(lambda: panel.status_kind == "valid")
                self.assertEqual(panel.status_var.get(), "✓ 账号有效")
                self.assertEqual(str(panel.status_label.cget("foreground")), panel.STATUS_COLORS["valid"])
                self.assertEqual(panel.retry_button.winfo_manager(), "")
                self.assertFalse(panel.dirty)
                self.assertEqual(self.store.path.read_bytes(), before)
            finally:
                release.set()
                finished.wait(1)
                panel.destroy()

    def test_empty_account_never_starts_network_check(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch("toolbox.ui.credentials_panel.verify_credentials") as check:
            panel = CredentialsPanel(self.root, Path(empty))
            self.root.update()
            self.assertIsNone(panel._startup_id)
            panel.retry_saved()
            check.assert_not_called()
            self.assertEqual(panel.status_kind, "neutral")
            panel.destroy()

    def test_failure_is_red_and_requires_explicit_retry(self):
        for kind, text in (("credentials", "账号或密码"), ("network", "连接失败"),
                           ("server", "服务暂不可用"), ("session", "暂未确认")):
            with self.subTest(kind=kind), mock.patch("toolbox.ui.credentials_panel.verify_credentials",
                    side_effect=[LoginError(kind, "fixture error"), None]) as check:
                panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
                panel.retry_saved()
                self.pump_until(lambda: panel._verification_id is None)
                self.assertEqual(panel.status_kind, "error")
                self.assertIn(text, panel.status_var.get())
                self.assertEqual(str(panel.status_label.cget("foreground")), panel.STATUS_COLORS["error"])
                self.assertEqual(panel.retry_button.winfo_manager(), "pack")
                self.assertIsNone(panel._startup_id)
                self.root.update()
                check.assert_called_once()
                panel.retry_button.invoke()
                self.pump_until(lambda: panel.status_kind == "valid")
                self.assertEqual(check.call_count, 2)
                self.assertEqual(self.store.load(), self.saved)
                panel.destroy()

    def test_captcha_is_amber_and_opens_current_tool_without_auto_login(self):
        callback = mock.Mock()
        self.root._visible_page = SimpleNamespace(tool_id="online-energy", start_login=callback)
        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=LoginError("verification", "captcha")):
            panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
            panel.retry_saved()
            self.pump_until(lambda: panel._verification_id is None)
            self.assertEqual(panel.status_kind, "manual")
            self.assertEqual(str(panel.status_label.cget("foreground")), panel.STATUS_COLORS["manual"])
            self.assertIn("需要人工验证", panel.status_var.get())
            callback.assert_not_called()
            panel.manual_login_button.invoke()
            callback.assert_called_once()
            panel.destroy()

    def test_draft_before_startup_is_not_submitted(self):
        with mock.patch("toolbox.ui.credentials_panel.verify_credentials") as check:
            panel = CredentialsPanel(self.root, self.workspace)
            panel.password_var.set("unsaved-draft")
            panel.retry_saved()
            check.assert_not_called()
            self.assertIsNone(panel._startup_id)
            self.assertTrue(panel.dirty)
            self.assertEqual(self.store.load(), self.saved)
            panel.destroy()

    def test_manual_action_resolves_tool_after_navigation(self):
        self.root._visible_page = SimpleNamespace(tool_id="wps-writer")
        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=LoginError("verification", "captcha")):
            panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
            panel.retry_saved()
            self.pump_until(lambda: panel._verification_id is None)
            self.assertEqual(panel.manual_login_button.winfo_manager(), "pack")
            panel.manual_login_button.invoke()
            self.assertIn("切换", panel.status_var.get())
            callback = mock.Mock()
            self.root._visible_page = SimpleNamespace(tool_id="market-table", start_login=callback)
            panel.manual_login_button.invoke()
            callback.assert_called_once()
            panel.destroy()

    def test_edit_while_checking_discards_late_success_even_if_text_is_restored(self):
        release = threading.Event()
        finished = threading.Event()

        def verify(*_args):
            release.wait(3)
            finished.set()

        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=verify):
            panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
            try:
                panel.retry_saved()
                panel.password_var.set("draft")
                panel.password_var.set("fixture-secret")
                release.set()
                self.pump_until(lambda: panel._verification_id is None)
                self.assertEqual(panel.status_kind, "neutral")
                self.assertTrue(panel.dirty)
                self.assertIn("重新验证", panel.status_var.get())
                self.assertEqual(self.store.load(), self.saved)
            finally:
                release.set()
                finished.wait(1)
                panel.destroy()

    def test_external_account_change_does_not_show_old_account_as_valid(self):
        release = threading.Event()
        finished = threading.Event()

        def verify(*_args):
            release.wait(3)
            finished.set()

        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=verify):
            panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
            try:
                panel.retry_saved()
                updated = self.store.save("another", "another-secret")
                release.set()
                self.pump_until(lambda: panel._verification_id is None)
                self.assertNotEqual(panel.status_kind, "valid")
                self.assertEqual(self.store.load(), updated)
            finally:
                release.set()
                finished.wait(1)
                panel.destroy()

    def test_destroy_cancels_scheduled_startup_and_discards_running_result(self):
        with mock.patch("toolbox.ui.credentials_panel.verify_credentials") as check:
            panel = CredentialsPanel(self.root, self.workspace)
            scheduled = panel._startup_id
            panel.destroy()
            self.assertNotIn(scheduled, self.root.tk.call("after", "info"))
            self.root.update()
            check.assert_not_called()
        release = threading.Event()
        finished = threading.Event()

        def verify(*_args):
            release.wait(3)
            finished.set()

        with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=verify):
            panel = CredentialsPanel(self.root, self.workspace, auto_verify=False)
            panel.retry_saved()
            panel.destroy()
            release.set()
            self.assertTrue(finished.wait(1))
            self.root.update()
            self.assertEqual(self.store.load(), self.saved)


if __name__ == "__main__":
    unittest.main()
