from __future__ import annotations

import json
import tempfile
import sys
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from playwright.sync_api import Error as PlaywrightError
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from toolbox_shared import qingneng_login as login
from toolbox_shared.credentials import CredentialStore
from toolbox.runtime import load_module


def response(payload, status=200):
    return SimpleNamespace(status=status, json=lambda: payload)


class LoginPolicyTests(unittest.TestCase):
    def test_api_success_skips_page_navigation_in_both_consumers(self):
        root = Path(__file__).resolve().parents[2]
        for index, script in enumerate(("上网电量抓取/export_online_energy.py", "集团每日上传/upload_daily_report.py")):
            auth = load_module(f"fast_login_{index}", root / script)
            context = mock.Mock()
            context.request.get.return_value = response({"retCode": "T200", "data": "key"})
            context.request.post.return_value = response({"retCode": "T200"})
            with mock.patch.object(auth, "rsa_encrypt_password", return_value="encrypted"), \
                    mock.patch.object(auth, "is_logged_in", return_value=True), \
                    mock.patch.object(auth, "save_auth_state") as save:
                self.assertTrue(auth.try_api_login(context, "account", "secret"))
                save.assert_called_once_with(context)
            context.new_page.assert_not_called()
            context.request.post.assert_called_once()

    def test_missing_application_session_navigates_once_without_networkidle(self):
        auth = SimpleNamespace(BASE_URL="https://example.test", rsa_encrypt_password=mock.Mock(),
                               is_logged_in=mock.Mock(side_effect=(False, True)), save_auth_state=mock.Mock())
        context = mock.Mock()
        with mock.patch.object(login, "submit_api_login"):
            self.assertTrue(login.api_login(auth, context, "account", "secret", app_url="https://example.test/app"))
        page = context.new_page.return_value
        page.goto.assert_called_once_with("https://example.test/app", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_load_state.assert_not_called()
        page.close.assert_called_once()

    def test_navigation_failure_closes_page(self):
        context = mock.Mock()
        context.new_page.return_value.goto.side_effect = PlaywrightError("offline")
        auth = SimpleNamespace(BASE_URL="https://example.test", rsa_encrypt_password=mock.Mock(),
                               is_logged_in=mock.Mock(return_value=False))
        with mock.patch.object(login, "submit_api_login"), self.assertRaises(PlaywrightError):
            login.api_login(auth, context, "account", "secret", app_url="https://example.test/app")
        context.new_page.return_value.close.assert_called_once()

    def test_cleanup_failure_keeps_first_navigation_error(self):
        context = mock.Mock()
        context.new_page.return_value.goto.side_effect = PlaywrightError("first navigation error")
        context.new_page.return_value.close.side_effect = PlaywrightError("cleanup error")
        auth = SimpleNamespace(BASE_URL="https://example.test", rsa_encrypt_password=mock.Mock(),
                               is_logged_in=mock.Mock(return_value=False))
        with mock.patch.object(login, "submit_api_login"), self.assertRaisesRegex(PlaywrightError, "first navigation"):
            login.api_login(auth, context, "account", "secret", app_url="https://example.test/app")

    def test_rejected_password_is_not_retried_or_echoed(self):
        request = mock.Mock()
        request.get.return_value = response({"retCode": "T200", "data": "key"})
        request.post.return_value = response({"retCode": "F401", "retMsg": "密码错误 secret"})
        with self.assertRaises(login.LoginError) as caught:
            login.submit_api_login(request, "account", "secret", encrypt=lambda *_: "cipher")
        self.assertEqual(caught.exception.kind, "credentials")
        self.assertNotIn("secret", str(caught.exception))
        request.post.assert_called_once()

    def test_network_errors_are_redacted_and_only_key_get_is_retried(self):
        request = mock.Mock()
        request.get.side_effect = [PlaywrightError("secret call log"), response({"retCode": "T200", "data": "key"})]
        request.post.side_effect = PlaywrightError("secret query")
        with mock.patch.object(login.time, "sleep"), self.assertRaises(login.LoginError) as caught:
            login.submit_api_login(request, "account", "secret", encrypt=lambda *_: "cipher")
        self.assertEqual(caught.exception.kind, "network")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(request.get.call_count, 2)
        request.post.assert_called_once()

    def test_only_explicit_verification_uses_ui_and_keeps_first_error(self):
        for kind in ("credentials", "network", "server", "verification"):
            auth = SimpleNamespace(log=mock.Mock(), try_api_login=mock.Mock(side_effect=login.LoginError(kind, kind)),
                                   try_ui_login=mock.Mock(return_value=False))
            with self.assertRaisesRegex(login.LoginError, kind):
                login.auto_login(auth, SimpleNamespace(_qingneng_headless=False), "account", "secret")
            self.assertEqual(auth.try_ui_login.call_count, int(kind == "verification"))

    def test_probe_uses_lightweight_endpoint_and_distinguishes_offline(self):
        request = mock.Mock()
        request.get.return_value = response({"retCode": "T200", "data": {"tenantId": "unit"}})
        self.assertTrue(login.check_session(request))
        self.assertEqual(request.get.call_count, 1)
        self.assertIn("/usercenter/", request.get.call_args.args[0])
        request.get.side_effect = PlaywrightError("offline")
        with self.assertRaises(login.LoginError) as caught:
            login.check_session(request)
        self.assertEqual(caught.exception.kind, "network")

    def test_atomic_cookie_save_preserves_last_session_on_replace_failure(self):
        root = Path(__file__).resolve().parents[2]
        for index, script in enumerate(("上网电量抓取/export_online_energy.py", "集团每日上传/upload_daily_report.py")):
            auth = load_module(f"atomic_login_{index}", root / script)
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "auth_state.json"
                target.write_text('{"cookies":[]}')
                with mock.patch.object(auth, "AUTH_STATE_PATH", target), mock.patch.object(auth, "mark_browser_session") as mark:
                    context = SimpleNamespace(storage_state=lambda: {"cookies": [{"name": "sample"}]})
                    with mock.patch.object(Path, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
                        auth.save_auth_state(context)
                    self.assertEqual(json.loads(target.read_text()), {"cookies": []})
                    mark.assert_not_called()
                    auth.save_auth_state(context)
                    self.assertEqual(json.loads(target.read_text())["cookies"][0]["name"], "sample")
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    def test_verification_does_not_mark_another_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(Path(folder))
            first = store.save("account", "secret")
            self.assertTrue(store.mark_verified(first["revision"]))
            self.assertIn("verified_at", store.load())
            second = store.save("new", "new secret")
            self.assertFalse(store.mark_verified(first["revision"]))
            self.assertEqual(store.load(), second)

    def test_network_login_error_keeps_category_across_worker_transport(self):
        from toolbox.tasks.errors import ErrorCategory, RemoteTaskError, classify_error, error_detail
        exc = login.LoginError("network", "登录服务暂不可达")
        detail = error_detail(exc, tool_id="market-table", action="run")
        self.assertEqual(detail["stage"], "login")
        self.assertEqual(detail["tool_id"], "market-table")
        remote = RemoteTaskError(str(exc), details=detail)
        self.assertEqual(classify_error(remote).category, ErrorCategory.NETWORK)


class AccountVerificationTests(unittest.TestCase):
    def test_failed_verification_preserves_account_and_success_saves_the_draft(self):
        from toolbox.ui.credentials_panel import CredentialsPanel
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                store = CredentialStore(Path(folder))
                before = store.save("original", "old-secret")
                panel = CredentialsPanel(root, Path(folder))
                panel.username_var.set("edited")
                panel.password_var.set("new-secret")
                with mock.patch("toolbox.ui.credentials_panel.verify_credentials",
                                side_effect=[login.LoginError("credentials", "账号或密码被拒绝"), None]):
                    for success in (False, True):
                        panel.verify()
                        deadline = time.monotonic() + 3
                        while panel._verification_id is not None and time.monotonic() < deadline:
                            root.update()
                            time.sleep(0.01)
                        if success:
                            self.assertEqual(store.load()["username"], "edited")
                            self.assertIn("verified_at", store.load())
                            self.assertFalse(panel.dirty)
                        else:
                            self.assertEqual(store.load(), before)
                            self.assertTrue(panel.dirty)
                panel.destroy()
        finally:
            root.destroy()

    def test_task_start_commits_draft_and_incomplete_draft_blocks_process(self):
        from toolbox.ui.credentials_panel import CredentialsPanel
        from toolbox.widgets import ToolPage
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root.credentials_panel = panel = CredentialsPanel(root, Path(folder))
                registry = mock.Mock()
                page = ToolPage(root, registry=registry)
                page.tool_id = "online-energy"
                panel.username_var.set("draft")
                panel.password_var.set("secret")
                registry.start_callable.side_effect = lambda *_args, **_kwargs: self.assertEqual(panel.store.load()["username"], "draft")
                page.run_in_thread(lambda: None, status="test")
                registry.start_callable.assert_called_once()
                self.assertFalse(panel.dirty)
                page.busy = False
                panel.password_var.set("")
                with mock.patch("toolbox.widgets.messagebox.showwarning"):
                    page.run_process(["unused"], cwd=Path(folder), status="test")
                registry.start_process.assert_not_called()
                self.assertEqual(panel.store.load()["password"], "secret")
                page.destroy()
                panel.destroy()
        finally:
            root.destroy()

    def test_manual_verification_exposes_only_the_current_tool_login(self):
        from toolbox.ui.credentials_panel import CredentialsPanel
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                panel = CredentialsPanel(root, Path(folder))
                panel.username_var.set("account")
                panel.password_var.set("secret")
                panel.save()
                root._visible_page = SimpleNamespace(tool_id="online-energy", start_login=mock.Mock())
                panel._verification_queue.put((("account", "secret"), panel.store.load()["revision"], ("需要人工验证", "verification")))
                panel._poll_verification()
                self.assertEqual(panel.manual_login_button.winfo_manager(), "pack")
                panel.manual_login_button.invoke()
                root._visible_page.start_login.assert_called_once()
                panel.destroy()
        finally:
            root.destroy()

    def test_verification_is_async_and_late_result_cannot_validate_new_draft(self):
        from toolbox.ui.credentials_panel import CredentialsPanel
        import threading
        root = tk.Tk()
        root.withdraw()
        done = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as folder:
                panel = CredentialsPanel(root, Path(folder))
                panel.username_var.set("account")
                panel.password_var.set("secret")
                with mock.patch("toolbox.ui.credentials_panel.verify_credentials", side_effect=lambda *_: done.wait(3)):
                    panel.verify()
                    panel.password_var.set("edited secret")
                    done.set()
                    deadline = time.monotonic() + 3
                    while panel._verification_id is not None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertIn("重新验证", panel.status_var.get())
                self.assertNotIn("verified_at", panel.store.load())
                self.assertEqual(panel.store.load(), {})
                panel.destroy()
        finally:
            done.set()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
