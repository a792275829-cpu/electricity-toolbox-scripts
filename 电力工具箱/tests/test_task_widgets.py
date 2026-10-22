from __future__ import annotations

import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
import gc
from pathlib import Path
from unittest.mock import patch


class ToolPageTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import TaskRegistry
        from toolbox.widgets import ToolPage

        self.registry = TaskRegistry()
        self.page = ToolPage(self.root, registry=self.registry, title="测试")

    def tearDown(self) -> None:
        if hasattr(self, "page"):
            self.registry.terminate_all()
            self.page.destroy()
            self.root.destroy()
            gc.collect()

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update_idletasks()
            self.page._poll_queues()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_background_logs_are_batched_and_trimmed(self) -> None:
        thread = threading.Thread(
            target=lambda: [self.page.append_log(f"line-{i}") for i in range(10_000)]
        )
        thread.start()
        thread.join()
        self.page._drain_log_queue()
        lines = self.page.log_text.get("1.0", "end-1c").splitlines()
        self.assertEqual(5_000, len(lines))
        self.assertTrue(lines[-1].endswith("line-9999"))

    def test_process_output_uses_engine_and_page_recovers(self) -> None:
        self.page.run_process(
            [sys.executable, "-c", "print('hello')"],
            cwd=Path.cwd(),
            status="运行测试",
        )
        self.assertTrue(self.wait_for(lambda: not self.page.busy))
        self.page._drain_log_queue()
        self.assertIn("hello", self.page.log_text.get("1.0", "end"))
        self.assertEqual("已完成", self.page.status_var.get())

    def test_log_backlog_yields_and_completion_waits_for_remaining_output(self) -> None:
        from toolbox.tasks import TaskSnapshot, TaskState

        for index in range(1000):
            self.page._log_queue.put(f"line-{index}\n")
        completed = []
        self.page._completion_queue.put((
            TaskSnapshot("done", "测试", TaskState.SUCCEEDED),
            lambda _result: completed.append(True),
        ))
        self.page._poll_queues()
        self.assertEqual(800, self.page._log_queue.qsize())
        self.assertEqual([], completed)
        navigated = []
        self.root.after(0, lambda: navigated.append(True))
        self.root.update()
        self.assertEqual([True], navigated)
        while not completed:
            self.page._poll_queues()
        contents = self.page.log_console.contents()
        self.assertLess(contents.index("line-999"), contents.index("已完成"))
        self.assertEqual(1, len(completed))

    def test_large_messages_also_limit_each_poll(self) -> None:
        for _ in range(20):
            self.page._log_queue.put("x" * 32_000 + "\n")
        self.page._poll_queues()
        self.assertEqual(18, self.page._log_queue.qsize())

    def test_destroy_persists_backlog_without_rendering(self) -> None:
        from toolbox.widgets import ToolPage
        page = ToolPage(self.root, registry=self.registry)
        with tempfile.TemporaryDirectory() as folder:
            page.log_console.directory = Path(folder)
            for index in range(1000):
                page._log_queue.put(f"record-{index}\n")
            page._poll_queues()
            path = page.log_console.path
            self.assertEqual(800, page._log_queue.qsize())
            with patch.object(page.log_console.text, "insert", side_effect=AssertionError("Shutdown rendered logs")):
                page.destroy()
            saved = path.read_text()
            self.assertEqual(1000, saved.count("record-"))
            self.assertIn("record-999\n", saved)

    def test_hidden_busy_page_pauses_animation_and_resumes_without_losing_state(self) -> None:
        def animation_timers():
            scripts = [tuple(map(str, self.root.tk.splitlist(self.root.tk.call("after", "info", item)[0])))
                       for item in self.root.tk.call("after", "info")]
            return [script for script in scripts
                    if script[:2] == ("ttk::progressbar::Autoincrement", str(self.page.progress))]

        self.page.set_busy(True, "后台运行")
        self.assertEqual([], animation_timers())
        self.page.pack()
        self.root.deiconify()
        self.root.update_idletasks()
        self.assertEqual(1, len(animation_timers()))
        self.assertEqual("80", animation_timers()[0][2])
        self.page.pack_forget()
        self.root.update_idletasks()
        self.assertTrue(self.page.busy)
        self.assertEqual("后台运行", self.page.status_var.get())
        self.assertEqual([], animation_timers())
        self.page.pack()
        self.root.update_idletasks()
        self.assertEqual(1, len(animation_timers()))
        self.page.set_busy(False)
        self.assertEqual([], animation_timers())

    def test_cancel_button_cancels_owned_process(self) -> None:
        self.page.run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=Path.cwd(),
            status="长任务",
        )
        self.assertIsNotNone(self.page.current_task_id)
        self.page.cancel_current_task()
        self.assertTrue(self.wait_for(lambda: not self.page.busy, timeout=5))
        self.assertEqual("已取消", self.page.status_var.get())


if __name__ == "__main__":
    unittest.main()
