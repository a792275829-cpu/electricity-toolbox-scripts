from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileDialogState:
    """Persist safe, local defaults for the toolbox file dialogs."""

    def __init__(self, settings_path: Path) -> None:
        self.settings_path = Path(settings_path)
        self._settings = self._load()

    @staticmethod
    def fallback_directory() -> Path:
        downloads = Path.home() / "Downloads"
        return downloads if downloads.is_dir() else Path.home()

    def initial_directory(self, key: str, current_path: str | Path | None = None) -> Path:
        candidates = [
            self._saved_directory(self._last_directories().get(key)),
            self._directory_for_path(current_path),
            self._saved_directory(self._settings.get("common_directory")),
            self.fallback_directory(),
        ]
        return next(directory for directory in candidates if directory is not None)

    def remember_file(self, key: str, path: str | Path) -> None:
        self._remember(key, Path(path).expanduser().parent)

    def remember_directory(self, key: str, path: str | Path) -> None:
        self._remember(key, Path(path).expanduser())

    def _remember(self, key: str, directory: Path) -> None:
        directory = self._saved_directory(directory)
        if directory is None:
            return
        self._last_directories()[key] = str(directory)
        self._settings["common_directory"] = str(directory)
        self._save()

    def _last_directories(self) -> dict[str, str]:
        value = self._settings.get("last_directories")
        if not isinstance(value, dict):
            value = {}
            self._settings["last_directories"] = value
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
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)
        except OSError:
            # Remembering a folder must never prevent the user from choosing a file.
            return
