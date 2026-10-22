from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from toolbox_log_console import LogConsole, explain_error, redact


class DiagnosisTests(unittest.TestCase):
    def test_subprocess_reason_comes_from_original_output(self):
        from toolbox.tasks import ProcessExecutionError, classify_error
        for detail, expected in (("TimeoutError: timeout 1000ms exceeded", "超时"),
                                 ("ModuleNotFoundError: No module named 'example'", "依赖"),
                                 ("PermissionError: denied", "权限"),
                                 ("登录状态已过期", "登录")):
            with self.subTest(detail=detail):
                error = classify_error(ProcessExecutionError(1), "登录状态验证通过\n" + detail)
                self.assertIn(expected, error.summary)
                self.assertTrue(error.advice)
        error = classify_error(ProcessExecutionError(2), "登录成功\n读取文件完成")
        self.assertIn("暂未识别", error.summary)
        self.assertEqual("命令退出码 2", str(error.cause))
        self.assertIsNone(explain_error("登录成功，网络连接正常"))

    def test_redaction_handles_headers_json_cli_and_url(self):
        raw = '\n'.join(('Authorization: Bearer topsecret', 'Cookie: sid=abcdef',
                         '{"password": "hello world", "token": "secret-token"}',
                         '--username someone --password verysecret',
                         'https://alice:passcode@example.com/path?token=querysecret'))
        result = redact(raw)
        for secret in ('topsecret', 'abcdef', 'hello world', 'secret-token', 'someone', 'verysecret', 'alice', 'passcode', 'querysecret'):
            self.assertNotIn(secret, result)


class LogConsoleTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("720x420")
        self.temp = tempfile.TemporaryDirectory()
        self.console = LogConsole(self.root, module="测试模块", directory=Path(self.temp.name))
        self.console.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.temp.cleanup()

    def test_compact_layout_keeps_controls_above_log_and_hides_empty_rows(self):
        from toolbox.ui.theme import configure_theme
        from tkinter import ttk
        configure_theme(ttk.Style(self.root))
        for width in (520, 1000):
            self.root.geometry(f"{width}x420")
            self.console.begin()
            self.root.update()
            top = self.console.text.winfo_y()
            self.assertLessEqual(top, 12)
            self.assertFalse(self.console.summary_label.winfo_ismapped())
            self.assertFalse(self.console.notice_label.winfo_ismapped())
            for button in (self.console.copy_button, self.console.expand_button):
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                     self.console.body.winfo_rooty())
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                     self.console.winfo_rootx() + self.console.winfo_width())
            self.console.finish("执行失败", "测试错误", "检查输入")
            self.console.notice.set("已复制")
            self.root.update()
            self.assertTrue(self.console.summary_label.winfo_ismapped())
            self.assertTrue(self.console.notice_label.winfo_ismapped())
            self.console.begin()
            self.root.update()
            self.assertEqual(top, self.console.text.winfo_y())
            self.assertFalse(self.console.notice_label.winfo_ismapped())

    def test_full_log_survives_display_trim_and_new_run(self):
        self.console.begin()
        self.console.append("\n".join(f"line-{i}" for i in range(10000)))
        self.assertNotIn("line-0\n", self.console.text.get("1.0", "end"))
        self.assertIn("line-0\n", self.console.contents())
        old = self.console.path
        self.assertIn("line-0\n", old.read_text())
        self.console.begin()
        self.assertNotEqual(old, self.console.path)
        self.assertTrue(old.exists())
        self.console.expand()
        self.root.update()
        self.console.append("live-line")
        self.assertIn("live-line", self.console._expanded_text.get("1.0", "end"))

    def test_scroll_pauses_and_resumes_without_checkbox(self):
        self.console.begin()
        self.console.append("\n".join(f"line-{i}" for i in range(300)))
        self.root.update()
        self.assertGreater(self.console.text.yview()[1], .99)
        self.console.text.yview_moveto(.2)
        self.root.update()
        top = self.console.text.index("@0,0")
        self.console.append("new-line")
        self.root.update()
        self.assertEqual(top, self.console.text.index("@0,0"))
        self.console.text.see("end")
        self.root.update()
        self.console.append("follow-again")
        self.root.update()
        self.assertGreater(self.console.text.yview()[1], .99)
        self.assertEqual("disabled", str(self.console.text.cget("state")))

    def test_copy_reads_back_complete_redacted_error(self):
        try:
            previous = self.root.clipboard_get()
        except tk.TclError:
            previous = ""
        try:
            self.console.begin()
            self.console.append("first cause\npassword=secret123\nTraceback: original exception")
            self.console.finish("执行失败", "网络超时", "检查网络后重试")
            self.console.copy_error()
            self.root.update()
            copied = self.root.clipboard_get()
            self.assertIn("first cause", copied)
            self.assertIn("Traceback", copied)
            self.assertIn("网络超时", copied)
            self.assertNotIn("secret123", copied)
        finally:
            self.root.clipboard_clear()
            self.root.clipboard_append(previous)
            self.root.update()

    def test_storage_failure_keeps_error_copy_available(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            self.console.begin()
        self.console.append("original exception")
        self.assertIn("original exception", self.console.contents())
        self.assertIn("未能保存", self.console.notice.get())

    def test_long_lines_have_bounded_display_but_complete_persisted_contents(self):
        self.console.begin()
        content = "first-cause\n" + "x" * 300_000 + "\nlast-line"
        self.console.append(content)
        self.console.append("y" * 100_000)
        visible = self.console.text.get("1.0", "end-1c")
        self.assertLessEqual(len(visible), self.console.DISPLAY_CHAR_LIMIT)
        self.assertTrue(visible.endswith("y" * (self.console.DISPLAY_LINE_LIMIT - 1) + "\n"))
        self.assertLessEqual(max(map(len, visible.splitlines())), self.console.DISPLAY_LINE_LIMIT + 1)
        self.assertIn(content, self.console.contents())
        self.assertEqual(self.console.contents(), self.console.path.read_text())

    def test_wps_backlog_is_batched_and_does_not_duplicate_poll_timers(self):
        from toolbox.runtime import load_module
        source = Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py"
        module = load_module("wps_log_console_test", source)
        with patch.object(module.WpsWriterFrame, "load_initial_config"), patch.object(module, "LOG_DIR", Path(self.temp.name)):
            frame = module.WpsWriterFrame(self.root)
            try:
                for index in range(1000):
                    frame.log_queue.put(f"line-{index}")
                frame.log_queue.put("写入完成：测试")
                old_timer = frame._drain_after_id
                with patch.object(frame.log_console, "append", wraps=frame.log_console.append) as append:
                    frame.drain_logs()
                self.assertEqual(801, frame.log_queue.qsize())
                self.assertLessEqual(append.call_count, 2)  # Includes initial header.
                self.assertNotIn(old_timer, self.root.tk.call("after", "info"))
                self.assertFalse(frame.log_console.finished)
                while not frame.log_queue.empty():
                    frame.drain_logs()
                self.assertTrue(frame.log_console.finished)
                contents = frame.log_console.contents()
                for index in range(1000):
                    self.assertEqual(1, contents.count(f"line-{index}\n"))
                self.assertLess(contents.index("line-999"), contents.index("写入完成："))
            finally:
                frame.destroy()

    def test_wps_error_includes_detailed_traceback_in_shared_console(self):
        from toolbox.runtime import load_module
        source = Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py"
        module = load_module("wps_log_console_test", source)
        with patch.object(module.WpsWriterFrame, "load_initial_config"), patch.object(module, "LOG_DIR", Path(self.temp.name)):
            frame = module.WpsWriterFrame(self.root)
            try:
                frame.log("操作失败：请求超时")
                frame.detail_log("Traceback (most recent call last):\nTimeoutError: original timeout")
                frame.after_cancel(frame._drain_after_id)
                frame.drain_logs()
                self.assertIn("超时", frame.log_console.summary.get())
                self.assertIn("TimeoutError: original timeout", frame.log_console.contents())
                self.assertIs(frame.log_text, frame.log_console.text)
            finally:
                frame.destroy()

    def test_failed_process_displays_reason_and_retains_exitcode(self):
        from toolbox.runtime import TaskRegistry
        from toolbox.widgets import ToolPage
        registry = TaskRegistry()
        page = ToolPage(self.root, registry=registry, title="失败测试")
        with patch("toolbox.widgets.messagebox.showerror") as popup:
            page.run_process([sys.executable, "-c", "raise TimeoutError('test timeout')"], cwd=Path.cwd(), status="运行中")
            deadline = time.monotonic() + 5
            while page.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.assertFalse(page.busy)
            self.assertIn("超时", page.log_console.summary.get())
            self.assertIn("命令退出码 1", page.log_console.contents())
            self.assertIn("TimeoutError", page.log_console.contents())
            popup.assert_called_once()
        page.destroy()
