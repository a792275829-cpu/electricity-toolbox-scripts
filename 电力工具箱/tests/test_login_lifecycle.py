from __future__ import annotations

import json
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
from toolbox_shared.auth_requests import AuthenticatedContext
from toolbox_shared.qingneng_login import LoginError
from toolbox_shared.credentials import CredentialStore
from toolbox.tasks import CancellationToken, ProcessRunner, ProcessExecutionError


def response(status=200, payload=None):
    return SimpleNamespace(status=status, json=lambda: payload or {"retCode": "T200", "data": {}})


class ReadRecoveryTests(unittest.TestCase):
    def context(self):
        raw = SimpleNamespace(request=mock.Mock(), _qingneng_credentials=("fixture", "secret"),
                              _qingneng_revision="revision", _qingneng_tenant="unit")
        auth = SimpleNamespace(BASE_URL="https://example.test", try_auto_login=mock.Mock(return_value=True),
                               log=mock.Mock(), parse_json_response=mock.Mock())
        return AuthenticatedContext(raw, auth), raw, auth

    def test_read_reauthenticates_once_and_restores_company(self):
        context, raw, auth = self.context()
        valid = response()
        raw.request.get.side_effect = [response(401), valid, valid]
        with mock.patch("toolbox_shared.credentials.read_json", return_value={"revision": "revision"}):
            self.assertIs(context.request.get("https://example.test/gdfire/api/unit/list"), valid)
        auth.try_auto_login.assert_called_once_with(context, "fixture", "secret")
        self.assertEqual(raw.request.get.call_count, 3)
        self.assertEqual(raw.request.get.call_args_list[1].kwargs["params"], {"tenantId": "unit"})

    def test_upload_post_and_unknown_get_are_never_replayed(self):
        for method, path in (("post", "/gdfire/api/import"), ("get", "/usercenter/web/switchTenant")):
            context, raw, auth = self.context()
            getattr(raw.request, method).return_value = response(401)
            getattr(context.request, method)("https://example.test" + path)
            getattr(raw.request, method).assert_called_once()
            auth.try_auto_login.assert_not_called()

    def test_repeated_expiry_stops_after_one_retry(self):
        context, raw, auth = self.context()
        raw._qingneng_tenant = None
        raw.request.get.return_value = response(401)
        with mock.patch("toolbox_shared.credentials.read_json", return_value={"revision": "revision"}), self.assertRaises(LoginError):
            context.request.get("https://example.test/gdfire/api/unit/list")
        self.assertEqual(raw.request.get.call_count, 2)
        auth.try_auto_login.assert_called_once()

    def test_account_edit_prevents_reauthenticating_old_task(self):
        context, raw, auth = self.context()
        raw.request.get.return_value = response(401)
        with mock.patch("toolbox_shared.credentials.read_json", return_value={"revision": "new"}), self.assertRaises(LoginError) as caught:
            context.request.get("https://example.test/gdfire/api/unit/list")
        self.assertEqual(caught.exception.kind, "account_changed")
        auth.try_auto_login.assert_not_called()


class CliPropagationTests(unittest.TestCase):
    def test_source_debug_interpreter_with_chinese_name_is_wrapped(self):
        from toolbox_shared.cli_login import script_command
        with mock.patch("toolbox_shared.cli_login.sys.executable", "/tmp/电力工具箱源码调试"):
            command = script_command(["/tmp/电力工具箱源码调试", "/tmp/example.py", "--login"])
        self.assertEqual(Path(command[1]).name, "cli_login.py")
        self.assertEqual(command[-2:], ["/tmp/example.py", "--login"])

    def fixture(self, root, caught):
        script = root / "上网电量抓取/export_online_energy.py"
        script.parent.mkdir()
        text = 'from toolbox_shared.qingneng_login import LoginError\n'
        if caught:
            text += 'from toolbox_shared.cli_login import emit_login_error\ntry:\n raise LoginError("verification", "fixture captcha")\nexcept LoginError as exc:\n emit_login_error(exc)\n raise SystemExit(1)\n'
        else:
            text += 'raise LoginError("verification", "fixture captcha")\n'
        script.write_text(text)
        return script

    def test_source_process_receives_login_type_from_caught_and_uncaught_cli(self):
        for caught in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                script = self.fixture(root, caught)
                with self.assertRaises(ProcessExecutionError) as error:
                    ProcessRunner().run([sys.executable, str(script)], cwd=root, env=None,
                                        token=CancellationToken(), on_output=lambda _: None)
                self.assertEqual(error.exception.error_detail["login_kind"], "verification")

    def test_desktop_service_keeps_inner_cli_type_through_task_worker(self):
        from toolbox.service import ToolboxService
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root, True)
            service = ToolboxService(root)
            try:
                task = service.call("start", dict(request_id="login-fixture", tool_id="online-energy", action="run", values={"run_date": "2026-10-28"}))
                deadline = time.monotonic() + 10
                while service.registry.has_running_tasks() and time.monotonic() < deadline:
                    time.sleep(0.02)
                error = service.registry.snapshot(task).error
                self.assertIsNotNone(error)
                self.assertEqual(error.error_detail["login_kind"], "verification")
                self.assertEqual(error.error_detail["tool_id"], "online-energy")
                self.assertEqual(error.error_detail["stage"], "login")
            finally:
                service.registry.terminate_all()


class DraftLifecycleTests(unittest.TestCase):
    def test_failed_draft_is_not_saved_on_close_and_offline_task_is_not_blocked(self):
        from toolbox.app import ToolboxApp, PAGE_NAMES
        from toolbox.widgets import ToolPage
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(Path(folder))
            before = store.save("original", "secret")
            app = ToolboxApp(workspace=Path(folder), page_factories={name: lambda parent, *_: tk.Frame(parent) for name in PAGE_NAMES})
            app.withdraw()
            panel = app.credentials_panel
            panel.username_var.set("rejected")
            panel.password_var.set("")
            page = ToolPage(app, registry=mock.Mock())
            page.tool_id = "trade-analysis"
            page.run_in_thread(lambda: None, status="local")
            page.registry.start_callable.assert_called_once()
            panel.password_var.set("bad-secret")
            panel._verification_queue.put((("rejected", "bad-secret"), before["revision"], ("rejected", "credentials")))
            panel._poll_verification()
            app._on_close()
            self.assertEqual(store.load(), before)

    def test_offline_modes_and_wps_do_not_require_qingneng(self):
        from toolbox.page_contracts import requires_qingneng
        for tool, values in (("daily-pdf-parser", {"mode": "parse"}), ("summary-upload", {"mode": "summary"}),
                             ("report", {"fetch_data": False}), ("wps-writer", {})):
            self.assertFalse(requires_qingneng(tool, "run", values))
        self.assertTrue(requires_qingneng("daily-pdf-parser", "run", {"mode": "upload"}))
        self.assertTrue(requires_qingneng("group-upload", "run"))

    def test_every_qingneng_tool_has_manual_login_action(self):
        from toolbox.service_catalog import desktop_catalog
        from toolbox.runtime import ToolPaths
        from toolbox_shared.login_tools import STATE_DIRS
        tools = {tool["id"]: tool for tool in desktop_catalog(ToolPaths(ROOT))}
        for tool in {*STATE_DIRS, "group-upload", "summary-upload"}:
            self.assertIn("login", [action["id"] for action in tools[tool]["actions"]], tool)


if __name__ == "__main__":
    unittest.main()
