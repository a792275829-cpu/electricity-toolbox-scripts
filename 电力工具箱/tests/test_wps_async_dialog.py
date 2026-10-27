from __future__ import annotations
import importlib

import json
import threading
import time
import tkinter as tk
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock


class WpsDialogAsyncTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()

    def test_edit_dialog_reads_target_automatically_without_blocking(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_async_dialog_test", paths.wps_writer)
        read_started = threading.Event()
        allow_read_to_finish = threading.Event()

        def slow_read(*_args, **_kwargs):
            read_started.set()
            allow_read_to_finish.wait(timeout=2)
            return {"title": "Target document", "sheets": ["Sheet1", "Sheet2"]}

        initial = module.DocumentConfig(
            name="Config",
            kdocs_url="https://www.kdocs.cn/l/target",
            local_file="",
            regions=[
                module.RegionRow(
                    source_sheet="Local",
                    source_start="A1",
                    source_end="A2",
                    target_sheet="Saved target",
                    target_start="B1",
                    target_end="B2",
                )
            ],
        )
        with (
            mock.patch.object(module, "read_online_document_info", side_effect=slow_read),
            mock.patch.object(module.DocumentConfigDialog, "wait_visibility"),
            mock.patch.object(module.DocumentConfigDialog, "wait_window"),
        ):
            started_at = time.monotonic()
            dialog = module.DocumentConfigDialog(self.root, "Edit config", initial)
            elapsed = time.monotonic() - started_at

            self.assertTrue(read_started.wait(timeout=0.5))
            self.assertEqual(dialog.target_doc_title.get(), "正在读取…")
            self.assertEqual(dialog.online_sheets, ["Saved target"])

        self.assertLess(elapsed, 0.5)
        self.assertEqual(dialog.target_doc_title.get(), "正在读取…")

        allow_read_to_finish.set()
        deadline = time.monotonic() + 1
        while dialog.online_sheets != ["Sheet1", "Sheet2"] and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

        self.assertEqual(dialog.online_sheets, ["Sheet1", "Sheet2"])
        self.assertEqual(dialog.target_doc_title.get(), "Target document")
        dialog.destroy()

    def test_truncated_xlsx_reports_that_generation_was_interrupted(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_service')
        with TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "summary.xlsx"
            workbook_path.write_bytes(b"PK\x03\x04partial workbook data")

            with self.assertRaisesRegex(ValueError, "尚未生成完整"):
                module.read_excel_sheet_names(workbook_path)

    def test_find_or_open_page_reuses_browser_blank_tab(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        blank_page = mock.Mock()
        blank_page.url = "about:blank"
        context = mock.Mock()
        context.pages = [blank_page]

        result = module.find_or_open_page(
            context, "https://www.kdocs.cn/l/target"
        )

        self.assertIs(result, blank_page)
        context.new_page.assert_not_called()
        blank_page.bring_to_front.assert_not_called()
        blank_page.goto.assert_called_once_with(
            "https://www.kdocs.cn/l/target",
            wait_until="commit",
            timeout=20000,
        )

    def test_new_cdp_target_is_created_in_background(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        session = mock.Mock()
        browser = mock.Mock()
        browser.contexts = []
        browser.new_browser_cdp_session.return_value = session

        with mock.patch.object(
            module,
            "cdp_page_targets",
            return_value=(
                "http://127.0.0.1:9222",
                [{"type": "page", "url": "https://www.kdocs.cn/l/new-target"}],
            ),
        ):
            created = module.ensure_background_cdp_target(
                browser,
                "http://127.0.0.1:9222",
                "https://www.kdocs.cn/l/new-target",
            )

        self.assertTrue(created)
        session.send.assert_called_once_with(
            "Target.createTarget",
            {
                "url": "https://www.kdocs.cn/l/new-target",
                "background": True,
            },
        )

    def test_ready_probe_bounds_document_ready_promise(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_service')
        page = mock.Mock()
        page.evaluate.return_value = {
            "ready": True,
            "url": "https://www.kdocs.cn/l/target",
            "title": "Target",
        }

        result = module.wait_for_wps_ready(page, lambda _message: None, 5)

        self.assertTrue(result["ready"])
        readiness_script = page.evaluate.call_args.args[0]
        self.assertIn("app.Ready", readiness_script)
        self.assertIn("Promise.race", readiness_script)
        self.assertNotIn(
            "await window.WPSOpenApi.documentReadyPromise", readiness_script
        )

    def test_macos_dedicated_chrome_uses_isolated_profile_and_local_cdp(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        chrome_path = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )

        command = module.build_wps_chrome_command(
            chrome_path, "http://127.0.0.1:9222"
        )

        if module.sys.platform == "darwin" and module.os.uname().machine == "arm64":
            self.assertEqual(
                command[:3], ["/usr/bin/arch", "-arm64", str(chrome_path)]
            )
        else:
            self.assertEqual(command[0], str(chrome_path))
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertIn(f"--user-data-dir={module.PROFILE_DIR}", command)
        self.assertIn("--disable-background-mode", command)
        self.assertIn("--disable-background-timer-throttling", command)
        self.assertIn("--disable-backgrounding-occluded-windows", command)
        self.assertIn("--disable-renderer-backgrounding", command)
        self.assertNotIn("--headless=new", command)
        self.assertEqual(command[-1], "https://www.kdocs.cn/")

        background_command = module.build_wps_chrome_command(
            chrome_path,
            "http://127.0.0.1:9222",
            headless=True,
        )
        self.assertIn("--headless=new", background_command)
        self.assertIn("--window-size=1400,900", background_command)

    def test_managed_chrome_prefers_separate_testing_bundle(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        with mock.patch.object(module.Path, "is_file", return_value=True):
            selected = module.find_macos_chrome()

        self.assertEqual(
            selected,
            Path(
                "/Applications/Google Chrome for Testing.app/Contents/MacOS/"
                "Google Chrome for Testing"
            ),
        )

    def test_managed_chrome_recovers_changed_pid_from_its_profile(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        chrome_command = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--remote-debugging-port=9222 "
            f"--user-data-dir={module.PROFILE_DIR}"
        )

        with TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "wps-chrome.pid"
            state_path = Path(temp_dir) / "wps-chrome-state.json"
            pid_path.write_text("111", encoding="utf-8")
            state_path.write_text(
                '{"pid": 111, "version": 3}', encoding="utf-8"
            )
            ps_result = mock.Mock(stdout=f"  222 {chrome_command}\n")
            with (
                mock.patch.object(module.sys, "platform", "darwin"),
                mock.patch.object(module, "WPS_CHROME_PID_PATH", pid_path),
                mock.patch.object(module, "WPS_CHROME_STATE_PATH", state_path),
                mock.patch.object(module.os, "kill", side_effect=ProcessLookupError),
                mock.patch.object(module.subprocess, "run", return_value=ps_result),
            ):
                recovered_pid = module.managed_chrome_pid()

            self.assertEqual(recovered_pid, 222)
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "222")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state,
                {"pid": 222, "version": 3, "headless": False},
            )

    def test_stopping_managed_chrome_clears_its_pid_state(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')

        def fake_kill(_pid, sig):
            if sig == module.signal.SIGTERM:
                return
            raise ProcessLookupError

        with TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "wps-chrome.pid"
            state_path = Path(temp_dir) / "wps-chrome-state.json"
            pid_path.write_text("321", encoding="utf-8")
            state_path.write_text(
                '{"pid": 321, "version": 4}', encoding="utf-8"
            )
            with (
                mock.patch.object(module, "WPS_CHROME_PID_PATH", pid_path),
                mock.patch.object(module, "WPS_CHROME_STATE_PATH", state_path),
                mock.patch.object(module.os, "kill", side_effect=fake_kill),
            ):
                module.stop_managed_chrome(321, lambda _message: None, "test")

            self.assertFalse(pid_path.exists())
            self.assertFalse(state_path.exists())

    def test_missing_managed_chrome_discards_stale_pid_state(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')

        with TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "wps-chrome.pid"
            state_path = Path(temp_dir) / "wps-chrome-state.json"
            pid_path.write_text("654", encoding="utf-8")
            state_path.write_text(
                '{"pid": 654, "version": 4}', encoding="utf-8"
            )
            with (
                mock.patch.object(module, "WPS_CHROME_PID_PATH", pid_path),
                mock.patch.object(module, "WPS_CHROME_STATE_PATH", state_path),
                mock.patch.object(module.os, "kill", side_effect=ProcessLookupError),
                mock.patch.object(
                    module, "discover_managed_chrome_pid", return_value=None
                ),
            ):
                self.assertIsNone(module.managed_chrome_pid())

            self.assertFalse(pid_path.exists())
            self.assertFalse(state_path.exists())

    def test_dedicated_chrome_reuses_running_endpoint(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        websocket_url = "ws://127.0.0.1:9222/devtools/browser/test"

        with (
            mock.patch.object(module, "stop_outdated_managed_chrome") as stop_old,
            mock.patch.object(
                module, "try_resolve_cdp_endpoint", return_value=websocket_url
            ),
            mock.patch.object(module, "ensure_wps_cdp_page") as ensure_page,
            mock.patch.object(module.subprocess, "Popen") as popen,
        ):
            result = module.ensure_wps_dedicated_chrome()

        self.assertFalse(result["started"])
        self.assertEqual(result["endpoint"], websocket_url)
        stop_old.assert_called_once_with(
            mock.ANY, "http://127.0.0.1:9222", headless=False
        )
        ensure_page.assert_called_once_with(
            "http://127.0.0.1:9222",
            "https://www.kdocs.cn/",
            require_initial_url=False,
        )
        popen.assert_not_called()

    def test_unresponsive_managed_chrome_is_restarted_automatically(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        websocket_url = "ws://127.0.0.1:9222/devtools/browser/recovered"
        process = mock.Mock(pid=456)
        process.poll.return_value = None

        with (
            mock.patch.object(module, "stop_outdated_managed_chrome"),
            mock.patch.object(
                module, "try_resolve_cdp_endpoint", side_effect=[None, websocket_url]
            ),
            mock.patch.object(module, "managed_chrome_pid", return_value=123),
            mock.patch.object(module, "stop_managed_chrome") as stop_browser,
            mock.patch.object(module, "find_macos_chrome", return_value=Path("/Chrome")),
            mock.patch.object(module.subprocess, "Popen", return_value=process),
            mock.patch.object(module, "ensure_wps_cdp_page"),
            mock.patch.object(
                module.time_module, "monotonic", side_effect=[0, 4, 5, 6]
            ),
            mock.patch.object(module, "WPS_CHROME_PID_PATH", mock.Mock()),
            mock.patch.object(module, "WPS_CHROME_STATE_PATH", mock.Mock()),
        ):
            result = module.ensure_wps_dedicated_chrome(
                initial_url="https://www.kdocs.cn/l/new-target"
            )

        stop_browser.assert_called_once_with(
            123,
            mock.ANY,
            "浏览器仍在运行，但连接无响应",
        )
        self.assertTrue(result["started"])
        self.assertEqual(result["endpoint"], websocket_url)

    def test_empty_dedicated_chrome_creates_wps_page_before_connect(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        opener = mock.MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"id":"page-1","type":"page"}'

        with (
            mock.patch.object(
                module,
                "cdp_page_targets",
                return_value=(
                    "http://127.0.0.1:9222",
                    [{"type": "page", "url": "chrome://newtab/"}],
                ),
            ),
            mock.patch.object(
                module.urllib.request, "build_opener", return_value=opener
            ),
        ):
            created = module.ensure_wps_cdp_page(
                "http://127.0.0.1:9222",
                "https://www.kdocs.cn/l/target",
                require_initial_url=True,
            )

        self.assertTrue(created)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertIn(
            "/json/new?https%3A%2F%2Fwww.kdocs.cn%2Fl%2Ftarget",
            request.full_url,
        )

    def test_managed_mode_connects_without_closing_dedicated_chrome(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = importlib.import_module('toolbox_shared.wps_browser')
        playwright = mock.Mock()
        browser = mock.Mock()
        context = mock.Mock()
        context.pages = [mock.Mock(url="https://www.kdocs.cn/l/target")]
        browser.contexts = [context]
        playwright.chromium.connect_over_cdp.return_value = browser
        logger = lambda _message: None
        target_url = "https://www.kdocs.cn/l/target"

        with (
            mock.patch.object(module, "ensure_wps_dedicated_chrome") as ensure,
            mock.patch.object(module, "ensure_background_cdp_target") as ensure_target,
            mock.patch.object(
                module,
                "resolve_cdp_endpoint",
                return_value="ws://127.0.0.1:9222/devtools/browser/test",
            ),
        ):
            result_browser, result_context, close_context = (
                module.open_browser_context(
                    playwright,
                    "managed_cdp",
                    "http://127.0.0.1:9222",
                    logger,
                    initial_url=target_url,
                )
            )

        ensure.assert_called_once_with(
            "http://127.0.0.1:9222", logger, headless=True
        )
        ensure_target.assert_not_called()
        self.assertIs(result_browser, browser)
        self.assertIs(result_context, context)
        self.assertFalse(close_context)

    def test_writer_migrates_old_persistent_mode_to_managed_chrome(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_managed_gui_test", paths.wps_writer)
        with mock.patch.object(
            module, "load_config", return_value={"browser_mode": "persistent", "configs": []}
        ):
            frame = module.WpsWriterFrame(self.root)

        self.assertEqual(frame.browser_mode.get(), "managed_cdp")
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        button_texts = {
            str(widget.cget("text"))
            for widget in descendants(frame)
            if isinstance(widget, module.ttk.Button)
        }
        self.assertIn("登录 WPS", button_texts)
        self.assertIn("刷新登录状态", button_texts)

    def test_writer_registers_and_releases_background_thread(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_tracked_worker_test", paths.wps_writer)
        registry = mock.Mock()
        frame = module.WpsWriterFrame(self.root, task_registry=registry)
        fake_thread = mock.Mock()
        with mock.patch.object(
            module.threading, "Thread", return_value=fake_thread
        ) as thread_class:
            thread = frame._start_tracked_thread(
                lambda: None,
                name="wps-test-worker",
            )

        self.assertIs(thread, fake_thread)
        registry.register_thread.assert_called_once_with(fake_thread)
        fake_thread.start.assert_called_once_with()
        tracked_target = thread_class.call_args.kwargs["target"]
        tracked_target()
        registry.unregister_thread.assert_called_once_with(fake_thread)

    def test_edit_config_opens_dialog_without_starting_chrome(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_edit_starts_chrome_test", paths.wps_writer)
        with mock.patch.object(module, "load_config", return_value={"configs": []}):
            frame = module.WpsWriterFrame(self.root)
        frame.configs = [
            module.DocumentConfig(
                name="Config",
                kdocs_url="https://www.kdocs.cn/l/target",
                local_file="/tmp/source.xlsx",
                regions=[],
            )
        ]
        frame.refresh_tree()
        frame.tree.selection_set(frame.tree.get_children()[0])
        calls: list[str] = []

        def open_dialog(*_args, **_kwargs):
            calls.append("dialog")
            return mock.Mock(result=None)

        with (
            mock.patch.object(
                frame,
                "start_wps_browser",
            ) as start_browser,
            mock.patch.object(
                module, "DocumentConfigDialog", side_effect=open_dialog
            ),
        ):
            frame.edit_config()

        self.assertEqual(calls, ["dialog"])
        start_browser.assert_not_called()


    def test_failed_target_read_restores_refresh_and_preserves_saved_sheets(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_failed_read_ui_test", paths.wps_writer)
        log = mock.Mock()
        with (
            mock.patch.object(module.DocumentConfigDialog, "wait_visibility"),
            mock.patch.object(module.DocumentConfigDialog, "wait_window"),
            mock.patch.object(module, "read_online_document_info", side_effect=RuntimeError("连接超时")),
        ):
            dialog = module.DocumentConfigDialog(self.root, "编辑配置", log=log)
            dialog.online_sheets = ["原有工作表"]
            dialog.kdocs_url.set("https://www.kdocs.cn/l/target")
            dialog._start_online_read("target", "https://www.kdocs.cn/l/target", False)
            deadline = time.monotonic() + 1
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)

        self.assertFalse(dialog._pending_online_reads)
        self.assertEqual(dialog.online_sheets, ["原有工作表"])
        self.assertEqual(dialog.target_doc_title.get(), "读取失败")
        self.assertEqual(str(dialog.refresh_target_button.cget("state")), "normal")
        log.assert_called_once_with("读取目标工作表失败：连接超时")
        dialog.destroy()


class WpsConnectionTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        self.module = importlib.import_module('toolbox_shared.wps_browser')
        self.playwright = mock.Mock()
        self.browser = mock.Mock()
        self.context = mock.Mock()
        self.browser.contexts = [self.context]
        self.timeout = self.module.PlaywrightTimeoutError("页面连接超时")
        self.log = mock.Mock()
        for name, kwargs in (
            ("ensure_wps_dedicated_chrome", {}),
            ("_ensure_wps_dedicated_chrome", {}),
            ("resolve_cdp_endpoint", {"return_value": "ws://127.0.0.1:9222/browser/test"}),
            ("managed_chrome_pid", {"return_value": 123}),
            ("stop_managed_chrome", {}),
        ):
            patcher = mock.patch.object(self.module, name, **kwargs)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def connect(self, mode="managed_cdp"):
        return self.module.open_browser_context(
            self.playwright, mode, "http://127.0.0.1:9222", self.log
        )

    def test_managed_renderer_timeout_restarts_owned_browser_once(self) -> None:
        connect = self.playwright.chromium.connect_over_cdp
        connect.side_effect = [self.timeout, self.browser]
        self.assertEqual(self.connect(), (self.browser, self.context, False))
        self.stop_managed_chrome.assert_called_once_with(
            123, self.log, "表格页面无响应，连接超时"
        )
        self._ensure_wps_dedicated_chrome.assert_called_once()
        self.assertEqual(connect.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 15000 for call in connect.call_args_list))

    def test_external_browser_timeout_does_not_restart_any_browser(self) -> None:
        self.playwright.chromium.connect_over_cdp.side_effect = self.timeout
        with self.assertRaises(self.module.PlaywrightTimeoutError):
            self.connect("cdp")
        self.stop_managed_chrome.assert_not_called()
        self._ensure_wps_dedicated_chrome.assert_not_called()

    def test_timeout_without_owned_process_does_not_stop_other_browsers(self) -> None:
        self.managed_chrome_pid.return_value = None
        self.playwright.chromium.connect_over_cdp.side_effect = self.timeout
        with self.assertRaisesRegex(RuntimeError, "浏览器连接超时") as caught:
            self.connect()
        self.assertIs(caught.exception.__cause__, self.timeout)
        self.stop_managed_chrome.assert_not_called()

    def test_second_timeout_is_reported_without_another_restart(self) -> None:
        self.playwright.chromium.connect_over_cdp.side_effect = self.timeout
        with self.assertRaisesRegex(RuntimeError, "首次连接失败原因：页面连接超时"):
            self.connect()
        self.stop_managed_chrome.assert_called_once()
        self.assertEqual(self.playwright.chromium.connect_over_cdp.call_count, 2)


if __name__ == "__main__":
    unittest.main()
