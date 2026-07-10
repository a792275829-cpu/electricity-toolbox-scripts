from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path


class TaskModelTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_token_cancels(self) -> None:
        from toolbox.tasks import CancellationToken, TaskCancelled, TaskSnapshot, TaskState

        snapshot = TaskSnapshot(task_id="task-1", name="示例")
        self.assertEqual(TaskState.CREATED, snapshot.state)
        with self.assertRaises(Exception):
            snapshot.state = TaskState.RUNNING  # type: ignore[misc]

        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelled):
            token.raise_if_cancelled()


class EventBufferTests(unittest.TestCase):
    def test_buffer_batches_events_in_order(self) -> None:
        from toolbox.tasks import EventBuffer, TaskEvent, TaskEventKind

        buffer = EventBuffer()
        for index in range(10_000):
            buffer.publish(TaskEvent("task", TaskEventKind.LOG, str(index)))
        self.assertEqual(10_000, buffer.pending_count)
        first = buffer.drain(limit=2)
        self.assertEqual(["0", "1"], [event.message for event in first])
        self.assertEqual(9_998, len(buffer.drain()))


class ProcessRunnerTests(unittest.TestCase):
    def test_process_streams_output_and_supports_cancellation(self) -> None:
        from toolbox.tasks import CancellationToken, ProcessRunner, TaskCancelled

        output: list[str] = []
        result = ProcessRunner().run(
            [sys.executable, "-c", "print('alpha'); print('beta')"],
            cwd=Path.cwd(),
            env=None,
            token=CancellationToken(),
            on_output=output.append,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(["alpha\n", "beta\n"], output)

        token = CancellationToken()
        runner = ProcessRunner(terminate_timeout=0.5)
        errors: list[BaseException] = []

        def target() -> None:
            try:
                runner.run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    cwd=Path.cwd(),
                    env=None,
                    token=token,
                    on_output=lambda _line: None,
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target)
        thread.start()
        deadline = time.monotonic() + 2
        while runner.active_process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        process = runner.active_process
        self.assertIsNotNone(process)
        token.cancel()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], TaskCancelled)
        assert process is not None
        self.assertIsNotNone(process.poll())


class TaskEngineTests(unittest.TestCase):
    def test_engine_tracks_success_cancel_and_process_output(self) -> None:
        from toolbox.tasks import TaskEngine, TaskState

        engine = TaskEngine()
        finished = threading.Event()
        task_id = engine.start_callable(
            "计算", lambda _token, _emit: 42, on_done=lambda _snapshot: finished.set()
        )
        self.assertTrue(finished.wait(2))
        self.assertEqual(TaskState.SUCCEEDED, engine.snapshot(task_id).state)
        self.assertEqual(42, engine.snapshot(task_id).result)

        started = threading.Event()
        finished.clear()

        def worker(token, _emit):
            started.set()
            while True:
                token.raise_if_cancelled()
                time.sleep(0.01)

        cancelled_id = engine.start_callable(
            "取消", worker, on_done=lambda _snapshot: finished.set()
        )
        self.assertTrue(started.wait(1))
        self.assertTrue(engine.cancel(cancelled_id))
        self.assertTrue(finished.wait(2))
        self.assertEqual(TaskState.CANCELLED, engine.snapshot(cancelled_id).state)

        lines: list[str] = []
        finished.clear()
        process_id = engine.start_process(
            "输出",
            [sys.executable, "-c", "print('line')"],
            cwd=Path.cwd(),
            env=None,
            on_output=lines.append,
            on_done=lambda _snapshot: finished.set(),
        )
        self.assertTrue(finished.wait(2))
        self.assertEqual(TaskState.SUCCEEDED, engine.snapshot(process_id).state)
        self.assertEqual(["line\n"], lines)


class ErrorClassificationTests(unittest.TestCase):
    def test_errors_have_actionable_categories(self) -> None:
        from toolbox.tasks import ErrorCategory, ProcessExecutionError, classify_error

        cases = [
            (RuntimeError("登录状态已过期"), ErrorCategory.AUTHENTICATION),
            (PermissionError("locked"), ErrorCategory.PERMISSION),
            (FileNotFoundError("missing"), ErrorCategory.INPUT),
            (ProcessExecutionError(3), ErrorCategory.EXTERNAL_PROCESS),
        ]
        for exc, category in cases:
            with self.subTest(exc=exc):
                result = classify_error(exc)
                self.assertEqual(category, result.category)
                self.assertTrue(result.summary)
                self.assertTrue(result.advice)


if __name__ == "__main__":
    unittest.main()
