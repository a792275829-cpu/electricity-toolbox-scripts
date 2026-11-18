"""Cancellation checkpoints for work that must finish its current commit."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path

REQUEST_ENV = "TOOLBOX_CANCEL_REQUEST"
EXIT_CANCELLED = 130
_local_request = ContextVar("toolbox_cancel_requested", default=None)


class CancellationRequested(SystemExit):
    def __init__(self):
        super().__init__(EXIT_CANCELLED)


def process_cancellation_requested() -> bool:
    path = os.environ.get(REQUEST_ENV)
    return bool(path and Path(path).is_file())


@contextmanager
def cancellation_scope(requested):
    scope = _local_request.set(requested)
    try:
        yield
    finally:
        _local_request.reset(scope)


def cancellation_checkpoint() -> None:
    requested = _local_request.get()
    if (requested is not None and requested()) or process_cancellation_requested():
        raise CancellationRequested()
