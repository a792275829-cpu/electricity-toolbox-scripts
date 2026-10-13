from __future__ import annotations

import gc
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import toolbox_credentials as accounts
from toolbox.runtime import load_module


class CredentialStoreTests(unittest.TestCase):
    def test_migrate_defaults_and_save_without_changing_legacy_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "上网电量抓取/config.json"
            legacy.parent.mkdir()
            raw = {"username": "original", "password": " original secret ", "outputDir": "输出", "headless": False}
            legacy.write_text(json.dumps(raw), encoding="utf-8")
            store = accounts.CredentialStore(root)
            initial = store.load()
            self.assertEqual(initial["password"], raw["password"])
            self.assertEqual(accounts.read_json(legacy), raw)
            saved = store.save(" new-account ", " new secret ")
            self.assertEqual(accounts.CredentialStore(root).load(), saved)
            self.assertEqual(saved["username"], "new-account")
            self.assertEqual(saved["password"], " new secret ")
            self.assertNotEqual(initial["revision"], saved["revision"])
            self.assertEqual(store.save("new-account", " new secret ")["revision"], saved["revision"])
            result = accounts.read_login_config(legacy, workspace=root)
            self.assertEqual(result["username"], "new-account")
            self.assertEqual(result["outputDir"], "输出")
            self.assertFalse(result["headless"])
            if os.name != "nt":
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(store.path.parent.glob("*.tmp")), [])

    def test_incomplete_edits_and_failed_replace_preserve_saved_account(self):
        with tempfile.TemporaryDirectory() as directory:
            store = accounts.CredentialStore(Path(directory))
            before = store.save("original", "secret")
            with self.assertRaises(ValueError):
                store.save("new", "")
            with mock.patch.object(Path, "replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    store.save("new", "new secret")
            self.assertEqual(store.load(), before)
            self.assertEqual(list(store.path.parent.glob("*.tmp")), [])

    def test_invalid_json_error_does_not_echo_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"password":"sensitive-value"', encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                accounts.read_json(path)
            self.assertNotIn("sensitive-value", str(caught.exception))


class ConsumerTests(unittest.TestCase):
    def test_python_consumers_reload_account_and_preserve_separate_state(self):
        scripts = (
            "上网电量抓取/export_online_energy.py",
            "每日生产经营情况汇报自动生成工具/scripts/export_online_energy.py",
            "集团每日上传/upload_daily_report.py",
            "市场表更新/update_market_table.py",
            "现货价格更新/update_spot_prices.py",
            "交易系统信息更新/update_transaction_info.py",
            "风力预测截图/wind_forecast_screenshot.py",
            "广东电价预测/update_gdfire_data.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = accounts.CredentialStore(root)
            store.save("first-account", "first-secret")
            with mock.patch.object(accounts, "credentials_path", return_value=store.path):
                modules = [load_module(f"credentials_consumer_{index}", ROOT / script) for index, script in enumerate(scripts)]
                snapshots = []
                for module in modules:
                    auth = getattr(module, "online_export_module", getattr(module, "_export", module))
                    snapshots.append(tuple(getattr(auth, name, None) for name in ("AUTH_STATE_PATH", "AUTH_LOCK_PATH", "PROFILE_DIR")))
                for username, password in (("first-account", "first-secret"), ("second-account", "second-secret")):
                    store.save(username, password)
                    for index, module in enumerate(modules):
                        with self.subTest(script=scripts[index], account=username):
                            config = module.load_config()
                            actual = config if isinstance(config, dict) else asdict(config)
                            self.assertEqual((actual["username"], actual["password"]), (username, password))
                            auth = getattr(module, "online_export_module", getattr(module, "_export", module))
                            self.assertEqual(snapshots[index], tuple(getattr(auth, name, None) for name in ("AUTH_STATE_PATH", "AUTH_LOCK_PATH", "PROFILE_DIR")))

    def test_browser_session_revision_and_lock_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = accounts.CredentialStore(root)
            store.save("account", "secret")
            auth = load_module("credentials_browser_auth", ROOT / "上网电量抓取/export_online_energy.py")
            state = root / "tool/auth_state.json"
            state.parent.mkdir()
            auth.AUTH_STATE_PATH = state
            auth.AUTH_LOCK_PATH = Path(str(state) + ".lock")
            context = SimpleNamespace(clear_cookies=mock.Mock(), storage_state=lambda: {"cookies": []})
            with mock.patch.object(accounts, "credentials_path", return_value=store.path):
                with auth.AuthStateLock():
                    self.assertTrue(accounts.prepare_browser_session(context, state))
                    context.clear_cookies.assert_called_once()
                    # Even a stale session reported as valid must log in again.
                    with mock.patch.object(auth, "is_logged_in", return_value=True) as check, mock.patch.object(auth, "try_auto_login", return_value=True) as login:
                        auth.ensure_login(context, auth.Config(username="account", password="secret"))
                        check.assert_not_called()
                        login.assert_called_once_with(context, "account", "secret")
                    context._qingneng_login_credentials = ("account", "secret")
                    auth.save_auth_state(context)
                    self.assertFalse(accounts.prepare_browser_session(context, state))
                    accounts.session_marker_path(state).write_text("broken metadata", encoding="utf-8")
                    self.assertTrue(accounts.prepare_browser_session(context, state))
                    store.save("new-account", "new-secret")
                    self.assertTrue(accounts.prepare_browser_session(context, state))
                    # A task holding old credentials must not mark the new account as logged in.
                    context._qingneng_login_credentials = ("account", "secret")
                    auth.save_auth_state(context)
                    self.assertTrue(accounts.prepare_browser_session(context, state))
                    context._qingneng_login_credentials = ("new-account", "new-secret")
                    auth.save_auth_state(context)
                    self.assertFalse(accounts.prepare_browser_session(context, state))
                self.assertFalse(auth.AUTH_LOCK_PATH.exists())


class CredentialsPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        gc.collect()

    def test_prefill_auto_save_visibility_and_reopen(self):
        from toolbox.ui.credentials_panel import CredentialsPanel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = accounts.CredentialStore(root)
            store.save("original", "old-secret")
            panel = CredentialsPanel(self.root, root)
            panel.pack()
            self.assertEqual(panel.username_var.get(), "original")
            self.assertEqual(panel.password_var.get(), "old-secret")
            self.assertEqual(panel.password_entry.cget("show"), "")
            panel.show_password_var.set(False)
            panel._toggle_password()
            self.assertNotEqual(panel.password_entry.cget("show"), "")
            panel.username_var.set("edited")
            panel.password_var.set("new-secret")
            self.root.after(750, self.root.quit)
            self.root.mainloop()
            self.assertEqual(store.load()["username"], "edited")
            self.assertEqual(store.load()["password"], "new-secret")
            self.assertEqual(panel.status_var.get(), "已保存到本机")
            self.assertFalse(panel.dirty)
            panel.destroy()
            reopened = CredentialsPanel(self.root, root)
            self.assertEqual(reopened.password_var.get(), "new-secret")
            reopened.destroy()

    def test_app_places_account_below_tasks_without_clipping(self):
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        with tempfile.TemporaryDirectory() as directory:
            app = ToolboxApp(workspace=Path(directory), page_factories={name: lambda parent, _paths, _registry: tk.Frame(parent) for name in PAGE_NAMES}, lazy_pages=True)
            self.root = app
            app.update()
            panel = app.credentials_panel
            self.assertEqual(panel.master, app.task_center.master)
            self.assertLess(int(app.task_center.grid_info()["row"]), int(panel.grid_info()["row"]))
            self.assertLess(int(panel.grid_info()["row"]), int(app.diagnostic_center.grid_info()["row"]))
            self.assertGreater(app.task_center.listbox.winfo_height(), 20)
            self.assertLessEqual(panel.winfo_rooty() + panel.winfo_height(), app.status_bar.winfo_rooty())
            self.assertLessEqual(app.diagnostic_center.winfo_rooty() + app.diagnostic_center.winfo_height(), app.status_bar.winfo_rooty())
            panel.username_var.set("last-edit")
            panel.password_var.set("secret")
            # Close before the debounce fires still persists a complete edit.
            app._on_close()
            self.assertEqual(accounts.CredentialStore(Path(directory)).load()["username"], "last-edit")
            self.root = tk.Tk()
            self.root.withdraw()


if __name__ == "__main__":
    unittest.main()
