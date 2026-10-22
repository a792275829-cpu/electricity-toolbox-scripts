from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from playwright.sync_api import sync_playwright

from toolbox_auth_loader import load_login_module


class FreshBrowserStateTests(unittest.TestCase):
    def test_new_tool_profiles_persist_their_own_session_and_release_locks(self):
        root = Path(__file__).resolve().parents[2]
        tools = ("市场表更新", "现货价格更新", "交易系统信息更新", "功率调整收益统计表更新")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "TOOLBOX_CODE_ROOT": str(root), "TOOLBOX_DATA_ROOT": directory, "TOOLBOX_PORTABLE": "1",
        }), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                self.skipTest("当前 Python 环境缺少匹配的 Playwright 浏览器")
            modules = [load_login_module(root, module_name=f"fresh_browser_state_{i}", state_dir=root / tool)
                for i, tool in enumerate(tools)]
            with ExitStack() as locks:
                for index, auth in enumerate(modules):
                    locks.enter_context(auth.AuthStateLock(timeout_seconds=0))
                    context = auth.launch_context(playwright, headless=True)
                    try:
                        self.assertEqual(context.cookies(), [])
                        context.add_cookies([dict(name="review-session", value=str(index), domain="review.invalid", path="/")])
                        auth.save_auth_state(context)
                    finally:
                        context.close()
                for index, auth in enumerate(modules):
                    context = auth.launch_context(playwright, headless=True)
                    try:
                        cookies = context.cookies("https://review.invalid")
                        self.assertEqual([(c["name"], c["value"]) for c in cookies], [("review-session", str(index))])
                    finally:
                        context.close()
            self.assertEqual(len({auth.PROFILE_DIR for auth in modules}), len(tools))
            for auth in modules:
                self.assertTrue(auth.AUTH_STATE_PATH.is_file())
                self.assertTrue(auth.PROFILE_DIR.is_dir())
                self.assertFalse(auth.AUTH_LOCK_PATH.exists())
