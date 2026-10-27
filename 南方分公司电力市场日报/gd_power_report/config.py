from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from toolbox_shared.storage import data_path


@dataclass(frozen=True)
class AppConfig:
    workspace: Path

    @classmethod
    def from_workspace(cls, workspace: str | Path) -> "AppConfig":
        return cls(workspace=Path(workspace).resolve())

    @property
    def outputs_dir(self) -> Path:
        return data_path(self.workspace / "南方分公司电力市场日报" / "输出")

    @property
    def state_dir(self) -> Path:
        return data_path(self.workspace / "南方分公司电力市场日报" / ".state")

    def run_dir(self, analysis_date: date) -> Path:
        return self.outputs_dir / analysis_date.isoformat()
