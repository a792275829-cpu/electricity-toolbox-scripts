from __future__ import annotations

import os
import re
import subprocess
import sys
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import ttk


def open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
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
        directories = [path for path in root.iterdir() if path.is_dir()]
        if directories:
            return max(directories, key=lambda path: path.stat().st_mtime)
    return preferred


def clearing_file_date(path: Path) -> date | None:
    patterns = [r"(20\d{2})[.\-_/年](\d{1,2})[.\-_/月](\d{1,2})", r"(20\d{2})(\d{2})(\d{2})"]
    for pattern in patterns:
        match = re.search(pattern, path.name)
        if match:
            try:
                return date(*(int(value) for value in match.groups()))
            except ValueError:
                return None
    return None


def add_path_row(parent: ttk.Frame, *, row: int, label: str, variable: tk.StringVar, command, button_text: str = "浏览") -> ttk.Button:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
    button = ttk.Button(parent, text=button_text, command=command)
    button.grid(row=row, column=2, padx=(8, 0), pady=5)
    parent.columnconfigure(1, weight=1)
    return button
