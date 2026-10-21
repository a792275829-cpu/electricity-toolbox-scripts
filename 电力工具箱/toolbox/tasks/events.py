from __future__ import annotations

from collections import deque
from threading import Lock

from .models import TaskEvent


class EventBuffer:
    def __init__(self, capacity: int = 0) -> None:
        self._queue = deque(maxlen=capacity or None)
        self._lock = Lock()
        self.dropped_count = 0

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def publish(self, event: TaskEvent) -> None:
        with self._lock:
            if self._queue.maxlen is not None and len(self._queue) == self._queue.maxlen:
                self.dropped_count += 1
            self._queue.append(event)

    def drain(self, limit: int | None = None) -> list[TaskEvent]:
        events: list[TaskEvent] = []
        with self._lock:
            while self._queue and (limit is None or len(events) < limit):
                events.append(self._queue.popleft())
        return events
