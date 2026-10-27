"""Publish a prepared file without replacing a concurrently created target."""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
from pathlib import Path


@lru_cache(maxsize=1)
def _rename_exclusive():
    # macOS SDK stdio.h: RENAME_EXCL = 0x00000004 (available since 10.12).
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    return function


def publish_new_file(source: Path, target: Path) -> None:
    """Prefer native exclusive rename; use hard links on supporting volumes.

    Both paths must reside in the same destination directory. Never fall back
    to a check-then-replace sequence, which can overwrite an external save.
    """
    try:
        rename = _rename_exclusive()
        if rename(os.fsencode(source), os.fsencode(target), 0x00000004) == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.ENOTSUP, errno.ENOSYS, errno.EINVAL}:
            raise OSError(code, os.strerror(code), str(target))
    except AttributeError:
        pass
    os.link(source, target)
