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
