from __future__ import annotations

import gc
from pathlib import Path
import tempfile
import sys
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolbox.diagnostics import DiagnosticCheck, DiagnosticLevel, DiagnosticReport
from toolbox.runtime import ToolPaths


class LoginFlowTests(unittest.TestCase):
    def test_manual_login_waits_for_browser_without_reading_stdin(self):
        from toolbox_shared import auth_loader as toolbox_auth_loader
        check = mock.Mock(side_effect=(False, True))
        with mock.patch("builtins.input", side_effect=AssertionError("No terminal input in GUI")), mock.patch.object(toolbox_auth_loader.time, "sleep"):
            toolbox_auth_loader.wait_for_login(check)
        self.assertEqual(check.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, "等待登录超时"):
            toolbox_auth_loader.wait_for_login(lambda: False, timeout_seconds=0)

    def test_group_login_saves_valid_session_without_requiring_report_navigation(self):
        from toolbox.runtime import load_module
        module = load_module("group_central_login_test", Path(__file__).resolve().parents[2] / "集团每日上传/upload_daily_report.py")
        context = SimpleNamespace(new_page=mock.Mock(side_effect=AssertionError("Login must not depend on report access")))
        with mock.patch.object(module, "is_logged_in", return_value=True), mock.patch.object(module, "save_auth_state") as save, mock.patch("builtins.input", side_effect=AssertionError("No stdin")):
            module.interactive_login(context, module.Config())
        save.assert_called_once_with(context)

    def _standalone_auth(self, *, auto_login: bool):
        context = mock.Mock()
        auth = SimpleNamespace(
            AuthStateLock=mock.MagicMock(),
            launch_context=mock.Mock(return_value=context),
            try_auto_login=mock.Mock(return_value=auto_login),
            is_logged_in=mock.Mock(side_effect=(False, True)),
            save_auth_state=mock.Mock(),
        )
        return auth, context

    def test_standalone_login_prefers_saved_credentials(self):
        from toolbox_shared import auth_loader as toolbox_auth_loader
        auth, context = self._standalone_auth(auto_login=True)
        log = mock.Mock()
        with mock.patch("playwright.sync_api.sync_playwright"):
            toolbox_auth_loader.standalone_login(
                auth, SimpleNamespace(username="user", password="secret"),
                app_url="https://example.test", log=log)
        auth.try_auto_login.assert_called_once_with(context, "user", "secret")
        auth.save_auth_state.assert_called_once_with(context)
        context.new_page.assert_not_called()
        context.close.assert_called_once_with()
        log.assert_called_once_with("自动登录成功，独立登录态已保存。")

    def test_standalone_login_reuses_valid_session_without_submitting_password(self):
        from toolbox_shared import auth_loader
        auth, context = self._standalone_auth(auto_login=True)
        auth.is_logged_in.side_effect = None
        auth.is_logged_in.return_value = True
        with mock.patch("playwright.sync_api.sync_playwright"):
            auth_loader.standalone_login(auth, SimpleNamespace(username="user", password="secret"),
                                         app_url="https://example.test", log=mock.Mock())
        auth.try_auto_login.assert_not_called()
        context.new_page.assert_not_called()
        context.close.assert_called_once()

    def test_standalone_login_falls_back_to_manual_login(self):
        from toolbox_shared import auth_loader as toolbox_auth_loader
        auth, context = self._standalone_auth(auto_login=False)
        page = mock.Mock()
        context.new_page.return_value = page
        log = mock.Mock()
        with mock.patch("playwright.sync_api.sync_playwright"):
            toolbox_auth_loader.standalone_login(
                auth, SimpleNamespace(username="", password=""),
                app_url="https://example.test", log=log, label="现货价格模块独立")
        page.goto.assert_called_once_with("https://example.test", wait_until="domcontentloaded", timeout=60_000)
        auth.save_auth_state.assert_called_once_with(context)
        page.close.assert_called_once_with()
        context.close.assert_called_once_with()
        log.assert_called_with("登录成功，现货价格模块独立登录态已保存。")

class AccountPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        gc.collect()

    def test_six_pages_and_account_panel_have_no_manual_login_controls(self):
        from toolbox.app import ToolboxApp
        self.root.destroy()
        with tempfile.TemporaryDirectory() as directory:
            app = ToolboxApp(workspace=Path(directory))
            app.withdraw()
            self.root = app
            panel = app.credentials_panel
            panel.username_var.set("test-account")
            panel.password_var.set("test-password")
            self.assertFalse(hasattr(panel, "login_selector"))
            self.assertFalse(hasattr(panel, "login_button"))
            modules = {
                "导出上网电量": "上网电量抓取",
                "风力预测截图": "风力预测截图",
                "上传集团每日数据": "集团每日上传",
                "市场表更新": "市场表更新",
                "现货价格分析表更新": "现货价格更新",
                "交易系统信息更新": "交易系统信息更新",
            }
            for name in modules:
                with self.subTest(name=name):
                    app.show_page(name)
                    page = app.pages[name]
                    self.assertFalse(hasattr(page, "login_button"))
                    self.assertEqual(app.current_page, name)
            app.geometry("980x640")
            app.update()
            self.assertGreater(app.task_center.listbox.winfo_height(), 20)
            self.assertLessEqual(panel.winfo_rooty() + panel.winfo_height(), app.status_bar.winfo_rooty())

    def test_diagnostics_hide_normal_checks_and_show_all_issues(self):
        from toolbox.ui.diagnostic_center import DiagnosticCenterPanel
        with tempfile.TemporaryDirectory() as directory:
            panel = DiagnosticCenterPanel(self.root, ToolPaths(Path(directory)))
            panel.pack()
            healthy = DiagnosticReport((DiagnosticCheck("Python", True, "正常"),))
            panel._render(healthy)
            self.assertEqual(panel.check_list.size(), 0)
            self.assertEqual(panel.checks_frame.winfo_manager(), "")
            self.assertEqual(panel.detail_label.winfo_manager(), "")
            issues = tuple(DiagnosticCheck(f"问题模块{index}", False, "缺少文件") for index in range(7))
            warning = DiagnosticCheck("私有上传", True, "未安装 Node", level=DiagnosticLevel.WARNING)
            panel._render(DiagnosticReport((*healthy.checks, *issues, warning)))
            self.assertEqual(panel.check_list.size(), 8)
            self.assertEqual(panel.checks_frame.winfo_manager(), "pack")
            self.assertTrue(all(check.status is not DiagnosticLevel.OK for check in panel._visible_checks))
            panel._render(healthy)
            self.assertEqual(panel.check_list.size(), 0)
            self.assertEqual(panel.checks_frame.winfo_manager(), "")
            panel.destroy()


if __name__ == "__main__":
    unittest.main()
