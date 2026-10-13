"""Commit prepared XLSX files with verification and rollback."""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, TypeVar


class PreparedFile(Protocol):
    original: Path
    temporary: Path


T = TypeVar("T", bound=PreparedFile)


def commit_workbooks(
    prepared: tuple[T, ...],
    verify: Callable[[Path, T], None],
    *,
    create_backup: bool,
) -> dict[Path, Path | None]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rollbacks: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    replaced: list[T] = []
    preserve: set[Path] = set()
    try:
        for item in prepared:
            rollback = item.original.with_name(
                f".{item.original.stem}.{uuid.uuid4().hex}.rollback.xlsx"
            )
            rollbacks[item.original] = rollback
            shutil.copy2(item.original, rollback)
            backup = None
            if create_backup:
                backup = item.original.with_name(
                    f"{item.original.stem}.备份-{timestamp}{item.original.suffix}"
                )
                if backup.exists():
                    backup = backup.with_name(f"{backup.stem}-{uuid.uuid4().hex[:8]}{backup.suffix}")
                shutil.copy2(item.original, backup)
            backups[item.original] = backup
        for item in prepared:
            os.replace(item.temporary, item.original)
            # Register immediately: verification can fail after replacement.
            replaced.append(item)
            verify(item.original, item)
    except Exception as error:
        for item in reversed(replaced):
            rollback = rollbacks[item.original]
            try:
                os.replace(rollback, item.original)
            except Exception as restore_error:
                preserve.add(rollback)
                error.add_note(f"回滚失败，原文件副本保留于 {rollback}：{restore_error}")
        raise
    finally:
        for item in prepared:
            item.temporary.unlink(missing_ok=True)
        for rollback in rollbacks.values():
            if rollback not in preserve:
                rollback.unlink(missing_ok=True)
    return backups
