from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path


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
        self.assertEqual("line-9999", lines[-1])

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
