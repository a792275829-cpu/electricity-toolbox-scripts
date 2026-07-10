# 工具箱稳定性底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有八个工具建立统一、可取消、可观察、可回收的后台任务底座，同时保持现有页面和业务脚本行为兼容。

**Architecture:** 新增独立的 `toolbox.tasks` 包，使用不可变任务快照、线程安全事件缓冲、取消令牌和跨平台子进程运行器。现有 `TaskRegistry` 变成兼容门面，`ToolPage` 改为消费统一任务 API，并把后台日志批量刷新到 Tk 主线程。

**Tech Stack:** Python 3.11、Tkinter/ttk、threading、subprocess、queue、dataclasses、enum、unittest。

## Global Constraints

- 保留八个工具现有的业务规则和计算口径。
- 保留配置文件、登录状态、浏览器 profile 和锁文件的现有位置与格式。
- 保留 Excel、Word、HTML 等输出内容、格式和默认位置。
- 保留现有独立启动入口以及根目录统一启动入口。
- 保留每个浏览器工具独立的 `auth_state.json`、`.browser-profile` 和锁文件，继续支持并行运行。
- 上传、覆盖和外部写入仍需用户确认，不得在测试中自动执行生产写入。
- 实施时保护工作区已有的未提交改动；本计划不修改已有改动的 `电力工具箱/tests/test_toolbox.py` 和 `集团每日上传/upload_daily_report.py`。
- 所有新行为遵循测试先行；每个生产代码步骤之前必须先观察对应测试按预期失败。
- `TaskRegistry`、`ToolPage.run_in_thread()` 和 `ToolPage.run_process()` 的现有调用签名保持兼容。

---

## File Structure

- Create: `电力工具箱/toolbox/tasks/__init__.py` — 导出稳定任务 API。
- Create: `电力工具箱/toolbox/tasks/models.py` — 状态、事件、快照、取消令牌和异常。
- Create: `电力工具箱/toolbox/tasks/events.py` — 线程安全批量事件缓冲。
- Create: `电力工具箱/toolbox/tasks/process.py` — 子进程输出、取消和强制回收。
- Create: `电力工具箱/toolbox/tasks/engine.py` — 任务启动、状态转换、订阅和统一关闭。
- Create: `电力工具箱/toolbox/tasks/errors.py` — 错误分类和界面建议。
- Create: `电力工具箱/tests/test_tasks.py` — 无 Tk 的任务底座测试。
- Create: `电力工具箱/tests/test_task_widgets.py` — Tk 可用时的批量日志与页面桥接测试。
- Modify: `电力工具箱/toolbox/runtime.py` — 保留 `TaskRegistry` 名称并转接新引擎。
- Modify: `电力工具箱/toolbox/widgets.py` — 批量日志、取消入口和统一结束处理。
- Modify: `电力工具箱/toolbox/app.py` — 关闭时展示任务清单并等待统一回收。

### Task 1: Task Models and Cancellation Contract

**Files:**
- Create: `电力工具箱/toolbox/tasks/__init__.py`
- Create: `电力工具箱/toolbox/tasks/models.py`
- Create: `电力工具箱/tests/test_tasks.py`

**Interfaces:**
- Produces: `TaskState`, `TaskEventKind`, `TaskEvent`, `TaskSnapshot`, `CancellationToken`, `TaskCancelled`.
- Consumes: only Python standard library.

- [ ] **Step 1: Write failing state and cancellation tests**

Create `电力工具箱/tests/test_tasks.py` with:

```python
from __future__ import annotations

import unittest


class TaskModelTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_starts_created(self) -> None:
        from toolbox.tasks.models import TaskSnapshot, TaskState

        snapshot = TaskSnapshot(task_id="task-1", name="示例任务")

        self.assertEqual(TaskState.CREATED, snapshot.state)
        with self.assertRaises(Exception):
            snapshot.state = TaskState.RUNNING  # type: ignore[misc]

    def test_cancellation_token_raises_task_cancelled(self) -> None:
        from toolbox.tasks.models import CancellationToken, TaskCancelled

        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.cancel()

        self.assertTrue(token.cancelled)
        with self.assertRaises(TaskCancelled):
            token.raise_if_cancelled()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run from `电力工具箱`:

```bash
python3 -m unittest tests.test_tasks.TaskModelTests -v
```

Expected: `ERROR` with `ModuleNotFoundError: No module named 'toolbox.tasks'`.

- [ ] **Step 3: Implement immutable models and cancellation**

Create `电力工具箱/toolbox/tasks/models.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from threading import Event
from typing import Any


class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
)


class TaskEventKind(str, Enum):
    STATE = "state"
    LOG = "log"
    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task_id: str
    kind: TaskEventKind
    message: str = ""
    progress: float | None = None
    payload: Any = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    name: str
    state: TaskState = TaskState.CREATED
    progress: float | None = None
    message: str = ""
    result: Any = None
    error: BaseException | None = None

    def transition(self, state: TaskState, **changes: Any) -> "TaskSnapshot":
        return replace(self, state=state, **changes)


class TaskCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled("任务已取消")
```

Create `电力工具箱/toolbox/tasks/__init__.py` with:

```python
from .models import (
    CancellationToken,
    TaskCancelled,
    TaskEvent,
    TaskEventKind,
    TaskSnapshot,
    TaskState,
)

__all__ = [
    "CancellationToken",
    "TaskCancelled",
    "TaskEvent",
    "TaskEventKind",
    "TaskSnapshot",
    "TaskState",
]
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tasks.TaskModelTests -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit the model contract**

```bash
git add 电力工具箱/toolbox/tasks/__init__.py \
  电力工具箱/toolbox/tasks/models.py \
  电力工具箱/tests/test_tasks.py
git commit -m "feat: add toolbox task model contract"
```

### Task 2: Thread-Safe Event Buffer

**Files:**
- Create: `电力工具箱/toolbox/tasks/events.py`
- Modify: `电力工具箱/toolbox/tasks/__init__.py`
- Modify: `电力工具箱/tests/test_tasks.py`

**Interfaces:**
- Consumes: `TaskEvent` from Task 1.
- Produces: `EventBuffer.publish(event)`, `EventBuffer.drain(limit=None)`, `EventBuffer.pending_count`.

- [ ] **Step 1: Write failing event ordering and batching tests**

Append to `电力工具箱/tests/test_tasks.py`:

```python
class EventBufferTests(unittest.TestCase):
    def test_drain_preserves_order_and_honors_limit(self) -> None:
        from toolbox.tasks.events import EventBuffer
        from toolbox.tasks.models import TaskEvent, TaskEventKind

        buffer = EventBuffer()
        for index in range(5):
            buffer.publish(
                TaskEvent("task-1", TaskEventKind.LOG, message=f"line-{index}")
            )

        first = buffer.drain(limit=2)
        second = buffer.drain()

        self.assertEqual(["line-0", "line-1"], [event.message for event in first])
        self.assertEqual(
            ["line-2", "line-3", "line-4"],
            [event.message for event in second],
        )
        self.assertEqual(0, buffer.pending_count)

    def test_buffer_accepts_ten_thousand_events_without_ui_calls(self) -> None:
        from toolbox.tasks.events import EventBuffer
        from toolbox.tasks.models import TaskEvent, TaskEventKind

        buffer = EventBuffer()
        for index in range(10_000):
            buffer.publish(TaskEvent("task-1", TaskEventKind.LOG, str(index)))

        self.assertEqual(10_000, buffer.pending_count)
        self.assertEqual(10_000, len(buffer.drain()))
```

- [ ] **Step 2: Run the event tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_tasks.EventBufferTests -v
```

Expected: `ERROR` with `No module named 'toolbox.tasks.events'`.

- [ ] **Step 3: Implement the event buffer**

Create `电力工具箱/toolbox/tasks/events.py` with:

```python
from __future__ import annotations

from queue import Empty, Queue

from .models import TaskEvent


class EventBuffer:
    def __init__(self) -> None:
        self._queue: Queue[TaskEvent] = Queue()

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def publish(self, event: TaskEvent) -> None:
        self._queue.put(event)

    def drain(self, limit: int | None = None) -> list[TaskEvent]:
        events: list[TaskEvent] = []
        while limit is None or len(events) < limit:
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                break
        return events
```

Export it from `电力工具箱/toolbox/tasks/__init__.py`:

```python
from .events import EventBuffer
```

and add `"EventBuffer"` to `__all__`.

- [ ] **Step 4: Run Task 1 and Task 2 tests**

Run:

```bash
python3 -m unittest tests.test_tasks.TaskModelTests tests.test_tasks.EventBufferTests -v
```

Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 5: Commit event buffering**

```bash
git add 电力工具箱/toolbox/tasks 电力工具箱/tests/test_tasks.py
git commit -m "feat: buffer toolbox task events"
```

### Task 3: Cancellable Cross-Platform Process Runner

**Files:**
- Create: `电力工具箱/toolbox/tasks/process.py`
- Modify: `电力工具箱/toolbox/tasks/__init__.py`
- Modify: `电力工具箱/tests/test_tasks.py`

**Interfaces:**
- Consumes: `CancellationToken`, `TaskCancelled`.
- Produces: `ProcessResult`, `ProcessRunner.run(command, cwd, env, token, on_output)`, `ProcessRunner.active_process`.

- [ ] **Step 1: Write failing success, nonzero-exit and cancellation tests**

Append to `电力工具箱/tests/test_tasks.py`:

```python
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory


