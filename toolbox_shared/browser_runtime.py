"""Use the bundled full Chromium for both visible and headless desktop tasks."""
from __future__ import annotations

import os


def chromium_launch_options(*, prefer_system: bool = False) -> dict[str, str]:
    # An unspecified channel selects headless-shell for headless launches.
    # Desktop ships only full Chromium; source launches retain their old policy.
    if os.environ.get("TOOLBOX_PORTABLE") == "1":
        return {"channel": "chromium"}
    return {"channel": "chrome"} if prefer_system else {}
