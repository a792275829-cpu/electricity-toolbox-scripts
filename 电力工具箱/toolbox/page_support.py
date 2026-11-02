from __future__ import annotations

import os
import re
import subprocess
import sys
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import ttk

from .file_rules import clearing_file_date
from toolbox_shared.dates import DATE_INPUT_ERROR, DATE_INPUT_HINT, normalize_date_input, parse_date_input
from toolbox_shared.date_input import DateInput
from toolbox_shared.file_button import FileSelectButton


def open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def default_review_root() -> Path:
    return Path.home() / "Desktop" / "复盘"


def default_review_folder_for_date(today: date, review_root: Path | None = None) -> Path:
    root = review_root or default_review_root()
    target_day = today + timedelta(days=1)
    preferred = root / f"{target_day.month}-{target_day.day}"
    if preferred.is_dir():
        return preferred
    if root.is_dir():
        directories = [path for path in root.iterdir()
                       if path.is_dir() and path.name != "模板" and not path.name.startswith(".")]
        if directories:
            return max(directories, key=lambda path: path.stat().st_mtime)
    return preferred




DATE_ROW_GAP = 20


def add_date_row(parent: ttk.Frame, *, row: int, start_variable: tk.StringVar,
                 end_variable: tk.StringVar, start_label: str = "开始日期",
                 end_label: str = "结束日期", hint: str = DATE_INPUT_HINT) -> ttk.Frame:
    """Single-line 开始日期/结束日期 row aligned to the path entry column.

    The two date fields share one cell so the stretched entry column cannot push
    结束日期 to the far right; the gap stays fixed and compact.
    """
    ttk.Label(parent, text=start_label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
    line = ttk.Frame(parent)
    line.grid(row=row, column=1, sticky="w", pady=3)
    DateInput(line, textvariable=start_variable, width=16).pack(side="left")
    ttk.Label(line, text=end_label).pack(side="left", padx=(DATE_ROW_GAP, 0))
    DateInput(line, textvariable=end_variable, width=16).pack(side="left", padx=(8, 0))
    if hint:
        ttk.Label(line, text=hint, style="Muted.TLabel").pack(side="left", padx=(DATE_ROW_GAP, 0))
    return line


def add_path_row(parent: ttk.Frame, *, row: int, label: str, variable: tk.StringVar, command, button_text: str = "浏览") -> ttk.Button:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
    button = FileSelectButton(parent, text=button_text, command=command)
    button.grid(row=row, column=2, padx=(8, 0), pady=5, sticky="w")
    parent.columnconfigure(1, weight=1)
    return button