class ProcessRunnerTests(unittest.TestCase):
    def test_runner_streams_output_and_returns_exit_code(self) -> None:
        from toolbox.tasks.models import CancellationToken
        from toolbox.tasks.process import ProcessRunner

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

    def test_runner_raises_for_nonzero_exit(self) -> None:
        from toolbox.tasks.models import CancellationToken
        from toolbox.tasks.process import ProcessExecutionError, ProcessRunner

        with self.assertRaisesRegex(ProcessExecutionError, "命令退出码 7"):
            ProcessRunner().run(
                [sys.executable, "-c", "raise SystemExit(7)"],
                cwd=Path.cwd(),
                env=None,
                token=CancellationToken(),
                on_output=lambda _line: None,
            )

    def test_runner_cancels_and_reaps_silent_process(self) -> None:
        from toolbox.tasks.models import CancellationToken, TaskCancelled
        from toolbox.tasks.process import ProcessRunner

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
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], TaskCancelled)
        assert process is not None
        self.assertIsNotNone(process.poll())
        self.assertIsNone(runner.active_process)
```

- [ ] **Step 2: Run process tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_tasks.ProcessRunnerTests -v
```

Expected: `ERROR` with `No module named 'toolbox.tasks.process'`.

- [ ] **Step 3: Implement process streaming and two-stage termination**

Create `电力工具箱/toolbox/tasks/process.py` with:

```python
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Mapping, Sequence

from .models import CancellationToken, TaskCancelled


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int


class ProcessExecutionError(RuntimeError):
    def __init__(self, returncode: int) -> None:
        super().__init__(f"命令退出码 {returncode}")
        self.returncode = returncode


class ProcessRunner:
    def __init__(self, *, terminate_timeout: float = 2.0) -> None:
        self.terminate_timeout = terminate_timeout
        self._active_process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @property
    def active_process(self) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._active_process

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None,
        token: CancellationToken,
        on_output: Callable[[str], None],
    ) -> ProcessResult:
        lines: Queue[str | None] = Queue()
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env) if env is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            bufsize=1,
        )
        with self._lock:
            self._active_process = process

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        stream_closed = False
        try:
            while process.poll() is None or not stream_closed:
                if token.cancelled and process.poll() is None:
                    self.terminate(process)
                try:
                    line = lines.get(timeout=0.05)
                except Empty:
                    continue
                if line is None:
                    stream_closed = True
                else:
                    on_output(line)
            returncode = process.wait()
            if token.cancelled:
                raise TaskCancelled("任务已取消")
            if returncode != 0:
                raise ProcessExecutionError(returncode)
            return ProcessResult(returncode)
        finally:
            if process.poll() is None:
                self.terminate(process)
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=1)
            with self._lock:
                self._active_process = None

    def terminate(self, process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=self.terminate_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.terminate_timeout)
        except OSError:
            pass
```

Export `ProcessExecutionError`, `ProcessResult`, and `ProcessRunner` from `toolbox/tasks/__init__.py`.

- [ ] **Step 4: Run process tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tasks.ProcessRunnerTests -v
```

Expected: `Ran 3 tests` and `OK`; the cancellation test completes in under 5 seconds.

- [ ] **Step 5: Commit the process runner**

```bash
git add 电力工具箱/toolbox/tasks 电力工具箱/tests/test_tasks.py
git commit -m "feat: add cancellable process runner"
```

### Task 4: Unified Task Engine and TaskRegistry Compatibility

**Files:**
- Create: `电力工具箱/toolbox/tasks/engine.py`
- Modify: `电力工具箱/toolbox/tasks/__init__.py`
- Modify: `电力工具箱/toolbox/runtime.py:190-234`
- Modify: `电力工具箱/tests/test_tasks.py`

**Interfaces:**
- Consumes: models, `EventBuffer`, `ProcessRunner`, current `utf8_environment()`.
- Produces: `TaskEngine.start_callable()`, `start_process()`, `cancel()`, `snapshots()`, `active_snapshots()`, `shutdown()`.
- Preserves: all existing `TaskRegistry` methods and constructor behavior.

- [ ] **Step 1: Write failing engine lifecycle tests**

Append to `电力工具箱/tests/test_tasks.py`:

```python
class TaskEngineTests(unittest.TestCase):
    def test_callable_transitions_to_succeeded_and_emits_result(self) -> None:
        from toolbox.tasks.engine import TaskEngine
        from toolbox.tasks.models import TaskState

        engine = TaskEngine()
        finished = threading.Event()
        task_id = engine.start_callable(
            "计算任务",
            lambda _token, emit: 42,
            on_done=lambda _snapshot: finished.set(),
        )

        self.assertTrue(finished.wait(2))
        snapshot = engine.snapshot(task_id)
        self.assertEqual(TaskState.SUCCEEDED, snapshot.state)
        self.assertEqual(42, snapshot.result)

    def test_callable_cancellation_reaches_cancelled(self) -> None:
        from toolbox.tasks.engine import TaskEngine
        from toolbox.tasks.models import TaskState

        engine = TaskEngine()
        started = threading.Event()
        finished = threading.Event()

        def worker(token, _emit):
            started.set()
            while True:
                token.raise_if_cancelled()
                time.sleep(0.01)

        task_id = engine.start_callable(
            "可取消任务", worker, on_done=lambda _snapshot: finished.set()
        )
        self.assertTrue(started.wait(1))
        self.assertTrue(engine.cancel(task_id))
        self.assertTrue(finished.wait(2))

        self.assertEqual(TaskState.CANCELLED, engine.snapshot(task_id).state)

    def test_shutdown_cancels_owned_process_without_touching_others(self) -> None:
        from toolbox.tasks.engine import TaskEngine

        engine = TaskEngine()
        task_id = engine.start_process(
            "长任务",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=Path.cwd(),
            env=None,
        )
        deadline = time.monotonic() + 2
        while not engine.active_snapshots() and time.monotonic() < deadline:
            time.sleep(0.01)

        engine.shutdown(timeout=5)

        self.assertFalse(engine.has_running_tasks())
        self.assertIn(engine.snapshot(task_id).state.value, {"cancelled", "failed"})
