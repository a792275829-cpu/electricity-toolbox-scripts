from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any

from toolbox_shared.credentials import write_private_json


# Preserve folder preferences written by older Tk versions.
_LEGACY_DIRECTORY_KEYS = {
    "daily-pdf-parser.pdf_path": "daily_pdf_input",
    "daily-pdf-parser.output_path": "daily_pdf_output",
    "group-upload.files": "group_upload_input",
    "market-table.workbook": "market_workbook",
    "report.online_workbook": "report_excel",
    "report.day_ahead_workbook": "report_excel",
    "report.daily_workbook": "report_excel",
    "report.template_report": "report_template",
    "section-analysis.files": "section_analysis_input",
    "trade-analysis.files": "trade_analysis_input",
    "transaction-info.workbook": "transaction_info_workbook",
    **{f"power-benefit.{station}_workbook": f"power_benefit_{index}"
       for index, station in enumerate(("guihu", "tuolian", "haifeng"))},
    **{f"spot-price.{station}_workbook": f"spot_price_{station}"
       for station in ("guihu", "tuolian", "haifeng")},
}


class FileDialogState:
    """Persist local file selections and initial folders."""

    def __init__(self, settings_path: Path) -> None:
        self.settings_path = Path(settings_path)

    @staticmethod
    def fallback_directory() -> Path:
        downloads = Path.home() / "Downloads"
        return downloads if downloads.is_dir() else Path.home()

    def initial_directory(self, key: str, current_path: str | Path | None = None) -> Path:
        settings = self._load()
        directories = self._mapping(settings, "last_directories")
        candidates = [
            self._saved_directory(directories.get(key)),
            self._saved_directory(directories.get(_LEGACY_DIRECTORY_KEYS.get(key, ""))),
            self._directory_for_path(current_path),
            self._saved_directory(settings.get("common_directory")),
            self.fallback_directory(),
        ]
        return next(directory for directory in candidates if directory is not None)

    def last_file(self, key: str, default: str | Path = "", *, save: bool = False) -> str:
        paths = self._selected_paths(key, save=save)
        return paths[0] if paths else str(default)

    def last_files(self, key: str) -> list[str]:
        return self._selected_paths(key)

    def _selected_paths(self, key: str, *, save: bool = False) -> list[str]:
        saved = self._mapping(self._load(), "last_files").get(key)
        if not isinstance(saved, list):
            return []
        paths = []
        for value in saved:
            if isinstance(value, str) and value.strip():
                path = Path(value).expanduser()
                if path.is_file() or (save and not path.exists() and path.parent.is_dir()):
                    paths.append(str(path))
        return paths

    def remember_file(self, key: str, path: str | Path, *, directory_only: bool = False,
                      reset_key: str | None = None) -> None:
        file_path = Path(path).expanduser()
        if directory_only:
            self.remember_directory(key, file_path.parent)
        else:
            self._remember(key, file_path.parent, [str(file_path)], reset_key=reset_key)

    def remember_files(self, key: str, paths: list[str] | list[Path], *,
                       chosen: str | Path | None = None) -> None:
        self._remember(
            key, Path(chosen).expanduser().parent if chosen else None,
            [str(Path(path).expanduser()) for path in paths],
        )

    def remember_directory(self, key: str, path: str | Path) -> None:
        self._remember(key, Path(path).expanduser())

    def _remember(self, key: str, directory: Path | None, paths: list[str] | None = None,
                  *, reset_key: str | None = None) -> None:
        directory = self._saved_directory(directory)
        if directory is None and paths is None:
            return
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.settings_path.with_suffix(".lock")
            with lock_path.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                settings = self._load()
                if directory is not None:
                    self._mapping(settings, "last_directories")[key] = str(directory)
                    settings["common_directory"] = str(directory)
                if paths is not None:
                    selections = self._mapping(settings, "last_files")
                    selections[key] = paths
                    if reset_key is not None:
                        selections.pop(reset_key, None)
                write_private_json(self.settings_path, settings)
        except OSError:
            # Remembering a selection must never prevent the user from choosing one.
            return

    @staticmethod
    def _mapping(settings: dict[str, Any], key: str) -> dict:
        value = settings.get(key)
        if not isinstance(value, dict):
            value = {}
            settings[key] = value
        return value

    @staticmethod
    def _saved_directory(value: object) -> Path | None:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            return None
        path = Path(value).expanduser()
        return path if path.is_dir() else None

    @classmethod
    def _directory_for_path(cls, value: str | Path | None) -> Path | None:
        if value is None or not str(value).strip():
            return None
        path = Path(value).expanduser()
        return cls._saved_directory(path) or cls._saved_directory(path.parent)

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
