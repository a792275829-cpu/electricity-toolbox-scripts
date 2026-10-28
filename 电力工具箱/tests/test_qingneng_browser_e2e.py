"""Real browser/request/CLI integration against a local login service; no business writes."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from playwright.sync_api import sync_playwright
from toolbox_shared import credentials
from toolbox_shared.auth_loader import load_login_module
from toolbox_shared.qingneng_login import verify_credentials
from toolbox.runtime import load_module


class BrowserLoginIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.posts = 0
        self.pages = 0
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, payload, cookie=False):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if cookie:
                    self.send_header("Set-Cookie", "session=fixture; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                if self.path.endswith("/publicKey"):
                    self.reply({"retCode": "T200", "data": public})
                elif self.path.endswith("/info"):
                    valid = "session=fixture" in self.headers.get("Cookie", "")
                    self.reply({"retCode": "T200" if valid else "F401", "data": {"tenantId": "fixture"} if valid else {}})
                else:
                    owner.pages += 1
                    self.reply({})

            def do_POST(self):
                params = parse_qs(urlsplit(self.path).query)
                password = owner.key.decrypt(base64.b64decode(params["password"][0]), padding.PKCS1v15()).decode()
                valid = params.get("username") == ["fixture"] and password == "fixture-secret"
                owner.posts += 1
                self.reply({"retCode": "T200" if valid else "F401", "retMsg": "" if valid else "密码错误"}, cookie=valid)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.proxy_patch = mock.patch.dict(os.environ, {name: "" for name in
            ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")})
        self.proxy_patch.start()

    def tearDown(self):
        self.proxy_patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_python_tools_and_account_verification_use_real_isolated_sessions(self):
        verify_credentials("fixture", "fixture-secret", base_url=self.url)
        store = credentials.CredentialStore(self.folder)
        store.save("fixture", "fixture-secret")
        with mock.patch.object(credentials, "credentials_path", return_value=store.path), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                self.skipTest("当前环境缺少 Playwright 浏览器")
            auths = [load_login_module(ROOT, module_name="local_login_e2e", state_dir=self.folder / "online"),
                     load_module("local_group_login_e2e", ROOT / "集团每日上传/upload_daily_report.py")]
            for index, auth in enumerate(auths):
                state = self.folder / str(index)
                auth.PROFILE_DIR = state / ".browser-profile"
                auth.AUTH_STATE_PATH = state / "auth_state.json"
                auth.AUTH_LOCK_PATH = state / "auth_state.json.lock"
                auth.BASE_URL = self.url
                with auth.AuthStateLock(timeout_seconds=0):
                    context = auth.launch_context(playwright, headless=True)
                    try:
                        self.assertEqual(context.cookies(), [])
                        auth.ensure_login(context, auth.Config(username="fixture", password="fixture-secret"))
                        self.assertTrue(auth.is_logged_in(context))
                    finally:
                        context.close()
                    posts = self.posts
                    context = auth.launch_context(playwright, headless=True)
                    try:
                        auth.ensure_login(context, auth.Config(username="fixture", password="fixture-secret"))
                        self.assertEqual(self.posts, posts)
                    finally:
                        context.close()
                self.assertFalse(auth.AUTH_LOCK_PATH.exists())
                self.assertEqual(auth.AUTH_STATE_PATH.stat().st_mode & 0o777, 0o600)
                saved = json.loads(auth.AUTH_STATE_PATH.read_text())
                self.assertEqual(saved["cookies"][0]["value"], "fixture")
        self.assertEqual(self.posts, 3)
        self.assertEqual(self.pages, 0)

    def test_optional_node_cli_saves_and_reuses_its_own_session(self):
        node = shutil.which("node")
        tool = ROOT / "电力工具脚本/private-data-uploader-tool"
        if node is None or not (tool / "node_modules/playwright").exists():
            self.skipTest("私有上传工具的可选 Node 依赖未安装")
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
        if not Path(executable).is_file():
            self.skipTest("当前环境缺少 Playwright 浏览器")
        config = dict(sourceDir=str(self.folder), loginConfigPath=str(self.folder / "login.json"),
                      authStatePath=str(self.folder / "auth_state.json"), userDataDir=str(self.folder / "profile"),
                      chromeExecutablePath=executable, baseUrl=self.url, headless=True)
        (self.folder / "login.json").write_text(json.dumps(dict(username="fixture", password="fixture-secret")))
        config_path = self.folder / "upload.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, TOOLBOX_CODE_ROOT=str(ROOT), TOOLBOX_DATA_ROOT=str(self.folder / "data"))
        for _ in range(2):
            result = subprocess.run([node, str(tool / "scripts/upload-private-data.mjs"), "--login", "--config", str(config_path)],
                                    cwd=tool, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("fixture-secret", result.stdout + result.stderr)
            self.assertFalse((self.folder / "auth_state.json.lock").exists())
        self.assertEqual(self.posts, 1)
        self.assertEqual((self.folder / "auth_state.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.folder / "auth_state.json").read_text())["cookies"][0]["value"], "fixture")


if __name__ == "__main__":
    unittest.main()