```

- [ ] **Step 2: Run engine tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_tasks.TaskEngineTests -v
```

Expected: `ERROR` with `No module named 'toolbox.tasks.engine'`.

- [ ] **Step 3: Implement TaskEngine with locked snapshots and one worker per task**

Implement `电力工具箱/toolbox/tasks/engine.py` with:

```python
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import EventBuffer
from .models import (
    CancellationToken,
    TaskCancelled,
    TaskEvent,
    TaskEventKind,
    TaskSnapshot,
    TaskState,
)
from .process import ProcessRunner


Worker = Callable[[CancellationToken, Callable[[TaskEvent], None]], Any]
DoneCallback = Callable[[TaskSnapshot], None]
ACTIVE_STATES = frozenset(
    {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING}
)


class TaskEngine:
    def __init__(self) -> None:
        self._events = EventBuffer()
        self._snapshots: dict[str, TaskSnapshot] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    @property
    def events(self) -> EventBuffer:
        return self._events

    def start_callable(
        self,
        name: str,
        worker: Worker,
        *,
        on_done: DoneCallback | None = None,
        _task_id: str | None = None,
    ) -> str:
        task_id = _task_id or uuid4().hex
        token = CancellationToken()
        created = TaskSnapshot(task_id=task_id, name=name)

        def emit(event: TaskEvent) -> None:
            normalized = event if event.task_id == task_id else replace(event, task_id=task_id)
            self._events.publish(normalized)

        def transition(state: TaskState, **changes: Any) -> TaskSnapshot:
            with self._lock:
                current = self._snapshots[task_id]
                if state is TaskState.RUNNING and current.state is TaskState.CANCELLING:
                    return current
                updated = current.transition(state, **changes)
                self._snapshots[task_id] = updated
            emit(TaskEvent(task_id, TaskEventKind.STATE, message=state.value))
            return updated

        def target() -> None:
            try:
                token.raise_if_cancelled()
                transition(TaskState.RUNNING)
                token.raise_if_cancelled()
                result = worker(token, emit)
                token.raise_if_cancelled()
            except TaskCancelled as exc:
                final = transition(
                    TaskState.CANCELLED,
                    message=str(exc),
                    error=exc,
                )
            except BaseException as exc:
                emit(
                    TaskEvent(
                        task_id,
                        TaskEventKind.ERROR,
                        message=str(exc),
                        payload=exc,
                    )
                )
                final = transition(
                    TaskState.FAILED,
                    message=str(exc),
                    error=exc,
                )
            else:
                emit(TaskEvent(task_id, TaskEventKind.RESULT, payload=result))
                final = transition(TaskState.SUCCEEDED, result=result)
            finally:
                with self._lock:
                    self._threads.pop(task_id, None)
            if on_done is not None:
                on_done(final)

        thread = threading.Thread(
            target=target,
            name=f"toolbox-task-{task_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._snapshots[task_id] = created
            self._tokens[task_id] = token
            self._threads[task_id] = thread
        emit(TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CREATED.value))
        thread.start()
        return task_id

    def start_process(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None,
        on_done: DoneCallback | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> str:
        task_id = uuid4().hex
        command_copy = list(command)

        def worker(
            token: CancellationToken,
            emit: Callable[[TaskEvent], None],
        ) -> int:
            def handle_output(line: str) -> None:
                emit(TaskEvent(task_id, TaskEventKind.LOG, message=line))
                if on_output is not None:
                    on_output(line)

            result = ProcessRunner().run(
                command_copy,
                cwd=cwd,
                env=env,
                token=token,
                on_output=handle_output,
            )
            return result.returncode

        return self.start_callable(
            name,
            worker,
            on_done=on_done,
            _task_id=task_id,
        )

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            snapshot = self._snapshots.get(task_id)
            if snapshot is None or snapshot.state not in ACTIVE_STATES:
                return False
            self._snapshots[task_id] = snapshot.transition(TaskState.CANCELLING)
            token = self._tokens[task_id]
        token.cancel()
        self._events.publish(
            TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CANCELLING.value)
        )
        return True

    def snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            return self._snapshots[task_id]

    def snapshots(self) -> tuple[TaskSnapshot, ...]:
        with self._lock:
            return tuple(self._snapshots.values())

    def active_snapshots(self) -> tuple[TaskSnapshot, ...]:
        return tuple(
            snapshot
            for snapshot in self.snapshots()
            if snapshot.state in ACTIVE_STATES
        )

    def has_running_tasks(self) -> bool:
        return bool(self.active_snapshots())

    def shutdown(self, timeout: float = 5.0) -> None:
        for snapshot in self.active_snapshots():
            self.cancel(snapshot.task_id)
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                threads = tuple(self._threads.values())
            if not threads:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for thread in threads:
                thread.join(timeout=min(0.1, remaining))
```

