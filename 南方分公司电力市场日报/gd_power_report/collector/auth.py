"""Use the toolbox login implementation with this report's own browser state."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

WORKSPACE = Path(__file__).resolve().parents[3]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
from toolbox_shared.auth_loader import load_login_module


def load_toolbox_auth_module(workspace: Path, state_dir: Path) -> ModuleType:
    return load_login_module(
        workspace,
        module_name="daily_market_report_auth",
        state_dir=state_dir,
        copy_initial_state=True,
    )
