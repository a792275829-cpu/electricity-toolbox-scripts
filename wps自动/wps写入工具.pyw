from __future__ import annotations

import runpy
import os
from pathlib import Path


def find_script(base_dir: Path) -> Path | None:
    for path in base_dir.rglob("wps_excel_to_kdocs_gui.py"):
        if path.is_file():
            return path
    return None


script = find_script(Path(__file__).resolve().parent)
if script is None:
    raise SystemExit("Could not find wps_excel_to_kdocs_gui.py")

if os.environ.get("WPS_WRITER_SMOKE") == "1":
    print(f"WPS_WRITER_SCRIPT={script}")
    raise SystemExit(0)

runpy.run_path(str(script), run_name="__main__")