Export `TaskEngine` from `toolbox/tasks/__init__.py`.

- [ ] **Step 4: Replace TaskRegistry internals with a compatibility subclass**

In `电力工具箱/toolbox/runtime.py`, import `TaskEngine` and replace the current class with:

```python
class TaskRegistry(TaskEngine):
    """Backward-compatible task owner used by existing pages and app code."""

    def __init__(self) -> None:
        super().__init__()
        self._legacy_processes: set[subprocess.Popen[str]] = set()
        self._legacy_threads: set[threading.Thread] = set()
        self._legacy_lock = threading.Lock()

    def register_process(self, process: subprocess.Popen[str]) -> None:
        with self._legacy_lock:
            self._legacy_processes.add(process)

    def unregister_process(self, process: subprocess.Popen[str]) -> None:
        with self._legacy_lock:
            self._legacy_processes.discard(process)

    def register_thread(self, thread: threading.Thread) -> None:
        with self._legacy_lock:
            self._legacy_threads.add(thread)

    def unregister_thread(self, thread: threading.Thread) -> None:
        with self._legacy_lock:
            self._legacy_threads.discard(thread)

    def has_running_tasks(self) -> bool:
        with self._legacy_lock:
            self._legacy_processes = {
                process for process in self._legacy_processes if process.poll() is None
            }
            self._legacy_threads = {
                thread for thread in self._legacy_threads if thread.is_alive()
            }
            legacy_running = bool(self._legacy_processes or self._legacy_threads)
        return legacy_running or super().has_running_tasks()

    def terminate_all(self) -> None:
        self.shutdown(timeout=5.0)
        with self._legacy_lock:
            processes = list(self._legacy_processes)
            self._legacy_processes.clear()
        for process in processes:
            if process.poll() is None:
                ProcessRunner().terminate(process)
```

Import `ProcessRunner` alongside `TaskEngine`. This preserves old page behavior until Task 6 migrates it.

- [ ] **Step 5: Run engine and existing registry tests**

Run:

```bash
python3 -m unittest tests.test_tasks.TaskEngineTests -v
python3 -m unittest tests.test_toolbox.RuntimeTests.test_task_registry_tracks_and_terminates_owned_process -v
```

Expected: both commands end with `OK`.

- [ ] **Step 6: Commit the engine and compatibility layer**

```bash
git add 电力工具箱/toolbox/tasks \
  电力工具箱/toolbox/runtime.py \
  电力工具箱/tests/test_tasks.py
git commit -m "refactor: unify toolbox task lifecycle"
```

### Task 5: Error Classification and Recovery Advice

**Files:**
- Create: `电力工具箱/toolbox/tasks/errors.py`
- Modify: `电力工具箱/toolbox/tasks/__init__.py`
- Modify: `电力工具箱/tests/test_tasks.py`

**Interfaces:**
- Consumes: Python exceptions and `ProcessExecutionError`.
- Produces: `ErrorCategory`, `UserFacingError`, `classify_error(exc)`.

- [ ] **Step 1: Write failing classification tests**

Append:

```python
class ErrorClassificationTests(unittest.TestCase):
    def test_classifies_login_permission_and_process_errors(self) -> None:
        from toolbox.tasks.errors import ErrorCategory, classify_error
        from toolbox.tasks.process import ProcessExecutionError

        cases = [
            (RuntimeError("登录状态已过期"), ErrorCategory.AUTHENTICATION),
            (PermissionError("book.xlsx is locked"), ErrorCategory.PERMISSION),
            (FileNotFoundError("missing.xlsx"), ErrorCategory.INPUT),
            (ProcessExecutionError(3), ErrorCategory.EXTERNAL_PROCESS),
        ]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                error = classify_error(exc)
                self.assertEqual(expected, error.category)
                self.assertTrue(error.summary)
                self.assertTrue(error.advice)

    def test_unknown_error_preserves_original_exception(self) -> None:
        from toolbox.tasks.errors import ErrorCategory, classify_error

        exc = RuntimeError("unexpected marker")
        error = classify_error(exc)

        self.assertEqual(ErrorCategory.UNKNOWN, error.category)
        self.assertIs(exc, error.cause)
```

