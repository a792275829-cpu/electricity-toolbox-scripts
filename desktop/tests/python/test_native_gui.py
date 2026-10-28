from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]

from native_bridge import NativeService
from toolbox.app import ToolboxApp


class NativeLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.update_requests = []
        cls.app = ToolboxApp(workspace=Path(cls.temp.name),
                             check_updates=lambda: cls.update_requests.append("check"))
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.temp.cleanup()

    def setUp(self):
        self.service = NativeService(self.app)
        self.app.deiconify()
        self.app.update_idletasks()

    def test_running_write_blocks_update_without_hiding_the_window(self):
        release = threading.Event()
        self.app.registry.start_callable(
            "测试写入", lambda token, emit: release.wait(10), cancellable=False
        )
        try:
            with self.assertRaisesRegex(ValueError, "等待"):
                self.service.call("prepare_update", {})
            self.assertNotEqual(self.app.state(), "withdrawn")
            self.assertFalse(self.service.maintenance)
        finally:
            release.set()
            self.app.registry.shutdown(timeout=2)

    def test_activate_restores_hidden_main_window_without_loading_business_pages(self):
        self.app.withdraw()
        self.assertTrue(self.service.call("activate", {}))
        self.app.update_idletasks()
        self.assertEqual(self.app.state(), "normal")
        self.assertEqual(self.app.current_page, "导出上网电量")
        self.assertEqual(list(self.app.pages), ["导出上网电量"])

    def test_update_does_not_save_unverified_account_draft(self):
        with (
            patch.object(self.app.credentials_panel, "dirty", True),
            patch.object(self.app.credentials_panel, "save", return_value=False),
        ):
            self.service.call("prepare_update", {})
            self.app.credentials_panel.save.assert_not_called()
            self.service.call("abort_update", {})
        self.assertNotEqual(self.app.state(), "withdrawn")

    def test_download_failure_restores_original_window_and_form_values(self):
        previous_page = self.app.current_page
        self.service.call("prepare_update", {})
        self.assertNotEqual(self.app.state(), "withdrawn")
        self.assertTrue(self.service._locked_widgets)
        self.assertFalse(self.service.call("activate", {}))
        self.service.call("abort_update", {})
        self.app.update_idletasks()
        self.assertNotEqual(self.app.state(), "withdrawn")
        self.assertEqual(self.app.current_page, previous_page)

    def test_install_guard_locks_business_inputs_and_can_restore_them(self):
        entry = self.app.credentials_panel
        tags = entry.bindtags()
        self.service.call("prepare_update", {})
        try:
            self.assertNotEqual(entry.bindtags(), tags)
            with patch("native_bridge.messagebox.showinfo"):
                self.assertFalse(self.service.call("request_close", {}))
        finally:
            self.service.call("abort_update", {})
        self.assertEqual(entry.bindtags(), tags)

    def test_native_close_retains_original_cancel_confirmation(self):
        release = threading.Event()
        self.app.registry.start_callable(
            "测试任务", lambda token, emit: release.wait(10)
        )
        try:
            with patch(
                "toolbox.app.messagebox.askyesno", return_value=False
            ) as confirm:
                self.service.call("request_close", {})
                self.app.update_idletasks()
            confirm.assert_called_once()
            self.assertTrue(self.app.winfo_exists())
            self.assertTrue(self.app.registry.has_running_tasks())
        finally:
            release.set()
            self.app.registry.shutdown(timeout=2)

    def test_updater_cannot_run_business_actions_or_read_passwords(self):
        for method in ("start", "credentials.get", "credentials.save", "wps.save"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.service.call(method, {})

    def test_update_badge_belongs_to_sidebar_and_starts_hidden(self):
        self.assertEqual(self.app.update_button.cget("text"), "new")
        self.assertIs(self.app.update_area.master, self.app.sidebar)
        self.app.set_update_available(False)
        self.assertEqual(self.app.update_button.winfo_manager(), "")
        self.app.set_update_available(True)
        try:
            self.app.update_idletasks()
            self.assertTrue(self.app.update_button.winfo_ismapped())
            self.assertNotIn("今日概览", self.app.nav_buttons)
            self.assertFalse(hasattr(self.app, "search_entry"))
        finally:
            self.app.set_update_available(False)

    def test_installer_exit_is_refused_when_a_write_is_running(self):
        release = threading.Event()
        self.app.registry.start_callable("测试安装保护", lambda token, emit: release.wait(10), cancellable=False)
        try:
            with self.assertRaisesRegex(ValueError, "等待"):
                self.service.call("installer_close", {})
            self.assertTrue(self.app.winfo_exists())
        finally:
            release.set()
            self.app.registry.shutdown(timeout=2)

    def test_update_lock_cancels_pending_page_creation_before_locking_inputs(self):
        app = self.app
        previous = app.current_page
        target = "市场表更新"
        with patch.dict(app._factories, {target: lambda *_: self.fail("Locked app built a new page")}):
            app.request_page(target)
            try:
                self.service.call("prepare_update", {})
                app.after(100, app.quit)
                app.mainloop()
                self.assertIsNone(app._pending_page)
                self.assertNotIn(target, app.pages)
                self.assertEqual(previous, app.current_page)
                app.request_page(target)
                self.assertIsNone(app._pending_page)
            finally:
                self.service.call("abort_update", {})

    def test_update_lock_pauses_and_resumes_wps_interface_loading(self):
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import ToolPaths

        app = self.app
        previous = app.current_page
        target = "WPS写入工具"
        factory = lambda parent, _paths, registry: WpsWriterPage(parent, ToolPaths(ROOT), registry)

        def writer_frame(parent, **_kwargs):
            frame = ttk.Frame(parent)
            ttk.Label(frame, text="写入设置").pack()
            return frame

        with (patch.dict(app._factories, {target: factory}),
              patch("toolbox.pages.load_module", return_value=SimpleNamespace(
                  WpsWriterFrame=writer_frame)) as load):
            app.show_page(target)
            page = app.pages[target]
            try:
                self.service.call("prepare_update", {})
                app.after(100, app.quit)
                app.mainloop()
                load.assert_not_called()
                self.assertIsNone(page._writer_load_after_id)
                self.service.call("abort_update", {})
                # Import completion and macOS window mapping are asynchronous.
                # A fixed quit timer may fire before Tk's geometry/redraw idle
                # handlers, even when the writer was constructed successfully.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    app.update()
                    if (page._writer_load_done and hasattr(page, "writer_frame")
                            and page.writer_frame.winfo_ismapped()):
                        break
                    time.sleep(.01)
                self.assertTrue(page._writer_load_done)
                self.assertTrue(hasattr(page, "writer_frame"), "WPS frame was not created")
                self.assertTrue(page.writer_frame.winfo_ismapped())
                self.assertFalse(any(str(tag).startswith("ToolboxUpdateLock")
                                     for tag in page.writer_frame.bindtags()))
            finally:
                self.service.call("abort_update", {})
                app.show_page(previous)


class NativeProtocolTests(unittest.TestCase):
    def test_actual_gui_starts_and_closes_through_json_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "desktop/python/native_bridge.py"),
                    "--workspace",
                    folder,
                ],
                input='{"id":1,"method":"ping"}\n{"id":2,"method":"request_close"}\n',
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1"},
                timeout=20,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = [json.loads(line) for line in proc.stdout.splitlines()]
        replies = {row["id"]: row for row in rows if "id" in row}
        self.assertEqual(replies[1]["result"]["ui"], "original-tk")
        self.assertTrue(replies[2]["result"])
        self.assertEqual(
            [row["event"]["kind"] for row in rows if "event" in row],
            ["native-ready", "native-closed"],
        )


if __name__ == "__main__":
    unittest.main()
