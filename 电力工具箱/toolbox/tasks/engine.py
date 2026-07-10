from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import EventBuffer
from .models import CancellationToken, TaskCancelled, TaskEvent, TaskEventKind, TaskSnapshot, TaskState
from .process import ProcessRunner

Worker = Callable[[CancellationToken, Callable[[TaskEvent], None]], Any]
DoneCallback = Callable[[TaskSnapshot], None]
ACTIVE_STATES = frozenset({TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING})


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

    def start_callable(self, name: str, worker: Worker, *, on_done: DoneCallback | None = None, _task_id: str | None = None) -> str:
        task_id = _task_id or uuid4().hex
        token = CancellationToken()
        created = TaskSnapshot(task_id=task_id, name=name)

        def emit(event: TaskEvent) -> None:
            self._events.publish(event if event.task_id == task_id else replace(event, task_id=task_id))

        def transition(state: TaskState, **changes: Any) -> TaskSnapshot:
            with self._lock:
                current = self._snapshots[task_id]
                updated = current.transition(state, **changes)
                self._snapshots[task_id] = updated
            emit(TaskEvent(task_id, TaskEventKind.STATE, message=state.value))
            return updated

        def target() -> None:
            try:
                token.raise_if_cancelled()
                transition(TaskState.RUNNING)
                result = worker(token, emit)
                token.raise_if_cancelled()
            except TaskCancelled as exc:
                final = transition(TaskState.CANCELLED, message=str(exc), error=exc)
            except BaseException as exc:
                emit(TaskEvent(task_id, TaskEventKind.ERROR, message=str(exc), payload=exc))
                final = transition(TaskState.FAILED, message=str(exc), error=exc)
            else:
                emit(TaskEvent(task_id, TaskEventKind.RESULT, payload=result))
                final = transition(TaskState.SUCCEEDED, result=result)
            finally:
                with self._lock:
                    self._threads.pop(task_id, None)
            if on_done is not None:
                on_done(final)

        thread = threading.Thread(target=target, name=f"toolbox-task-{task_id[:8]}", daemon=True)
        with self._lock:
            self._snapshots[task_id] = created
            self._tokens[task_id] = token
            self._threads[task_id] = thread
        emit(TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CREATED.value))
        thread.start()
        return task_id

    def start_process(self, name: str, command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None, on_done: DoneCallback | None = None, on_output: Callable[[str], None] | None = None) -> str:
        task_id = uuid4().hex

        def worker(token: CancellationToken, emit: Callable[[TaskEvent], None]) -> int:
            def output(line: str) -> None:
                emit(TaskEvent(task_id, TaskEventKind.LOG, message=line))
                if on_output is not None:
                    on_output(line)
            return ProcessRunner().run(list(command), cwd=cwd, env=env, token=token, on_output=output).returncode

        return self.start_callable(name, worker, on_done=on_done, _task_id=task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            snapshot = self._snapshots.get(task_id)
            if snapshot is None or snapshot.state not in ACTIVE_STATES:
                return False
            self._snapshots[task_id] = snapshot.transition(TaskState.CANCELLING)
            token = self._tokens[task_id]
        token.cancel()
        self._events.publish(TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CANCELLING.value))
        return True

    def snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            return self._snapshots[task_id]

    def snapshots(self) -> tuple[TaskSnapshot, ...]:
        with self._lock:
            return tuple(self._snapshots.values())

    def active_snapshots(self) -> tuple[TaskSnapshot, ...]:
        return tuple(item for item in self.snapshots() if item.state in ACTIVE_STATES)

    def has_running_tasks(self) -> bool:
        return bool(self.active_snapshots())

    def shutdown(self, timeout: float = 5.0) -> None:
        for snapshot in self.active_snapshots():
            self.cancel(snapshot.task_id)
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                threads = tuple(self._threads.values())
            if not threads or time.monotonic() >= deadline:
                return
            for thread in threads:
                thread.join(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
