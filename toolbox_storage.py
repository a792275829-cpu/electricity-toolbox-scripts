"""Map writable paths for the managed desktop; keep source-mode paths unchanged."""

from __future__ import annotations

import os
from pathlib import Path


def online_energy_output_dir(output_dir: str | Path | None = None) -> Path:
    if output_dir:
        return data_path(Path(output_dir).expanduser())
    return Path.home() / "Downloads" / "上网电量"


def data_path(path: str | Path) -> Path:
    path = Path(path)
    destination = os.environ.get("TOOLBOX_DATA_ROOT")
    if not destination:
        return path
    source = Path(os.environ.get("TOOLBOX_CODE_ROOT", Path(__file__).resolve().parent)).resolve()
    try:
        relative = path.resolve().relative_to(source)
    except ValueError:
        # Explicit external workbooks and paths already in data/ stay untouched.
        return path
    return Path(destination).expanduser().resolve() / relative
