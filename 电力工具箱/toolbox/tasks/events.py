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
