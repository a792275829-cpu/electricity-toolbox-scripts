from __future__ import annotations

import threading
import time
import tkinter as tk
import unittest
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

    def test_edit_dialog_only_reads_wps_after_explicit_refresh(self) -> None:
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

            self.assertFalse(read_started.wait(timeout=0.1))
            self.assertEqual(dialog.target_doc_title.get(), "")
            self.assertEqual(dialog.online_sheets, ["Saved target"])
            self.assertIn("saved config", dialog.read_status.get())
            dialog.refresh_online_sheets()
            self.assertTrue(read_started.wait(timeout=0.5))

        self.assertLess(elapsed, 0.5)
        self.assertEqual(dialog.target_doc_title.get(), "Loading...")

        allow_read_to_finish.set()
        deadline = time.monotonic() + 1
        while dialog.online_sheets != ["Sheet1", "Sheet2"] and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

        self.assertEqual(dialog.online_sheets, ["Sheet1", "Sheet2"])
        self.assertEqual(dialog.target_doc_title.get(), "Target document")
        dialog.destroy()

    def test_find_or_open_page_reuses_browser_blank_tab(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_blank_tab_test", paths.wps_writer)
        blank_page = mock.Mock()
        blank_page.url = "about:blank"
        context = mock.Mock()
        context.pages = [blank_page]

        result = module.find_or_open_page(
            context, "https://www.kdocs.cn/l/target"
        )

        self.assertIs(result, blank_page)
        context.new_page.assert_not_called()
        blank_page.bring_to_front.assert_called_once_with()
        blank_page.goto.assert_called_once_with(
            "https://www.kdocs.cn/l/target",
            wait_until="commit",
            timeout=60000,
        )

    def test_ready_probe_bounds_document_ready_promise(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_ready_probe_test", paths.wps_writer)
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
        module = load_module("wps_chrome_command_test", paths.wps_writer)
        chrome_path = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )

        command = module.build_wps_chrome_command(
            chrome_path, "http://127.0.0.1:9222"
        )

        self.assertEqual(command[0], str(chrome_path))
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertIn(f"--user-data-dir={module.PROFILE_DIR}", command)
        self.assertIn("--disable-background-timer-throttling", command)
        self.assertIn("--disable-backgrounding-occluded-windows", command)
        self.assertIn("--disable-renderer-backgrounding", command)
        self.assertEqual(command[-1], "https://www.kdocs.cn/")

    def test_dedicated_chrome_reuses_running_endpoint(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_chrome_reuse_test", paths.wps_writer)
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
            mock.ANY, "http://127.0.0.1:9222"
        )
        ensure_page.assert_called_once_with(
            "http://127.0.0.1:9222",
            "https://www.kdocs.cn/",
            require_initial_url=False,
        )
        popen.assert_not_called()

    def test_empty_dedicated_chrome_creates_wps_page_before_connect(self) -> None:
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_empty_cdp_page_test", paths.wps_writer)
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
        module = load_module("wps_managed_context_test", paths.wps_writer)
        playwright = mock.Mock()
        browser = mock.Mock()
        context = mock.Mock()
        browser.contexts = [context]
        playwright.chromium.connect_over_cdp.return_value = browser
        logger = lambda _message: None
        target_url = "https://www.kdocs.cn/l/target"

        with (
            mock.patch.object(module, "ensure_wps_dedicated_chrome") as ensure,
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
            "http://127.0.0.1:9222",
            logger,
            initial_url=target_url,
            preload_url=True,
        )
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
        self.assertIn("Start/open dedicated Chrome", button_texts)
        self.assertIn("Check status", button_texts)

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

    def test_edit_config_starts_managed_chrome_before_opening_dialog(self) -> None:
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
                side_effect=lambda **kwargs: calls.append(
                    f"browser:{kwargs.get('initial_url')}"
                ),
            ),
            mock.patch.object(
                module, "DocumentConfigDialog", side_effect=open_dialog
            ),
        ):
            frame.edit_config()

        self.assertEqual(
            calls,
            ["browser:https://www.kdocs.cn/l/target", "dialog"],
        )


if __name__ == "__main__":
    unittest.main()
