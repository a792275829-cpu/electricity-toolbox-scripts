"""Publish the two PDF outputs together, restoring previous files on failure."""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from pypdf import PdfReader


@contextmanager
def output_directory_lock(output_dir: Path):
    """Lock one report date without leaving lock files."""
    import fcntl

    descriptor = os.open(output_dir, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("同日期日报正在保存，请稍后重试。") from exc
        yield
    finally:
        os.close(descriptor)


def publish_pdf_pair(output_dir: Path, sources: tuple[Path, Path]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = tuple(output_dir / source.name for source in sources)
    if len(set(targets)) != 2:
        raise ValueError("两份 PDF 的输出文件名必须不同。")
    with output_directory_lock(output_dir):
        staging = Path(tempfile.mkdtemp(prefix=".report-staging-", dir=output_dir))
        backups: dict[Path, Path] = {}
        replaced: list[Path] = []
        preserve_staging = False
        try:
            prepared = []
            for index, (source, target) in enumerate(zip(sources, targets)):
                staged = staging / f"{index}.pdf"
                shutil.copy2(source, staged)
                with staged.open("rb") as stream:
                    if not PdfReader(stream).pages:
                        raise ValueError(f"{source.name} 未包含有效 PDF 页面。")
                prepared.append(staged)
                if target.exists():
                    backup = staging / f"{index}.previous.pdf"
                    shutil.copy2(target, backup)
                    backups[target] = backup
            # Both PDFs and all rollback copies are ready before either is replaced.
            for staged, target in zip(prepared, targets):
                os.replace(staged, target)
                replaced.append(target)
        except Exception as exc:
            for target in reversed(replaced):
                try:
                    if target in backups:
                        os.replace(backups[target], target)
                    else:
                        target.unlink(missing_ok=True)
                except Exception as restore_error:
                    preserve_staging = True
                    exc.add_note(f"恢复 {target.name} 失败；恢复副本保留于 {staging}：{restore_error}")
            raise
        finally:
            if not preserve_staging:
                shutil.rmtree(staging)
        return targets