- [ ] **Step 2: Run classification tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_tasks.ErrorClassificationTests -v
```

Expected: `ERROR` with `No module named 'toolbox.tasks.errors'`.

- [ ] **Step 3: Implement deterministic error mapping**

Create `电力工具箱/toolbox/tasks/errors.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import TaskCancelled
from .process import ProcessExecutionError


class ErrorCategory(str, Enum):
    INPUT = "input"
    DEPENDENCY = "dependency"
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    PERMISSION = "permission"
    EXTERNAL_PROCESS = "external_process"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UserFacingError:
    category: ErrorCategory
    summary: str
    advice: str
    cause: BaseException


def classify_error(exc: BaseException) -> UserFacingError:
    text = str(exc).lower()
    if isinstance(exc, TaskCancelled):
        return UserFacingError(ErrorCategory.CANCELLED, "任务已取消", "可以调整参数后重新运行。", exc)
    if isinstance(exc, FileNotFoundError):
        return UserFacingError(ErrorCategory.INPUT, "找不到输入文件或程序文件", "请重新选择文件，并确认项目目录完整。", exc)
    if isinstance(exc, PermissionError):
        return UserFacingError(ErrorCategory.PERMISSION, "文件无权访问或正被占用", "请关闭占用该文件的 Excel/WPS 后重试。", exc)
    if isinstance(exc, ProcessExecutionError):
        return UserFacingError(ErrorCategory.EXTERNAL_PROCESS, str(exc), "请查看任务日志中的最后一段输出。", exc)
    if any(marker in text for marker in ("登录", "login", "auth", "认证")):
        return UserFacingError(ErrorCategory.AUTHENTICATION, "登录状态不可用", "请先执行登录或刷新登录状态，再手动重试。", exc)
    if any(marker in text for marker in ("timeout", "timed out", "network", "socket", "连接")):
        return UserFacingError(ErrorCategory.NETWORK, "网络请求失败", "请检查网络和代理；读取任务可重试，上传任务需重新确认。", exc)
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return UserFacingError(ErrorCategory.DEPENDENCY, "缺少运行依赖", "请重新运行 setup_macos.command。", exc)
    return UserFacingError(ErrorCategory.UNKNOWN, str(exc) or exc.__class__.__name__, "请查看完整日志和 traceback。", exc)
