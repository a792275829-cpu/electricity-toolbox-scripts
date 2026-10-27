from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from toolbox_log_console import LogConsole, _LogBuffer, explain_error, redact


class DiagnosisTests(unittest.TestCase):
    def test_group_upload_uses_one_output_channel_and_preserves_repeated_messages(self):
        import contextlib
        import io
        from toolbox.runtime import load_module

        module = load_module("group_upload_log_test", Path(__file__).resolve().parents[2] / "集团每日上传/upload_daily_report.py")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.log("cli-message")
            with patch.object(module, "LOG_SINK", lambda message: print(message, flush=True)):
                module.log("callback-message")
                module.log("callback-message")
        self.assertEqual(["cli-message", "callback-message", "callback-message"], output.getvalue().splitlines())

    def test_remote_error_only_omits_traceback_that_was_delivered(self):
        from toolbox.tasks.errors import RemoteTaskError, classify_error

        trace = "Traceback (most recent call last):\n  original frame\nRuntimeError: business-failure\n"
        message = "命令退出码 1\n" + trace
        error = RemoteTaskError(message, trace)
        self.assertEqual("命令退出码 1", error.log_details)
        self.assertEqual(message, str(error))
        self.assertEqual("RuntimeError: business-failure", classify_error(error).summary)
        for delivered in ("", trace[:30], "another traceback"):
            self.assertEqual(message, RemoteTaskError(message, delivered).log_details)

    def test_expired_login_explicitly_requires_login(self):
        from toolbox.tasks.errors import classify_error, ErrorCategory

        result = classify_error(RuntimeError("登录状态已过期"))
        self.assertEqual(result.summary, "需要重新登录")
        self.assertEqual(result.category, ErrorCategory.AUTHENTICATION)
        self.assertIn("在本模块重新登录", result.advice)

    def test_reported_proxy_failure_is_not_classified_as_expired_login(self):
        from toolbox.tasks.errors import classify_error, ErrorCategory
        from toolbox.tasks.process import ProcessExecutionError

        output = (
            "正在使用已配置的清能网站账号密码自动登录...\n"
            "接口登录失败：APIRequestContext.get: connect ECONNREFUSED 127.0.0.1:7897\n"
            "改用页面登录...\n"
            "失败：Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://example.test/#/login\n"
            "subprocess.CalledProcessError: Command returned non-zero exit status 1.\n"
        )
        for error in (ProcessExecutionError(1), RuntimeError("命令退出码 1"), RuntimeError(output)):
            with self.subTest(error=type(error).__name__):
                result = classify_error(error, output)
                self.assertEqual(result.category, ErrorCategory.NETWORK)
                self.assertEqual(result.summary, "代理或网络连接失败")
                self.assertIn("代理软件是否运行", result.advice)
        self.assertEqual(explain_error(output)[0], "代理或网络连接失败")

    def test_recovered_proxy_failure_does_not_hide_final_authentication_failure(self):
        from toolbox.tasks.errors import classify_error, ErrorCategory

        error = RuntimeError("首次请求：ECONNREFUSED 127.0.0.1:7897\n重试后：HTTP status 401 Unauthorized")
        result = classify_error(error)
        self.assertEqual(result.summary, "需要重新登录")
        self.assertEqual(result.category, ErrorCategory.AUTHENTICATION)

    def test_log_buffer_pages_and_search_cross_chunk_boundaries(self):
        buffer = _LogBuffer()
        chunks = ["甲🙂AB", "cd", "EF尾", "第二段"]
        for chunk in chunks:
            buffer.append(chunk)
        complete = "".join(chunks)
        for start in range(len(complete) + 1):
            for end in range(start, len(complete) + 1):
                self.assertEqual(complete[start:end], buffer.slice(start, end))
            for query in ("ABcdEF", "🙂AB", "第二", "不存在"):
                self.assertEqual(complete.find(query, start), buffer.find(query, start))
        self.assertEqual(2, buffer.find("abcdef", ignore_case=True))

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

    def test_finish_refines_diagnosis_without_duplicate_terminal_record(self):
        console = self.console
        console.begin()
        console.append("same-message")
        console.append("same-message")
        console.finish("执行失败", "初步错误", "检查输入")
        console.append("Traceback (most recent call last):\nTimeoutError: original timeout")
        console.finish("执行失败", "请求或等待超时", "检查网络")
        console.finish("执行失败", "请求或等待超时", "检查网络")
        self.assertEqual(1, console.contents().count("执行失败 · 耗时"))
        self.assertEqual(2, console.contents().count("same-message\n"))
        self.assertIn("检查网络", console.summary.get())
        self.assertEqual(console.contents(), console.path.read_text())
        console.begin()
        console.finish("已完成")
        self.assertEqual(1, console.contents().count("已完成 · 耗时"))

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

    def test_wps_destroy_saves_even_a_never_rendered_run(self):
        from toolbox.runtime import load_module
        source = Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py"
        module = load_module("wps_log_console_test", source)
        with patch.object(module.WpsWriterFrame, "load_initial_config"), patch.object(module, "LOG_DIR", Path(self.temp.name)):
            frame = module.WpsWriterFrame(self.root)
            console = frame.log_console
            for index in range(1000):
                frame.log_queue.put(f"record-{index}")
            frame.log_queue.put("操作失败：original failure; password=secret123")
            with patch.object(console.text, "insert", side_effect=AssertionError("Shutdown rendered logs")):
                frame.destroy()
            saved = console.path.read_text()
            self.assertEqual(1000, saved.count("record-"))
            self.assertIn("original failure", saved)
            self.assertNotIn("secret123", saved)

    def test_expanded_pages_preserve_long_lines_and_do_not_follow_earlier_pages(self):
        console = self.console
        console.begin()
        console.append("first-cause\n" + "x" * 300_000 + "\nlast-line")
        console.expand()
        self.root.update_idletasks()
        self.assertEqual("none", str(console._expanded_text.cget("wrap")))
        console._expanded_start = 0
        parts = []
        while console._expanded_start < console._log.length:
            console._refresh_expanded()
            part = console._expanded_text.get("1.0", "end-1c")
            self.assertLessEqual(len(part), console.EXPANDED_PAGE_SIZE)
            parts.append(part)
            if console._expanded_start + console.EXPANDED_PAGE_SIZE >= console._log.length:
                break
            console._move_expanded_page(1)
        self.assertEqual(console.contents(), "".join(parts))
        console._expanded_start = 0
        console._refresh_expanded()
        before = console._expanded_text.get("1.0", "end-1c")
        console.append("live-line")
        self.assertEqual(before, console._expanded_text.get("1.0", "end-1c"))
        console._refresh_expanded(follow=True)
        self.assertIn("live-line", console._expanded_text.get("1.0", "end-1c"))

    def test_expanded_history_copy_is_complete_and_search_reaches_other_pages(self):
        from tkinter import ttk
        console = self.console
        console.begin()
        console.append("early-marker\n" + "x" * 50_000 + "\nlate-marker")
        previous = console.contents()
        console.begin()
        console.append("current-run")
        console.expand()
        bar = console._expanded.winfo_children()[0]
        selected = next(w for w in bar.winfo_children() if isinstance(w, ttk.Combobox))
        selected.current(1)
        selected.event_generate("<<ComboboxSelected>>")
        self.root.update_idletasks()
        text = console._expanded_text
        self.assertIn("late-marker", text.get("1.0", "end-1c"))
        buttons = {str(w.cget("text")): w for w in bar.winfo_children() if isinstance(w, ttk.Button)}
        with patch.object(console, "clipboard_clear"), patch.object(console, "clipboard_append") as copy:
            buttons["复制错误"].invoke()
        self.assertEqual(previous, copy.call_args.args[0])
        search = next(w for w in bar.winfo_children() if type(w) is ttk.Entry)
        search.insert(0, "EARLY-MARKER")
        buttons["查找"].invoke()
        self.assertIn("early-marker", text.get("1.0", "end-1c"))
        console.append("new-live-output")
        self.assertNotIn("new-live-output", text.get("1.0", "end-1c"))

    def test_expanded_search_highlights_unicode_after_supplementary_characters(self):
        from tkinter import ttk
        console = self.console
        console.begin()
        console.append("🙂🙂目标字符")
        console.expand()
        bar = console._expanded.winfo_children()[0]
        search = next(w for w in bar.winfo_children() if type(w) is ttk.Entry)
        search.insert(0, "目标字符")
        button = next(w for w in bar.winfo_children() if isinstance(w, ttk.Button) and str(w.cget("text")) == "查找")
        button.invoke()
        ranges = console._expanded_text.tag_ranges("match")
        self.assertEqual("目标字符", console._expanded_text.get(*ranges))

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
                self.assertEqual(1, frame.log_console.contents().count("执行失败 · 耗时"))
                self.assertIs(frame.log_text, frame.log_console.text)
            finally:
                frame.destroy()

    def test_wps_remote_failure_does_not_replay_streamed_trace(self):
        from unittest.mock import Mock
        from toolbox.runtime import load_module
        from toolbox.tasks.errors import RemoteTaskError

        module = load_module("wps_remote_log_test", Path(__file__).resolve().parents[2] / "wps自动/wps_excel_to_kdocs_gui.py")
        trace = "Traceback (most recent call last):\nTimeoutError: original remote timeout\n"
        def fail(_action, _config, _indexes, log):
            log(trace)
            raise RemoteTaskError("命令退出码 1\n" + trace, trace)
        registry = Mock(remote_business=True)
        registry.wps_action.side_effect = fail
        with patch.object(module.WpsWriterFrame, "load_initial_config"), patch.object(module, "LOG_DIR", Path(self.temp.name)):
            frame = module.WpsWriterFrame(self.root)
            frame.task_registry = registry
            try:
                with patch.object(frame, "selected_config_indexes", return_value=[0]), patch.object(frame, "current_runtime_config", return_value={}), patch.object(frame, "save_current_config"), patch.object(frame, "_start_tracked_thread", side_effect=lambda worker, **_kw: worker()):
                    frame._remote_write(preview=False)
                frame.drain_logs()
                contents = frame.log_console.contents()
                self.assertEqual(1, contents.count("Traceback (most recent call last):"))
                self.assertEqual(1, contents.count("执行失败 · 耗时"))
                self.assertIn("命令退出码 1", contents)
                self.assertIn("超时", frame.log_console.summary.get())
                self.assertEqual(contents, frame.log_console.path.read_text())
                daily = list(Path(self.temp.name).glob("wps_write_*.log"))
                self.assertEqual(1, len(daily))
                self.assertIn(trace, daily[0].read_text())
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

    def test_login_and_proxy_failures_display_distinct_actionable_reasons(self):
        from toolbox.runtime import TaskRegistry
        from toolbox.widgets import ToolPage

        cases = (
            ("登录状态已过期", "需要重新登录"),
            ("接口登录失败：connect ECONNREFUSED 127.0.0.1:7897\n失败：Page.goto: net::ERR_PROXY_CONNECTION_FAILED", "代理或网络连接失败"),
        )
        for output, expected in cases:
            with self.subTest(reason=expected):
                page = ToolPage(self.root, registry=TaskRegistry(), title="风力预测截图")
                try:
                    with patch("toolbox.widgets.messagebox.showerror") as popup:
                        page.run_process(
                            [sys.executable, "-c", f"import sys; print({output!r}, flush=True); sys.exit(1)"],
                            cwd=Path.cwd(), status="正在生成截图",
                        )
                        deadline = time.monotonic() + 5
                        while page.busy and time.monotonic() < deadline:
                            self.root.update()
                            time.sleep(.01)
                        self.assertFalse(page.busy)
                        self.assertIn(expected, page.log_console.summary.get())
                        self.assertIn("命令退出码 1", page.log_console.contents())
                        popup.assert_called_once()
                        self.assertIn(expected, popup.call_args.args[1])
                finally:
                    page.destroy()
