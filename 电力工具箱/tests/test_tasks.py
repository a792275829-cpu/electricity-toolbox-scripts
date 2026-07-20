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
    def test_parallel_processes_have_isolated_input_and_cancellation(self) -> None:
        from toolbox.tasks import CancellationToken, ProcessRunner, TaskCancelled

        tokens = [CancellationToken(), CancellationToken()]
        runners = [ProcessRunner(terminate_timeout=0.5), ProcessRunner(terminate_timeout=0.5)]
        outputs: list[list[str]] = [[], []]
        results = [None, None]
        errors: list[BaseException | None] = [None, None]

        def target(index: int, delay: float) -> None:
            code = (
                "import sys,time; "
                "print('stdin-eof=' + str(sys.stdin.read() == ''), flush=True); "
                f"time.sleep({delay}); print('done', flush=True)"
            )
            try:
                results[index] = runners[index].run(
                    [sys.executable, "-c", code],
                    cwd=Path.cwd(),
                    env=None,
                    token=tokens[index],
                    on_output=outputs[index].append,
                )
            except BaseException as exc:
                errors[index] = exc

        threads = [
            threading.Thread(target=target, args=(0, 30)),
            threading.Thread(target=target, args=(1, 0.2)),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 2
        while any(runner.active_process is None for runner in runners) and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertTrue(all(runner.active_process is not None for runner in runners))
        tokens[0].cancel()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertIsInstance(errors[0], TaskCancelled)
        self.assertIsNone(errors[1])
        self.assertIsNotNone(results[1])
        self.assertIn("stdin-eof=True\n", outputs[1])
        self.assertIn("done\n", outputs[1])

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

    @unittest.skipUnless(sys.platform != "win32", "POSIX process group assertion")
    def test_cancellation_reaps_child_process_group(self) -> None:
        import os
        import tempfile
        from toolbox.tasks import CancellationToken, ProcessRunner, TaskCancelled

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            code = (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"open({str(pid_file)!r},'w').write(str(p.pid)); time.sleep(30)"
            )
            token = CancellationToken()
            runner = ProcessRunner(terminate_timeout=0.5)
            errors: list[BaseException] = []
            thread = threading.Thread(target=lambda: self._run_captured(runner, code, token, errors))
            thread.start()
            deadline = time.monotonic() + 2
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            child_pid = int(pid_file.read_text())
            token.cancel()
            thread.join(timeout=5)
            self.assertIsInstance(errors[0], TaskCancelled)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    @staticmethod
    def _run_captured(runner, code, token, errors) -> None:
        try:
            runner.run([sys.executable, "-c", code], cwd=Path.cwd(), env=None, token=token, on_output=lambda _line: None)
        except BaseException as exc:
            errors.append(exc)


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

    def test_non_cancellable_task_rejects_cancel_and_shutdown_reports_it(self) -> None:
        from toolbox.tasks import TaskEngine, TaskState

        engine = TaskEngine()
        release = threading.Event()
        task_id = engine.start_callable(
            "写入阶段", lambda _token, _emit: release.wait(1), cancellable=False
        )
        self.assertFalse(engine.cancel(task_id))
        remaining = engine.shutdown(timeout=0.01)
        self.assertEqual((task_id,), tuple(item.task_id for item in remaining))
        release.set()

    def test_success_cannot_overwrite_accepted_cancellation(self) -> None:
        from toolbox.tasks import TaskEngine, TaskState

        engine = TaskEngine()
        for _ in range(100):
            release = threading.Event()
            task_id = engine.start_callable("竞态", lambda _token, _emit: release.wait(1))
            accepted = engine.cancel(task_id)
            release.set()
            deadline = time.monotonic() + 1
            while engine.snapshot(task_id).state in {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING} and time.monotonic() < deadline:
                time.sleep(0.001)
            if accepted:
                self.assertEqual(TaskState.CANCELLED, engine.snapshot(task_id).state)


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


class TaskRegistryTests(unittest.TestCase):
    def test_non_cancellable_registered_thread_blocks_shutdown_until_finished(self) -> None:
        from toolbox.runtime import TaskRegistry

        registry = TaskRegistry()
        started = threading.Event()
        release = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(timeout=5)

        thread = threading.Thread(target=worker, name="external-write", daemon=True)
        registry.register_thread(thread)
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertTrue(registry.has_running_tasks())

        remaining = registry.terminate_all()
        self.assertIn(thread, remaining)

        release.set()
        thread.join(timeout=2)
        self.assertFalse(registry.has_running_tasks())
        self.assertEqual((), registry.terminate_all())


if __name__ == "__main__":
    unittest.main()
