from __future__ import annotations

from collections import deque
from threading import Lock

from .models import TaskEvent


class EventBuffer:
    def __init__(self, max_pending: int = 10_000) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        # Tk reads snapshots and per-page logs directly. An absent event
        # subscriber must not retain every process output for the app's lifetime.
        self._queue: deque[TaskEvent] = deque(maxlen=max_pending)
        self._lock = Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def publish(self, event: TaskEvent) -> None:
        with self._lock:
            self._queue.append(event)

    def drain(self, limit: int | None = None) -> list[TaskEvent]:
        events: list[TaskEvent] = []
        with self._lock:
            while self._queue and (limit is None or len(events) < limit):
                events.append(self._queue.popleft())
        return events