```

Export these symbols from `toolbox/tasks/__init__.py`.

- [ ] **Step 4: Run classification tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tasks.ErrorClassificationTests -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit error handling**

```bash
git add 电力工具箱/toolbox/tasks 电力工具箱/tests/test_tasks.py
git commit -m "feat: classify toolbox task failures"
```

### Task 6: ToolPage Migration and Batched Tk Logging

**Files:**
- Create: `电力工具箱/tests/test_task_widgets.py`
- Modify: `电力工具箱/toolbox/widgets.py`

**Interfaces:**
- Consumes: `TaskRegistry.start_callable()`, `start_process()`, `TaskSnapshot`, `TaskState`, `classify_error()`.
- Preserves: existing `run_in_thread(worker, status, on_success)` and `run_process(command, cwd, status, on_success)` call signatures.
- Produces: `cancel_current_task()`, `current_task_id`, 100 ms log draining, 5,000 visible-line limit.

- [ ] **Step 1: Write failing Tk log and cancellation bridge tests**

Create `电力工具箱/tests/test_task_widgets.py`:

```python
from __future__ import annotations

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

    def test_background_log_writes_are_batched_and_trimmed(self) -> None:
        def produce() -> None:
            for index in range(10_000):
                self.page.append_log(f"line-{index}")

        thread = threading.Thread(target=produce)
        thread.start()
        thread.join()
        self.page._drain_log_queue()

        lines = self.page.log_text.get("1.0", "end-1c").splitlines()
        self.assertEqual(5_000, len(lines))
        self.assertEqual("line-9999", lines[-1])

    def test_run_in_thread_uses_registry_and_can_cancel(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(1)

        self.page.run_in_thread(worker, status="运行中")
        self.assertTrue(started.wait(1))
        self.assertIsNotNone(self.page.current_task_id)
        self.assertEqual("normal", str(self.page.cancel_button.cget("state")))

        self.page.cancel_current_task()
        self.assertEqual("正在取消...", self.page.status_var.get())
        release.set()
```

- [ ] **Step 2: Run widget tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_task_widgets -v
```

Expected: tests fail because `_drain_log_queue`, `current_task_id`, and `cancel_current_task` do not exist.

- [ ] **Step 3: Implement queue-based log draining**

In `ToolPage.__init__`, add:

```python
self._log_queue: queue.Queue[str] = queue.Queue()
self._log_after_id: str | None = None
self._max_log_lines = 5_000
self.current_task_id: str | None = None
self._schedule_log_drain()
```

Add a cancel button next to the footer progress bar:

```python
self.cancel_button = ttk.Button(
    footer,
    text="取消任务",
    command=self.cancel_current_task,
    state="disabled",
)
self.cancel_button.grid(row=0, column=1, padx=(8, 0))
```

At the end of `set_busy()`, keep its state synchronized:

```python
self.cancel_button.configure(state="normal" if busy else "disabled")
```

Replace `append_log()` and add helpers:

```python
def append_log(self, text: str) -> None:
    self._log_queue.put(text.rstrip("\n") + "\n")

def _schedule_log_drain(self) -> None:
    try:
        self._log_after_id = self.after(100, self._drain_log_queue)
    except tk.TclError:
        self._log_after_id = None

def _drain_log_queue(self) -> None:
    chunks: list[str] = []
    while True:
        try:
            chunks.append(self._log_queue.get_nowait())
        except queue.Empty:
            break
    if chunks:
        self.log_text.insert("end", "".join(chunks))
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        visible_lines = max(0, line_count - 1)
        excess = visible_lines - self._max_log_lines
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.see("end")
    self._schedule_log_drain()
```

Replace `clear_log()` so it drains pending queue entries before deleting the widget contents:

```python
def clear_log(self) -> None:
    while True:
        try:
            self._log_queue.get_nowait()
        except queue.Empty:
            break
    self.log_text.delete("1.0", "end")
```

Override `destroy()` to cancel the scheduled callback before `super().destroy()`.

- [ ] **Step 4: Migrate page execution to TaskRegistry while preserving call signatures**

Change `run_in_thread()` to wrap the existing zero-argument worker:

```python
def run_in_thread(self, worker, *, status, on_success=None) -> None:
    if self.busy:
        return
    self.set_busy(True, status)

    def task_worker(token, _emit):
        token.raise_if_cancelled()
        result = worker()
        token.raise_if_cancelled()
        return result

    def done(snapshot):
        self.after(0, self._finish_snapshot, snapshot, on_success)

    self.current_task_id = self.registry.start_callable(
        status, task_worker, on_done=done
    )
```

Change `run_process()` to call `registry.start_process()` directly, emit the command line through `append_log()`, and pass `on_output=self.append_log` so each subprocess line reaches this page's batched log queue:

```python
def run_process(
    self,
    command: list[str],
    *,
    cwd: Path,
    status: str,
    on_success: Callable[[int], None] | None = None,
) -> None:
    if self.busy:
        return
    self.clear_log()
    self.append_log(f"> {' '.join(command)}")
    self.set_busy(True, status)

    def done(snapshot: TaskSnapshot) -> None:
        self.after(0, self._finish_snapshot, snapshot, on_success)

    self.current_task_id = self.registry.start_process(
        status,
        command,
        cwd=cwd,
        env=utf8_environment(),
        on_done=done,
        on_output=self.append_log,
    )
```

Add:

```python
def cancel_current_task(self) -> None:
    if self.current_task_id and self.registry.cancel(self.current_task_id):
        self.set_busy(True, "正在取消...")

def _finish_snapshot(self, snapshot, callback) -> None:
    self.current_task_id = None
    if snapshot.state is TaskState.SUCCEEDED:
        self.set_busy(False, "已完成")
        if callback is not None:
            callback(snapshot.result)
        return
    if snapshot.state is TaskState.CANCELLED:
        self.set_busy(False, "已取消")
        self.append_log("任务已取消。")
        return
    error = classify_error(snapshot.error or RuntimeError("未知错误"))
    self.append_log(f"失败：{error.summary}")
    self.append_log(f"建议：{error.advice}")
    self.append_log("".join(traceback.format_exception(snapshot.error)))
    self.set_busy(False, "执行失败")
    messagebox.showerror("执行失败", f"{error.summary}\n\n{error.advice}", parent=self)
```

Update imports: add `queue`; remove direct `subprocess` use; import `TaskSnapshot`, `TaskState`, and `classify_error` from `.tasks`.

- [ ] **Step 5: Run widget and existing page adapter tests**

Run:

```bash
python3 -m unittest tests.test_task_widgets -v
python3 -m unittest tests.test_toolbox.PageAdapterTests -v
```

Expected: both commands end with `OK`; no production upload is executed.

- [ ] **Step 6: Commit the page bridge**

```bash
git add 电力工具箱/toolbox/widgets.py \
  电力工具箱/tests/test_task_widgets.py
git commit -m "refactor: run toolbox pages through task engine"
```

### Task 7: Safe Application Shutdown and Stage-One Verification

**Files:**
- Modify: `电力工具箱/toolbox/app.py:124-134`
- Modify: `电力工具箱/tests/test_task_widgets.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `TaskRegistry.active_snapshots()` and `terminate_all()`.
- Produces: close confirmation containing active task names; README cancellation behavior.

- [ ] **Step 1: Write failing close-summary test**

Append to `test_task_widgets.py`:

```python
class AppCloseTests(unittest.TestCase):
    def test_close_confirmation_lists_active_task_names(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        root.destroy()

        from unittest import mock
        from toolbox.app import ToolboxApp

        app = ToolboxApp(page_factories={
            name: (lambda parent, _paths, registry: __import__(
                "toolbox.widgets", fromlist=["ToolPage"]
            ).ToolPage(parent, registry=registry))
            for name in __import__("toolbox.app", fromlist=["PAGE_NAMES"]).PAGE_NAMES
        })
        def worker(token, _emit):
            while True:
                token.raise_if_cancelled()
                time.sleep(0.01)

        task_id = app.registry.start_callable("关闭测试任务", worker)
        with mock.patch("toolbox.app.messagebox.askyesno", return_value=False) as ask:
            app._on_close()

        self.assertIn("关闭测试任务", ask.call_args.args[1])
        app.registry.cancel(task_id)
        app.registry.terminate_all()
        app.destroy()
```

- [ ] **Step 2: Run the close test and verify RED**

Run:

```bash
python3 -m unittest tests.test_task_widgets.AppCloseTests -v
```

Expected: failure because the current confirmation text does not include `关闭测试任务`.

- [ ] **Step 3: Include active task names and wait for owned-task cleanup**

Replace `_on_close()` with:

```python
def _on_close(self) -> None:
    active = self.registry.active_snapshots()
    if active or self.registry.has_running_tasks():
        names = "\n".join(f"• {task.name}" for task in active) or "• 后台任务"
        confirmed = messagebox.askyesno(
            "仍有任务运行",
            "以下任务仍在运行：\n\n"
            f"{names}\n\n"
            "关闭工具箱将取消由工具箱启动的任务，确定关闭吗？",
            parent=self,
        )
        if not confirmed:
            return
    self.registry.terminate_all()
    self.destroy()
```

- [ ] **Step 4: Document task cancellation and side-effect retry rules**

Add a `后台任务与取消` subsection to `README.md` stating exactly:

```markdown
## 后台任务与取消

工具箱中的耗时任务在后台运行，切换功能页不会中断任务。运行中的任务可以取消；关闭工具箱时会列出仍在运行的任务，确认后只终止由本工具箱启动的线程或子进程。

数据读取任务遇到明确的瞬时网络错误时可以有限重试。上传、覆盖和外部写入不会自动重试；每次重试前仍需确认日期、文件和目标。
```

- [ ] **Step 5: Run the complete stage verification**

Run from `电力工具箱`:

```bash
python3 -m unittest tests.test_tasks tests.test_task_widgets -v
python3 -m unittest discover -s tests
python3 -m py_compile toolbox/runtime.py toolbox/widgets.py toolbox/app.py toolbox/tasks/*.py
```

Then from repository root:

```bash
TOOLBOX_SMOKE=1 ./电力工具箱.command
git diff --check
```

Expected:

- New task tests pass.
- Existing suite reports zero failures and zero errors; environment-dependent skips are allowed.
- `py_compile` produces no output and exits 0.
- Launcher smoke prints `TOOLBOX_SCRIPT=` and exits 0.
- `git diff --check` produces no output.

- [ ] **Step 6: Review requirement coverage**

Confirm all items before committing:

```text
[ ] Existing eight tools still use the same page APIs.
[ ] 10,000 queued log events are handled in one drain cycle and visible logs are capped.
[ ] Thread tasks reach succeeded, failed or cancelled terminal states.
[ ] Silent child processes can be cancelled and reaped within 5 seconds.
[ ] Closing lists active unified tasks and terminates only owned work.
[ ] User-facing failures include summary and recovery advice.
[ ] No production upload or external write was executed by tests.
[ ] Existing uncommitted user files were not staged or overwritten.
```

- [ ] **Step 7: Commit stage-one integration**

```bash
git add README.md \
  电力工具箱/toolbox/app.py \
  电力工具箱/tests/test_task_widgets.py
git diff --cached --check
git commit -m "feat: harden toolbox task execution"
```

## Next Plans

After this plan is fully verified, write and execute these plans in order:

1. `2026-07-10-toolbox-workbench-ui.md` — 工作台首页、分组导航、任务中心、主题和结果卡片。
2. `2026-07-10-toolbox-performance.md` — 冷启动基线、页面懒加载、动态模块缓存和非阻塞预检。
3. `2026-07-10-toolbox-modularization.md` — 工具目录、八个适配器、页面模块拆分和 `pages.py` 兼容导出。

Each next plan must repeat the TDD, complete-suite, launcher-smoke, and staged-file safety requirements from this plan.
