from __future__ import annotations

import gc
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from toolbox.runtime import load_module


def load_writer():
    return load_module(
        "wps_login_status_test",
        Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py",
    )


class WpsLoginStatusTests(unittest.TestCase):
    def setUp(self):
        self.module = load_writer()
        self.context = mock.Mock()

    def response(self, status, data):
        self.context.request.get.return_value = mock.Mock(status=status)
        self.context.request.get.return_value.json.return_value = data
        return self.module.read_wps_login_status(self.context)

    def test_server_confirmed_account_is_logged_in(self):
        result = self.response(200, {"result": "ok", "userid": 123})
        self.assertEqual(result.state, "logged_in")
        self.assertEqual(result.message, "已登录")
        self.assertNotIn("123", result.message)
        self.context.request.get.assert_called_once_with(
            self.module.WPS_LOGIN_STATUS_URL, timeout=10000
        )

    def test_expired_session_is_logged_out(self):
        result = self.response(403, {"result": "userNotLogin"})
        self.assertEqual(result.state, "logged_out")
        self.assertIn("登录 WPS", result.message)

    def test_access_denied_does_not_mean_logged_out(self):
        for status, data in (
            (403, {"result": "blocked"}),
            (503, {"result": "error"}),
            (200, {"result": "ok"}),
            (200, []),
        ):
            with self.subTest(status=status, data=data):
                self.assertEqual(self.response(status, data).state, "unknown")

    def test_new_mac_without_profile_needs_login_without_starting_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.object(self.module, "PROFILE_DIR", Path(folder) / "new-profile"),
                mock.patch.object(self.module, "sync_playwright") as playwright,
            ):
                result = self.module.check_wps_login_status("managed_cdp", "", mock.Mock())
        self.assertEqual(result.state, "logged_out")
        playwright.assert_not_called()

    def test_copied_profile_is_verified_online_and_does_not_imply_login(self):
        manager = mock.MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.object(self.module, "PROFILE_DIR", Path(folder)),
                mock.patch.object(self.module, "sync_playwright", return_value=manager),
                mock.patch.object(self.module, "open_browser_context", return_value=(None, self.context, False)) as opened,
            ):
                self.context.request.get.return_value = mock.Mock(status=403)
                self.context.request.get.return_value.json.return_value = {"result": "userNotLogin"}
                result = self.module.check_wps_login_status("managed_cdp", "local", mock.Mock())
        self.assertEqual(result.state, "logged_out")
        self.assertTrue(opened.call_args.kwargs["for_login_check"])
        self.context.close.assert_not_called()

    def test_temporary_profile_is_closed_even_on_network_failure(self):
        manager = mock.MagicMock()
        self.context.request.get.side_effect = RuntimeError("网络不可用")
        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.object(self.module, "PROFILE_DIR", Path(folder)),
                mock.patch.object(self.module, "sync_playwright", return_value=manager),
                mock.patch.object(self.module, "open_browser_context", return_value=(None, self.context, True)),
            ):
                with self.assertRaisesRegex(RuntimeError, "网络不可用"):
                    self.module.check_wps_login_status("persistent", "local", mock.Mock())
        self.context.close.assert_called_once()

    def test_check_does_not_switch_visible_login_browser_to_headless(self):
        playwright = mock.Mock()
        browser = playwright.chromium.connect_over_cdp.return_value
        browser.contexts = [self.context]
        with (
            mock.patch.object(self.module, "try_resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "ensure_wps_dedicated_chrome") as launch,
            mock.patch.object(self.module, "ensure_wps_cdp_page") as ensure_page,
        ):
            result = self.module.open_browser_context(
                playwright, "managed_cdp", "local", mock.Mock(), for_login_check=True
            )
        launch.assert_not_called()
        ensure_page.assert_called_once_with("local")
        self.assertEqual(result, (browser, self.context, False))

    def test_failed_check_does_not_restart_browser_during_login(self):
        playwright = mock.Mock()
        playwright.chromium.connect_over_cdp.side_effect = self.module.PlaywrightTimeoutError("连接超时")
        with (
            mock.patch.object(self.module, "try_resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "ensure_wps_cdp_page"),
            mock.patch.object(self.module, "stop_managed_chrome") as stop,
        ):
            with self.assertRaises(self.module.PlaywrightTimeoutError):
                self.module.open_browser_context(
                    playwright, "managed_cdp", "local", mock.Mock(), for_login_check=True
                )
        stop.assert_not_called()


class WpsLoginStatusUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.module = load_writer()
        with mock.patch.object(self.module, "load_config", return_value={}):
            self.frame = self.module.WpsWriterFrame(self.root)

    def tearDown(self):
        if hasattr(self, "frame"):
            self.frame.destroy()
        if hasattr(self, "root"):
            self.root.destroy()
        gc.collect()

    def check_status(self, **kwargs):
        with mock.patch.object(self.module, "check_wps_login_status", **kwargs):
            self.frame.check_wps_browser()
            self.frame.browser_worker.join(2)
            self.assertFalse(self.frame.browser_worker.is_alive())
            self.frame.drain_logs()

    def test_login_status_changes_after_login(self):
        self.check_status(return_value=self.module.WpsLoginStatus("logged_out", "请登录"))
        self.assertEqual(self.frame.login_status.get(), "请登录")
        self.check_status(return_value=self.module.WpsLoginStatus("logged_in", "已登录"))
        self.assertEqual(self.frame.login_status.get(), "已登录")
        self.assertEqual(str(self.frame.check_browser_button.cget("state")), "normal")

    def test_network_failure_displays_unknown_and_restores_buttons(self):
        self.check_status(side_effect=RuntimeError("网络不可用"))
        self.assertIn("暂时无法确认", self.frame.login_status.get())
        self.assertEqual(str(self.frame.check_browser_button.cget("state")), "normal")
        self.assertEqual(str(self.frame.start_browser_button.cget("state")), "normal")

    def test_changing_connection_discards_old_account_status(self):
        self.frame.login_event_queue.put((
            self.frame.browser_mode.get(), self.frame.cdp_url.get(),
            self.module.WpsLoginStatus("logged_in", "已登录"),
        ))
        self.frame.cdp_url.set("http://127.0.0.1:9333")
        self.frame.drain_logs()
        self.assertEqual(self.frame.login_status.get(), "尚未检查")

    def test_login_button_opens_visible_login_page(self):
        with (
            mock.patch.object(self.frame, "save_current_config"),
            mock.patch.object(self.module, "ensure_wps_dedicated_chrome", return_value={"started": True}) as launch,
            mock.patch.object(self.module, "check_wps_login_status", return_value=self.module.WpsLoginStatus("logged_out", "请登录")),
        ):
            self.frame.start_wps_browser()
            self.frame.browser_worker.join(2)
            self.frame.drain_logs()
        self.assertEqual(self.frame.browser_mode.get(), "managed_cdp")
        self.assertEqual(launch.call_args.kwargs["initial_url"], "https://www.kdocs.cn/latest")
        self.assertTrue(launch.call_args.kwargs["preload_url"])
        self.assertNotIn("headless", launch.call_args.kwargs)

    def test_hidden_page_does_not_poll_or_start_browser(self):
        with mock.patch.object(self.frame, "check_wps_browser") as check:
            self.frame._poll_login_status()
        check.assert_not_called()
        self.assertIsNone(self.frame._login_poll_after_id)

    def test_map_schedules_check_and_destroy_cancels_it(self):
        self.frame._schedule_login_check(SimpleNamespace(widget=self.frame))
        callback = self.frame._login_poll_after_id
        self.assertIsNotNone(callback)
        self.frame.destroy()
        with self.assertRaises(tk.TclError):
            self.root.tk.call("after", "info", callback)
        del self.frame


if __name__ == "__main__":
    unittest.main()
