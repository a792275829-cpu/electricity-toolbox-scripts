"""Reuse immutable workbook inputs during one report parse or pipeline run."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from openpyxl import load_workbook as _load_workbook


_workbooks: ContextVar[dict | None] = ContextVar("report_workbooks", default=None)


def load_workbook(path, **kwargs):
    cache = _workbooks.get()
    if cache is None:
        return _load_workbook(path, **kwargs)
    source = Path(path).expanduser().resolve()
    state = source.stat()
    key = (source, state.st_mtime_ns, state.st_size, tuple(sorted(kwargs.items())))
    if key not in cache:
        cache[key] = _load_workbook(source, **kwargs)
    return cache[key]


def reuse_workbook_inputs(function):
    """Nested parsers share their caller's scope; all handles close on exit."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _workbooks.get() is not None:
            return function(*args, **kwargs)
        cache = {}
        token = _workbooks.set(cache)
        try:
            return function(*args, **kwargs)
        finally:
            _workbooks.reset(token)
            for workbook in cache.values():
                workbook.close()
    return wrapped
