"""Map writable paths for the managed desktop; keep source-mode paths unchanged."""

from __future__ import annotations

import os
from pathlib import Path

# The workspace root that holds 电力工具箱/ and the standalone tool folders.
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def online_energy_output_dir(output_dir: str | Path | None = None) -> Path:
    if output_dir:
        return data_path(Path(output_dir).expanduser())
    return Path.home() / "Downloads" / "上网电量"


def wind_forecast_output_dir(
    output_dir: str | Path | None = None, *, script_dir: Path | None = None,
) -> Path:
    if not output_dir:
        return Path.home() / "Downloads" / "风力预测截图"
    path = Path(output_dir).expanduser()
    if path.is_absolute():
        return path
    root = script_dir if script_dir is not None else WORKSPACE_ROOT / "风力预测截图"
    return data_path(root / path)


def data_path(path: str | Path) -> Path:
    path = Path(path)
    destination = os.environ.get("TOOLBOX_DATA_ROOT")
    if not destination:
        return path
    source = Path(os.environ.get("TOOLBOX_CODE_ROOT", WORKSPACE_ROOT)).resolve()
    try:
        relative = path.resolve().relative_to(source)
    except ValueError:
        # Explicit external workbooks and paths already in data/ stay untouched.
        return path
    return Path(destination).expanduser().resolve() / relative
