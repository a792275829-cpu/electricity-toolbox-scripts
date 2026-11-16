"""Match clearing workbooks by complete filename dates."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def clearing_file_date(path: Path) -> date | None:
    patterns = (r"(20\d{2})[.\-_/年](\d{1,2})[.\-_/月](\d{1,2})(?!\d)",
                r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
    for pattern in patterns:
        match = re.search(pattern, path.name)
        if match:
            try:
                return date(*(int(value) for value in match.groups()))
            except ValueError:
                return None
    return None


def find_workbook_for_day(target_day: date, downloads_dir: Path | None = None) -> Path | None:
    downloads = downloads_dir if downloads_dir is not None else Path.home() / "Downloads"
    candidates = [path for path in downloads.glob("*.xlsx")
                  if not path.name.startswith("~$") and "出清情况" in path.name
                  and clearing_file_date(path) == target_day]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
